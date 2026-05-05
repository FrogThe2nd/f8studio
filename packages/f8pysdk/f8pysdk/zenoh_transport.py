from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .nats_naming import kv_bucket_for_service
from .runtime_transport import RequestHandler, TransportCallback
from .zenoh_config import apply_zenoh_shared_memory_config
from .zenoh_naming import (
    kv_bucket_to_service_id,
    subject_to_zenoh_key,
    zenoh_key_to_kv_key,
    zenoh_key_to_subject,
    zenoh_kv_key,
    zenoh_kv_pattern,
    zenoh_service_liveliness_key,
)

log = logging.getLogger(__name__)
# Zenoh can return a subscriber declaration before local matching is fully
# visible to other sessions. This small setup-only pause prevents losing the
# first state/data sample immediately after a watch is installed.
_SUBSCRIPTION_SETTLE_S = 0.01


def _is_zenoh_channel_drained(exc: BaseException) -> bool:
    return "channel is empty and closed" in str(exc).strip().lower()


@dataclass(frozen=True)
class ZenohTransportConfig:
    service_id: str
    config_path: str | None = None
    connect: tuple[str, ...] = ()
    listen: tuple[str, ...] = ()
    shm_pool_bytes: int = 256 * 1024 * 1024


class _ZenohSubscriptionHandle:
    def __init__(self, declaration: Any, task: asyncio.Task[None]) -> None:
        self._declaration = declaration
        self._task = task

    async def unsubscribe(self) -> None:
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
    memory and exposes it through Zenoh queryables. Writes are also published as
    update samples so remote watchers do not need Zenoh storage-manager support.
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
        self._queryables: list[_ZenohServeHandle] = []
        self._kv: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

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
                    "Install repo dependencies or choose --bus-backend nats."
                ) from exc

            config = self._build_config(zenoh)
            self._session = await asyncio.to_thread(zenoh.open, config)
            self._liveliness_token = self._session.liveliness().declare_token(
                zenoh_service_liveliness_key(self._config.service_id)
            )
            await self._start_kv_queryables()

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
        return config

    async def close(self) -> None:
        async with self._lock:
            handles = [*self._subs, *self._serve_handles, *self._queryables]
            self._subs.clear()
            self._serve_handles.clear()
            self._queryables.clear()
            for handle in handles:
                await handle.unsubscribe()

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
        session = await self._require_session()
        key = subject_to_zenoh_key(subject)
        await asyncio.to_thread(_put_realtime_drop, session, key, bytes(payload))

    async def subscribe(
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
        session = await self._require_session()
        key = subject_to_zenoh_key(subject)
        try:
            replies = await asyncio.to_thread(session.get, key, payload=bytes(payload), timeout=float(timeout))
            reply = await self._recv_reply(replies, timeout_s=float(timeout))
        except Exception as exc:
            if raise_on_error:
                raise
            log.debug("zenoh request failed key=%s", key, exc_info=exc)
            return None
        if reply is None:
            if raise_on_error:
                raise TimeoutError(f"zenoh request timed out key={key!r}")
            return None
        sample = reply.ok
        if sample is None:
            if raise_on_error:
                raise RuntimeError(f"zenoh request returned error key={key!r}")
            return None
        return bytes(sample.payload)

    async def serve(self, subject: str, handler: RequestHandler) -> _ZenohServeHandle:
        session = await self._require_session()
        key_expr = subject_to_zenoh_key(subject)
        declaration = await asyncio.to_thread(session.declare_queryable, key_expr)
        task = asyncio.create_task(
            self._pump_queryable(declaration, key_expr=key_expr, handler=handler, reply_key=key_expr),
            name=f"zenoh_serve:{key_expr}",
        )
        handle = _ZenohServeHandle(declaration, task)
        self._serve_handles.append(handle)
        await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        return handle

    async def kv_put(self, key: str, value: bytes) -> None:
        key_s = str(key or "").strip()
        if not key_s:
            raise ValueError("key must be non-empty")
        raw = bytes(value)
        self._kv[key_s] = raw
        session = await self._require_session()
        zenoh_key = zenoh_kv_key(self._config.service_id, key_s)
        await asyncio.to_thread(_put_realtime_drop, session, zenoh_key, raw)

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
        session = await self._require_session()
        peer_service_id = kv_bucket_to_service_id(bucket)
        key_expr = zenoh_kv_pattern(peer_service_id, key_pattern)
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
        peer_service_id = kv_bucket_to_service_id(bucket)
        key_s = str(key or "").strip()
        if not key_s:
            raise ValueError("key must be non-empty")
        if peer_service_id == self._config.service_id:
            return self._kv.get(key_s)
        session = await self._require_session()
        selector = zenoh_kv_key(peer_service_id, key_s)
        timeout_s = 1.0 if timeout is None else max(0.001, float(timeout))
        try:
            replies = await asyncio.to_thread(session.get, selector, timeout=timeout_s)
            reply = await self._recv_reply(replies, timeout_s=timeout_s)
        except Exception as exc:
            log.debug("zenoh kv get failed bucket=%s key=%s", bucket, key_s, exc_info=exc)
            return None
        if reply is None:
            return None
        sample = reply.ok
        if sample is None:
            return None
        return bytes(sample.payload)

    async def _require_session(self) -> Any:
        if self._session is None:
            await self.connect()
        if self._session is None:
            raise RuntimeError("Zenoh not connected")
        return self._session

    async def _start_kv_queryables(self) -> None:
        state_expr = f"f8/svc/{self._config.service_id}/state/**"
        kv_expr = f"f8/svc/{self._config.service_id}/kv/**"
        self._queryables.append(await self._serve_kv_queryable(state_expr))
        self._queryables.append(await self._serve_kv_queryable(kv_expr))

    async def _serve_kv_queryable(self, key_expr: str) -> _ZenohServeHandle:
        session = await self._require_session()
        declaration = await asyncio.to_thread(session.declare_queryable, key_expr)
        task = asyncio.create_task(
            self._pump_kv_queryable(declaration, key_expr=key_expr),
            name=f"zenoh_kv_queryable:{key_expr}",
        )
        await asyncio.sleep(_SUBSCRIPTION_SETTLE_S)
        return _ZenohServeHandle(declaration, task)

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

    async def _pump_queryable(
        self,
        declaration: Any,
        *,
        key_expr: str,
        handler: RequestHandler,
        reply_key: str,
    ) -> None:
        while True:
            try:
                query = declaration.try_recv()
                if query is None:
                    await asyncio.sleep(0.001)
                    continue
                response = await handler(bytes(query.payload))
                if response is None:
                    query.reply_err(b"empty response")
                else:
                    query.reply(reply_key, bytes(response))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("zenoh queryable pump failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.05)

    async def _pump_kv_queryable(self, declaration: Any, *, key_expr: str) -> None:
        while True:
            try:
                query = declaration.try_recv()
                if query is None:
                    await asyncio.sleep(0.001)
                    continue
                kv_key = zenoh_key_to_kv_key(str(query.key_expr))
                if kv_key is None:
                    query.reply_err(b"invalid key")
                    continue
                value = self._kv.get(kv_key)
                if value is None:
                    query.reply_err(b"not found")
                    continue
                query.reply(str(query.key_expr), value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("zenoh kv queryable pump failed key_expr=%s", key_expr, exc_info=exc)
                await asyncio.sleep(0.05)

    async def _recv_reply(self, replies: Any, *, timeout_s: float) -> Any | None:
        deadline = time.monotonic() + max(0.001, float(timeout_s))
        while time.monotonic() < deadline:
            try:
                reply = replies.try_recv()
            except Exception as exc:
                if _is_zenoh_channel_drained(exc):
                    return None
                raise
            if reply is not None:
                return reply
            await asyncio.sleep(0.001)
        return None


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


__all__ = ["ZenohTransport", "ZenohTransportConfig"]
