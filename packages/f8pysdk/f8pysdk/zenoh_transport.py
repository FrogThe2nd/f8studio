from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from .codec import decode_obj, encode_obj
from .f8_naming import kv_bucket_for_service, new_id
from .runtime_transport import RequestHandler, TransportCallback
from .time_utils import now_ms
from .zenoh_config import apply_zenoh_shared_memory_config, apply_zenoh_timestamping_config
from .zenoh_naming import (
    kv_bucket_to_service_id,
    subject_to_zenoh_command_key,
    subject_to_zenoh_key,
    zenoh_key_to_kv_key,
    zenoh_key_to_subject,
    zenoh_kv_key,
    zenoh_kv_pattern,
    zenoh_reply_key,
    zenoh_reply_pattern,
    zenoh_service_liveliness_key,
)

log = logging.getLogger(__name__)
# Zenoh can return a subscriber declaration before local matching is fully
# visible to other sessions. This small setup-only pause prevents losing the
# first state/data sample immediately after a watch is installed.
_SUBSCRIPTION_SETTLE_S = 0.01


@dataclass(frozen=True)
class ZenohTransportConfig:
    service_id: str
    config_path: str | None = None
    connect: tuple[str, ...] = ()
    listen: tuple[str, ...] = ()
    shm_pool_bytes: int = 256 * 1024 * 1024


StreamDeliveryPolicy = Literal["latest", "fifo", "reliable"]


@dataclass(frozen=True)
class _ZenohCommandEnvelope:
    req_id: str
    actor: str
    ts_ms: int
    payload: bytes
    reply_key: str | None


@dataclass(frozen=True)
class _ZenohCommandReply:
    req_id: str
    ok: bool
    payload: bytes
    error: str


class _ZenohSubscriptionHandle:
    def __init__(self, declaration: Any, task: asyncio.Task[None]) -> None:
        self._declaration = declaration
        self._task = task
        self._closed = False

    async def unsubscribe(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._task
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            self._declaration.undeclare()
        except Exception as exc:
            log.debug("zenoh undeclare subscription failed", exc_info=exc)

    async def stop(self) -> None:
        await self.unsubscribe()


class _ZenohServeHandle(_ZenohSubscriptionHandle):
    pass


class ZenohTransport:
    """
    Zenoh runtime transport.

    State is service-owned: this transport keeps the latest local KV snapshot in
    memory and publishes retained state samples through Zenoh advanced
    publisher cache/history. Remote watchers receive the current value on
    subscribe without a hot-path queryable or storage-manager dependency.
    """

    def __init__(self, config: ZenohTransportConfig) -> None:
        service_id = str(config.service_id or "").strip()
        self._config = ZenohTransportConfig(
            service_id=service_id,
            config_path=str(config.config_path).strip() if config.config_path else None,
            connect=tuple(str(item).strip() for item in config.connect if str(item).strip()),
            listen=tuple(str(item).strip() for item in config.listen if str(item).strip()),
            shm_pool_bytes=max(0, int(config.shm_pool_bytes)),
        )
        self._kv_bucket = kv_bucket_for_service(service_id)
        self._session: Any | None = None
        self._liveliness_token: Any | None = None
        self._subs: list[_ZenohSubscriptionHandle] = []
        self._serve_handles: list[_ZenohServeHandle] = []
        self._kv: dict[str, bytes] = {}
        self._state_publishers: dict[str, Any] = {}
        self._pending_replies: dict[str, asyncio.Future[_ZenohCommandReply]] = {}
        self._reply_subscriber: _ZenohSubscriptionHandle | None = None
        self._lock = asyncio.Lock()
        self._reply_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def kv_bucket(self) -> str:
        return self._kv_bucket

    async def connect(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            try:
                import zenoh  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "Zenoh backend requires the `eclipse-zenoh` Python package. "
                    "Install repo dependencies or use --bus-backend mem for local tests."
                ) from exc

            config = self._build_config(zenoh)
            self._session = await asyncio.to_thread(zenoh.open, config)
            self._liveliness_token = self._session.liveliness().declare_token(
                zenoh_service_liveliness_key(self._config.service_id)
            )

    def _build_config(self, zenoh_module: Any) -> Any:
        path = self._config.config_path
        if path:
            config = zenoh_module.Config.from_file(path)
        else:
            config = zenoh_module.Config()
        if self._config.connect:
            config.insert_json5("connect/endpoints", json.dumps(list(self._config.connect)))
        if self._config.listen:
            config.insert_json5("listen/endpoints", json.dumps(list(self._config.listen)))
        apply_zenoh_shared_memory_config(
            config,
            zenoh_module=zenoh_module,
            shm_pool_bytes=self._config.shm_pool_bytes,
            log_context=f"runtime:{self._config.service_id}",
        )
        apply_zenoh_timestamping_config(
            config,
            zenoh_module=zenoh_module,
            log_context=f"runtime:{self._config.service_id}",
        )
        return config

    async def close(self) -> None:
        async with self._lock:
            handles = [*self._subs, *self._serve_handles]
            if self._reply_subscriber is not None:
                handles.append(self._reply_subscriber)
                self._reply_subscriber = None
            self._subs.clear()
            self._serve_handles.clear()
            for handle in handles:
                await handle.unsubscribe()

            for publisher in list(self._state_publishers.values()):
                try:
                    publisher.undeclare()
                except Exception as exc:
                    log.debug("zenoh retained state publisher undeclare failed", exc_info=exc)
            self._state_publishers.clear()

            for req_id, future in list(self._pending_replies.items()):
                if not future.done():
                    future.cancel()
                self._pending_replies.pop(req_id, None)

            token = self._liveliness_token
            self._liveliness_token = None
            if token is not None:
                try:
                    token.undeclare()
                except Exception as exc:
                    log.debug("zenoh liveliness undeclare failed", exc_info=exc)

            session = self._session
            self._session = None
            if session is not None:
                try:
                    await asyncio.to_thread(session.close)
                except Exception as exc:
                    log.debug("zenoh session close failed", exc_info=exc)

    async def publish(self, subject: str, payload: bytes) -> None:
        await self.publish_stream(subject, payload, delivery="latest")

    async def publish_stream(
        self,
        subject: str,
        payload: bytes,
        *,
        delivery: StreamDeliveryPolicy = "latest",
    ) -> None:
        session = await self._require_session()
        key = subject_to_zenoh_key(subject)
        if delivery == "latest":
            await asyncio.to_thread(_put_realtime_drop, session, key, bytes(payload))
            return
        if delivery == "fifo":
            await asyncio.to_thread(_put_data_fifo, session, key, bytes(payload))
            return
        if delivery == "reliable":
            await asyncio.to_thread(_put_reliable_control, session, key, bytes(payload))
            return
        raise ValueError(f"unsupported stream delivery policy: {delivery!r}")

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: TransportCallback | None = None,
    ) -> _ZenohSubscriptionHandle:
        return await self.subscribe_stream(subject, queue=queue, cb=cb)

    async def subscribe_stream(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: TransportCallback | None = None,
    ) -> _ZenohSubscriptionHandle:
        del queue
        session = await self._require_session()
        key_expr = subject_to_zenoh_key(subject)
        declaration = await asyncio.to_thread(session.declare_subscriber, key_expr)
        task = asyncio.create_task(
            self._pump_subscriber(declaration, key_expr=key_expr, cb=cb, key_converter=zenoh_key_to_subject),
            name=f"zenoh_sub:{key_expr}",
        )
        handle = _ZenohSubscriptionHandle(declaration, task)
        self._subs.append(handle)
        await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        return handle

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        try:
            return await self.send_command(subject, payload, timeout=timeout, raise_on_error=True)
        except Exception as exc:
            if raise_on_error:
                raise
            log.debug("zenoh command request failed subject=%s", subject, exc_info=exc)
            return None

    async def serve(self, subject: str, handler: RequestHandler) -> _ZenohServeHandle:
        return await self.serve_command(subject, handler)

    async def send_command(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        await self._ensure_reply_subscriber()
        session = await self._require_session()
        req_id = new_id()
        reply_key = zenoh_reply_key(self._config.service_id, req_id)
        command_key = subject_to_zenoh_command_key(subject)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_ZenohCommandReply] = loop.create_future()
        self._pending_replies[req_id] = future
        envelope = _encode_command_envelope(
            _ZenohCommandEnvelope(
                req_id=req_id,
                actor=self._config.service_id,
                ts_ms=now_ms(),
                payload=bytes(payload),
                reply_key=reply_key,
            )
        )
        try:
            await asyncio.to_thread(_put_reliable_control, session, command_key, envelope)
            reply = await asyncio.wait_for(future, timeout=max(0.001, float(timeout)))
        except asyncio.TimeoutError:
            if raise_on_error:
                raise TimeoutError(f"zenoh command request timed out subject={subject!r}") from None
            return None
        finally:
            self._pending_replies.pop(req_id, None)

        if not reply.ok:
            if raise_on_error:
                raise RuntimeError(reply.error or f"zenoh command request failed subject={subject!r}")
            return None
        return reply.payload

    async def serve_command(self, subject: str, handler: RequestHandler) -> _ZenohServeHandle:
        session = await self._require_session()
        key_expr = subject_to_zenoh_command_key(subject)
        declaration = await asyncio.to_thread(session.declare_subscriber, key_expr)
        task = asyncio.create_task(
            self._pump_command_subscriber(declaration, key_expr=key_expr, handler=handler),
            name=f"zenoh_cmd_serve:{key_expr}",
        )
        handle = _ZenohServeHandle(declaration, task)
        self._serve_handles.append(handle)
        await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        return handle

    async def kv_put(self, key: str, value: bytes) -> None:
        await self.publish_state(key, value)

    async def publish_state(self, key: str, value: bytes) -> None:
        key_s = str(key or "").strip()
        if not key_s:
            raise ValueError("key must be non-empty")
        raw = bytes(value)
        self._kv[key_s] = raw
        session = await self._require_session()
        zenoh_key = zenoh_kv_key(self._config.service_id, key_s)
        publisher = self._state_publishers.get(zenoh_key)
        if publisher is None:
            publisher = await asyncio.to_thread(_declare_retained_state_publisher, session, zenoh_key)
            self._state_publishers[zenoh_key] = publisher
            await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        await asyncio.to_thread(publisher.put, raw)

    async def kv_get(self, key: str) -> bytes | None:
        return self._kv.get(str(key or "").strip())

    async def kv_watch(self, key_pattern: str, *, cb: TransportCallback) -> _ZenohSubscriptionHandle:
        return await self.kv_watch_in_bucket(self._kv_bucket, key_pattern, cb=cb)

    async def kv_watch_in_bucket(
        self,
        bucket: str,
        key_pattern: str,
        *,
        cb: TransportCallback,
    ) -> _ZenohSubscriptionHandle:
        return await self.subscribe_state_in_bucket(bucket, key_pattern, cb=cb, with_initial=True)

    async def subscribe_state_in_bucket(
        self,
        bucket: str,
        key_pattern: str,
        *,
        cb: TransportCallback,
        with_initial: bool = True,
    ) -> _ZenohSubscriptionHandle:
        session = await self._require_session()
        peer_service_id = kv_bucket_to_service_id(bucket)
        key_expr = zenoh_kv_pattern(peer_service_id, key_pattern)
        if with_initial:
            declaration = await asyncio.to_thread(_declare_retained_state_subscriber, session, key_expr)
        else:
            declaration = await asyncio.to_thread(session.declare_subscriber, key_expr)

        async def _on_sample(key: str, payload: bytes) -> None:
            kv_key = zenoh_key_to_kv_key(key)
            if kv_key is None:
                return
            await cb(kv_key, payload)

        task = asyncio.create_task(
            self._pump_subscriber(declaration, key_expr=key_expr, cb=_on_sample, key_converter=lambda item: item),
            name=f"zenoh_kv_watch:{bucket}:{key_pattern}",
        )
        handle = _ZenohSubscriptionHandle(declaration, task)
        self._subs.append(handle)
        await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        return handle

    async def kv_get_in_bucket(self, bucket: str, key: str, *, timeout: float | None = None) -> bytes | None:
        del timeout
        peer_service_id = kv_bucket_to_service_id(bucket)
        key_s = str(key or "").strip()
        if not key_s:
            raise ValueError("key must be non-empty")
        if peer_service_id == self._config.service_id:
            return self._kv.get(key_s)
        return None

    async def _ensure_reply_subscriber(self) -> None:
        async with self._reply_lock:
            if self._reply_subscriber is not None:
                return
            session = await self._require_session()
            key_expr = zenoh_reply_pattern(self._config.service_id)
            declaration = await asyncio.to_thread(session.declare_subscriber, key_expr)
            task = asyncio.create_task(
                self._pump_reply_subscriber(declaration, key_expr=key_expr),
                name=f"zenoh_reply:{key_expr}",
            )
            self._reply_subscriber = _ZenohSubscriptionHandle(declaration, task)
            await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)

    async def _require_session(self) -> Any:
        if self._session is None:
            await self.connect()
        if self._session is None:
            raise RuntimeError("Zenoh not connected")
        return self._session

    async def _pump_subscriber(
        self,
        declaration: Any,
        *,
        key_expr: str,
        cb: TransportCallback | None,
        key_converter: Callable[[str], str],
    ) -> None:
        while True:
            try:
                sample = declaration.try_recv()
                if sample is None:
                    await asyncio.sleep(0.001)
                    continue
                if cb is not None:
                    await cb(key_converter(str(sample.key_expr)), bytes(sample.payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("zenoh subscriber pump failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.05)

    async def _pump_reply_subscriber(self, declaration: Any, *, key_expr: str) -> None:
        while True:
            try:
                sample = declaration.try_recv()
                if sample is None:
                    await asyncio.sleep(0.001)
                    continue
                reply = _decode_command_reply(bytes(sample.payload))
                future = self._pending_replies.get(reply.req_id)
                if future is not None and not future.done():
                    future.set_result(reply)
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                log.error("zenoh command reply decode failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.01)
            except Exception as exc:
                log.error("zenoh command reply pump failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.05)

    async def _pump_command_subscriber(self, declaration: Any, *, key_expr: str, handler: RequestHandler) -> None:
        while True:
            try:
                sample = declaration.try_recv()
                if sample is None:
                    await asyncio.sleep(0.001)
                    continue
                envelope = _decode_command_envelope(bytes(sample.payload))
                try:
                    response = await handler(envelope.payload)
                except Exception as exc:
                    log.error("zenoh command handler failed key_expr=%s req_id=%s", key_expr, envelope.req_id, exc_info=exc)
                    await self._publish_command_reply(
                        envelope.reply_key,
                        _ZenohCommandReply(
                            req_id=envelope.req_id,
                            ok=False,
                            payload=b"",
                            error="command handler failed",
                        ),
                    )
                    continue
                if response is None:
                    await self._publish_command_reply(
                        envelope.reply_key,
                        _ZenohCommandReply(
                            req_id=envelope.req_id,
                            ok=False,
                            payload=b"",
                            error="empty response",
                        ),
                    )
                    continue
                await self._publish_command_reply(
                    envelope.reply_key,
                    _ZenohCommandReply(
                        req_id=envelope.req_id,
                        ok=True,
                        payload=bytes(response),
                        error="",
                    ),
                )
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                log.error("zenoh command envelope decode failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.01)
            except Exception as exc:
                log.error("zenoh command subscriber pump failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.05)

    async def _publish_command_reply(self, reply_key: str | None, reply: _ZenohCommandReply) -> None:
        if not reply_key:
            return
        session = await self._require_session()
        await asyncio.to_thread(_put_reliable_control, session, reply_key, _encode_command_reply(reply))

def _put_realtime_drop(session: Any, key: str, payload: bytes) -> None:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh backend requires the `eclipse-zenoh` Python package") from exc
    session.put(
        key,
        bytes(payload),
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.REAL_TIME,
        express=True,
    )


def _put_data_fifo(session: Any, key: str, payload: bytes) -> None:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh backend requires the `eclipse-zenoh` Python package") from exc
    session.put(
        key,
        bytes(payload),
        congestion_control=zenoh.CongestionControl.BLOCK_FIRST,
        priority=zenoh.Priority.DATA,
        express=False,
    )


def _put_reliable_control(session: Any, key: str, payload: bytes) -> None:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh backend requires the `eclipse-zenoh` Python package") from exc
    session.put(
        key,
        bytes(payload),
        congestion_control=zenoh.CongestionControl.BLOCK,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        express=True,
    )


def _declare_retained_state_publisher(session: Any, key: str) -> Any:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh backend requires the `eclipse-zenoh` Python package") from exc
    return zenoh.ext.declare_advanced_publisher(
        session,
        key,
        congestion_control=zenoh.CongestionControl.BLOCK,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
        express=True,
        reliability=zenoh.Reliability.RELIABLE,
        cache=zenoh.ext.CacheConfig(max_samples=1),
        publisher_detection=True,
    )


def _declare_retained_state_subscriber(session: Any, key_expr: str) -> Any:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh backend requires the `eclipse-zenoh` Python package") from exc
    return zenoh.ext.declare_advanced_subscriber(
        session,
        key_expr,
        history=zenoh.ext.HistoryConfig(detect_late_publishers=True, max_samples=1),
    )


def _encode_command_envelope(envelope: _ZenohCommandEnvelope) -> bytes:
    return encode_obj(
        {
            "v": 1,
            "reqId": envelope.req_id,
            "actor": envelope.actor,
            "tsMs": int(envelope.ts_ms),
            "payload": envelope.payload,
            "replyKey": envelope.reply_key or "",
        }
    )


def _decode_command_envelope(raw: bytes) -> _ZenohCommandEnvelope:
    payload = decode_obj(raw)
    req_id = str(payload.get("reqId") or "").strip()
    if not req_id:
        raise ValueError("command envelope missing reqId")
    raw_payload = payload.get("payload")
    if not isinstance(raw_payload, bytes):
        raise ValueError("command envelope payload must be bytes")
    raw_reply_key = payload.get("replyKey")
    reply_key = str(raw_reply_key).strip() if raw_reply_key else None
    raw_ts = payload.get("tsMs")
    try:
        ts_ms = int(raw_ts or 0)
    except (TypeError, ValueError):
        ts_ms = 0
    return _ZenohCommandEnvelope(
        req_id=req_id,
        actor=str(payload.get("actor") or "").strip(),
        ts_ms=ts_ms,
        payload=raw_payload,
        reply_key=reply_key,
    )


def _encode_command_reply(reply: _ZenohCommandReply) -> bytes:
    return encode_obj(
        {
            "v": 1,
            "reqId": reply.req_id,
            "ok": bool(reply.ok),
            "payload": reply.payload,
            "error": reply.error,
        }
    )


def _decode_command_reply(raw: bytes) -> _ZenohCommandReply:
    payload = decode_obj(raw)
    req_id = str(payload.get("reqId") or "").strip()
    if not req_id:
        raise ValueError("command reply missing reqId")
    raw_payload = payload.get("payload")
    if raw_payload is None:
        data = b""
    elif isinstance(raw_payload, bytes):
        data = raw_payload
    else:
        raise ValueError("command reply payload must be bytes")
    return _ZenohCommandReply(
        req_id=req_id,
        ok=bool(payload.get("ok")),
        payload=data,
        error=str(payload.get("error") or ""),
    )


__all__ = ["StreamDeliveryPolicy", "ZenohTransport", "ZenohTransportConfig"]
