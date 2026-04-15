from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import nats  # type: ignore[import-not-found]
from nats.errors import BadSubscriptionError  # type: ignore[import-not-found]
from nats.errors import TimeoutError as NatsTimeoutError  # type: ignore[import-not-found]
from nats.js.api import KeyValueConfig, StorageType  # type: ignore[import-not-found]
from nats.js.errors import BucketNotFoundError, NotFoundError as JsNotFoundError  # type: ignore[import-not-found]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NatsTransportConfig:
    url: str
    kv_bucket: str
    kv_history: int = 1
    kv_storage: StorageType = StorageType.MEMORY
    delete_bucket_on_connect: bool = False
    delete_bucket_on_close: bool = False


class NatsTransport:
    """
    Single-process transport for NATS core pub/sub + JetStream KV.

    Intended to be shared by all RuntimeNode instances in a service process.
    """

    def __init__(self, config: NatsTransportConfig) -> None:
        self._config = config
        self._nc: Any = None
        self._js: Any = None
        self._kv: Any = None
        self._kv_stores: dict[str, Any] = {}
        self._subs: list[Any] = []
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._nc is not None

    @property
    def raw_client(self) -> Any | None:
        return self._nc

    async def require_client(self) -> Any:
        if self._nc is None:
            await self.connect()
        if self._nc is None:
            raise RuntimeError("NATS not connected")
        return self._nc

    async def connect(self) -> None:
        async with self._lock:
            if self._nc is not None:
                return
            url = str(self._config.url or "nats://127.0.0.1:4222").strip()
            last_log = 0.0
            attempt = 0
            last_err_log = 0.0

            async def _error_cb(exc: Exception) -> None:
                nonlocal last_err_log
                now = time.monotonic()
                if (now - last_err_log) < 2.0:
                    return
                last_err_log = now
                log.warning(
                    "NATS connection error (will retry): %s: %s",
                    type(exc).__name__,
                    exc,
                )

            while self._nc is None:
                attempt += 1
                try:
                    self._nc = await nats.connect(
                        servers=[url],
                        connect_timeout=2,
                        reconnect_time_wait=0.5,
                        max_reconnect_attempts=-1,
                        error_cb=_error_cb,
                    )
                except Exception as exc:
                    now = time.monotonic()
                    if attempt == 1 or (now - last_log) >= 2.0:
                        last_log = now
                        log.warning(
                            "NATS server is not reachable at %r. Start `nats-server` or set "
                            "`F8_NATS_URL`. retrying... (%s)",
                            url,
                            type(exc).__name__,
                        )
                    await asyncio.sleep(min(2.0, 0.2 * attempt))
                    continue
            self._js = self._nc.jetstream()
            if bool(self._config.delete_bucket_on_connect) and self._js is not None:
                try:
                    await self._js.delete_key_value(str(self._config.kv_bucket))
                except (BucketNotFoundError, JsNotFoundError) as exc:
                    log.debug(
                        "delete_key_value skipped during connect bucket=%s reason=%s",
                        self._config.kv_bucket,
                        type(exc).__name__,
                    )
                except Exception as exc:
                    log.debug("delete_key_value failed during connect bucket=%s", self._config.kv_bucket, exc_info=exc)
            self._kv = await self._open_kv(self._config.kv_bucket)
            if self._kv is not None:
                self._kv_stores[self._config.kv_bucket] = self._kv

    async def _open_kv(self, bucket: str) -> Any:
        if self._js is None:
            raise RuntimeError("JetStream not initialized")
        try:
            return await self._js.key_value(str(bucket))
        except (BucketNotFoundError, JsNotFoundError):
            cfg = KeyValueConfig(
                bucket=str(bucket),
                history=int(self._config.kv_history),
                storage=self._config.kv_storage,
            )
            return await self._js.create_key_value(config=cfg)

    async def close(self) -> None:
        async with self._lock:
            subs = list(self._subs)
            self._subs.clear()
            for sub in subs:
                try:
                    await sub.unsubscribe()
                except BadSubscriptionError:
                    log.debug("skip unsubscribe for invalid subscription during close")
                except Exception as exc:
                    log.debug("unsubscribe failed during close", exc_info=exc)

            if bool(self._config.delete_bucket_on_close) and self._js is not None:
                try:
                    await self._js.delete_key_value(str(self._config.kv_bucket))
                except (BucketNotFoundError, JsNotFoundError) as exc:
                    log.debug(
                        "delete_key_value skipped during close bucket=%s reason=%s",
                        self._config.kv_bucket,
                        type(exc).__name__,
                    )
                except Exception as exc:
                    log.debug("delete_key_value failed during close bucket=%s", self._config.kv_bucket, exc_info=exc)

            if self._nc is not None:
                try:
                    await self._nc.drain()
                except Exception as exc:
                    log.debug("nats drain failed during close", exc_info=exc)
            self._nc = None
            self._js = None
            self._kv = None
            self._kv_stores.clear()

    async def publish(self, subject: str, payload: bytes) -> None:
        if self._nc is None:
            await self.connect()
        if self._nc is None:
            return
        await self._nc.publish(str(subject), bytes(payload))

    async def request(
        self, subject: str, payload: bytes, *, timeout: float = 1.0, raise_on_error: bool = False
    ) -> bytes | None:
        if self._nc is None:
            await self.connect()
        if self._nc is None:
            return None
        try:
            msg = await self._nc.request(str(subject), bytes(payload), timeout=float(timeout))
        except Exception:
            if raise_on_error:
                raise
            return None
        try:
            return bytes(msg.data or b"")  # type: ignore[attr-defined]
        except Exception:
            return None

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> Any:
        if self._nc is None:
            await self.connect()
        if self._nc is None:
            raise RuntimeError("NATS not connected")

        async def _handler(msg: Any) -> None:
            if cb is None:
                return
            try:
                await cb(str(msg.subject or subject), bytes(msg.data or b""))  # type: ignore[attr-defined]
            except Exception as exc:
                log.error("subscriber callback failed subject=%s", subject, exc_info=exc)

        sub = await self._nc.subscribe(str(subject), queue=str(queue) if queue else None, cb=_handler)
        self._subs.append(sub)
        return sub

    async def subscribe_msg(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[Any], Awaitable[None]] | None = None,
    ) -> Any:
        if self._nc is None:
            await self.connect()
        if self._nc is None:
            raise RuntimeError("NATS not connected")

        async def _handler(msg: Any) -> None:
            if cb is None:
                return
            try:
                await cb(msg)
            except Exception as exc:
                log.error("subscriber raw callback failed subject=%s", subject, exc_info=exc)

        sub = await self._nc.subscribe(str(subject), queue=str(queue) if queue else None, cb=_handler)
        self._subs.append(sub)
        return sub

    async def kv_put(self, key: str, value: bytes) -> None:
        if self._kv is None:
            await self.connect()
        if self._kv is None:
            return
        await self._kv.put(str(key), bytes(value))

    async def kv_get(self, key: str) -> bytes | None:
        if self._kv is None:
            await self.connect()
        if self._kv is None:
            return None
        try:
            entry = await self._kv.get(str(key))
        except Exception:
            return None
        if entry is None:
            return None
        try:
            return bytes(entry.value or b"")  # type: ignore[attr-defined]
        except Exception:
            return None

    async def kv_watch(self, key_pattern: str, *, cb: Callable[[str, bytes], Awaitable[None]]) -> Any:
        if self._kv is None:
            await self.connect()
        if self._kv is None:
            raise RuntimeError("KV not available")

        watcher = await self._kv.watch(str(key_pattern))

        async def _pump() -> None:
            update_error_logged = False
            while True:
                try:
                    entry = await watcher.updates(timeout=0.5)
                except asyncio.CancelledError:
                    break
                except (NatsTimeoutError, asyncio.TimeoutError, TimeoutError):
                    continue
                except Exception as exc:
                    if not update_error_logged:
                        update_error_logged = True
                        log.error("kv watch updates failed key_pattern=%s", key_pattern, exc_info=exc)
                    await asyncio.sleep(0.05)
                    continue
                update_error_logged = False
                if entry is None:
                    continue
                try:
                    key = str(entry.key or "")  # type: ignore[attr-defined]
                    value = bytes(entry.value or b"")  # type: ignore[attr-defined]
                except Exception as exc:
                    log.error("kv watch entry parse failed key_pattern=%s", key_pattern, exc_info=exc)
                    continue
                if not key:
                    continue
                try:
                    await cb(key, value)
                except Exception as exc:
                    log.error("kv watch callback failed key_pattern=%s key=%s", key_pattern, key, exc_info=exc)

        task = asyncio.create_task(_pump(), name=f"kv_watch:{key_pattern}")
        return (watcher, task)

    async def kv_store(self, bucket: str) -> Any:
        bucket_s = str(bucket or "").strip()
        if not bucket_s:
            raise ValueError("bucket must be non-empty")
        if self._js is None:
            await self.connect()
        if self._js is None:
            raise RuntimeError("JetStream not initialized")
        cached = self._kv_stores.get(bucket_s)
        if cached is not None:
            return cached
        store = await self._open_kv(bucket_s)
        self._kv_stores[bucket_s] = store
        return store

    async def kv_watch_in_bucket(
        self,
        bucket: str,
        key_pattern: str,
        *,
        cb: Callable[[str, bytes], Awaitable[None]],
    ) -> Any:
        bucket_s = str(bucket or "").strip()
        pattern_s = str(key_pattern or "").strip()
        if not bucket_s:
            raise ValueError("bucket must be non-empty")
        if not pattern_s:
            raise ValueError("key_pattern must be non-empty")

        class _ManagedWatcher:
            def __init__(self) -> None:
                self._watcher: Any | None = None

            def set_watcher(self, watcher: Any | None) -> None:
                self._watcher = watcher

            async def stop(self) -> None:
                watcher = self._watcher
                self._watcher = None
                if watcher is None:
                    return
                stop_fn = getattr(watcher, "stop", None)
                if callable(stop_fn):
                    try:
                        await stop_fn()
                    except Exception as exc:
                        log.debug("kv watcher stop failed bucket=%s key_pattern=%s", bucket_s, pattern_s, exc_info=exc)

        managed = _ManagedWatcher()

        async def _pump() -> None:
            update_error_logged = False
            missing_stream_logged = False
            while True:
                watcher = None
                try:
                    kv = await self.kv_store(bucket_s)
                    watcher = await kv.watch(pattern_s)
                    managed.set_watcher(watcher)
                    missing_stream_logged = False
                    while True:
                        try:
                            entry = await watcher.updates(timeout=0.5)
                        except asyncio.CancelledError:
                            raise
                        except (NatsTimeoutError, asyncio.TimeoutError, TimeoutError):
                            continue
                        except (BucketNotFoundError, JsNotFoundError) as exc:
                            managed.set_watcher(None)
                            if not missing_stream_logged:
                                missing_stream_logged = True
                                log.debug(
                                    "kv bucket watch waiting for bucket/stream bucket=%s key_pattern=%s",
                                    bucket_s,
                                    pattern_s,
                                    exc_info=exc,
                                )
                            await asyncio.sleep(0.2)
                            break
                        except Exception as exc:
                            if not update_error_logged:
                                update_error_logged = True
                                log.error(
                                    "kv bucket watch updates failed bucket=%s key_pattern=%s",
                                    bucket_s,
                                    pattern_s,
                                    exc_info=exc,
                                )
                            await asyncio.sleep(0.05)
                            continue
                        update_error_logged = False
                        if entry is None:
                            continue
                        try:
                            key = str(entry.key or "")  # type: ignore[attr-defined]
                            value = bytes(entry.value or b"")  # type: ignore[attr-defined]
                        except Exception as exc:
                            log.error(
                                "kv bucket watch entry parse failed bucket=%s key_pattern=%s",
                                bucket_s,
                                pattern_s,
                                exc_info=exc,
                            )
                            continue
                        if not key:
                            continue
                        try:
                            await cb(key, value)
                        except Exception as exc:
                            log.error(
                                "kv bucket watch callback failed bucket=%s key_pattern=%s key=%s",
                                bucket_s,
                                pattern_s,
                                key,
                                exc_info=exc,
                            )
                except asyncio.CancelledError:
                    if watcher is not None:
                        managed.set_watcher(None)
                    break
                finally:
                    await managed.stop()

        task = asyncio.create_task(_pump(), name=f"kv_watch:{bucket_s}:{pattern_s}")
        return (managed, task)

    async def kv_get_in_bucket(self, bucket: str, key: str) -> bytes | None:
        try:
            kv = await self.kv_store(str(bucket))
        except Exception:
            return None
        try:
            entry = await kv.get(str(key))
        except Exception:
            return None
        if entry is None:
            return None
        try:
            return bytes(entry.value or b"")  # type: ignore[attr-defined]
        except Exception:
            return None


async def reset_kv_bucket(
    *,
    url: str,
    kv_bucket: str,
    kv_storage: StorageType = StorageType.MEMORY,
    timeout_s: float = 2.5,
) -> None:
    tr = NatsTransport(
        NatsTransportConfig(
            url=str(url).strip(),
            kv_bucket=str(kv_bucket).strip(),
            kv_storage=kv_storage,
            delete_bucket_on_connect=True,
        )
    )
    await asyncio.wait_for(tr.connect(), timeout=float(timeout_s))
    await tr.close()


def reset_kv_bucket_sync(
    *,
    url: str,
    kv_bucket: str,
    kv_storage: StorageType = StorageType.MEMORY,
    timeout_s: float = 2.5,
) -> None:
    asyncio.run(
        reset_kv_bucket(url=url, kv_bucket=kv_bucket, kv_storage=kv_storage, timeout_s=float(timeout_s))
    )

__all__ = ["NatsTransport", "NatsTransportConfig", "reset_kv_bucket", "reset_kv_bucket_sync"]
