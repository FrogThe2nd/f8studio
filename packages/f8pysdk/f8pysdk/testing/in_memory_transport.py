from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


def _match_pattern(pattern: str, key: str) -> bool:
    if pattern == key:
        return True
    if pattern.endswith(">"):
        prefix = pattern[:-1]
        return key.startswith(prefix)
    return False


@dataclass
class InMemoryCluster:
    kv: dict[str, dict[str, bytes]] = field(default_factory=dict)
    kv_watchers: dict[str, list[tuple[str, Callable[[str, bytes], Awaitable[None]]]]] = field(default_factory=dict)
    subs: dict[str, list[Callable[[str, bytes], Awaitable[None]]]] = field(default_factory=dict)
    request_handlers: dict[str, Callable[[bytes], Awaitable[bytes | None]]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def kv_put(self, bucket: str, key: str, value: bytes) -> None:
        callbacks: list[Callable[[str, bytes], Awaitable[None]]] = []
        async with self.lock:
            self.kv.setdefault(bucket, {})[key] = bytes(value)
            for pattern, cb in list(self.kv_watchers.get(bucket, [])):
                if _match_pattern(pattern, key):
                    callbacks.append(cb)
        for cb in callbacks:
            await cb(key, bytes(value))

    async def kv_get(self, bucket: str, key: str) -> bytes | None:
        async with self.lock:
            return self.kv.get(bucket, {}).get(key)

    def add_kv_watch(self, bucket: str, pattern: str, cb: Callable[[str, bytes], Awaitable[None]]) -> None:
        self.kv_watchers.setdefault(bucket, []).append((pattern, cb))

    def remove_kv_watch(self, bucket: str, pattern: str, cb: Callable[[str, bytes], Awaitable[None]]) -> None:
        watchers = self.kv_watchers.get(bucket)
        if not watchers:
            return
        try:
            watchers.remove((pattern, cb))
        except ValueError:
            return

    async def publish(self, subject: str, payload: bytes) -> None:
        for cb in list(self.subs.get(subject, [])):
            await cb(str(subject), bytes(payload))

    def subscribe(self, subject: str, cb: Callable[[str, bytes], Awaitable[None]]) -> None:
        self.subs.setdefault(subject, []).append(cb)

    def unsubscribe(self, subject: str, cb: Callable[[str, bytes], Awaitable[None]]) -> None:
        subs = self.subs.get(subject)
        if not subs:
            return
        try:
            subs.remove(cb)
        except ValueError:
            return

    async def request(self, subject: str, payload: bytes) -> bytes | None:
        handler = self.request_handlers.get(str(subject))
        if handler is None:
            return None
        result = await handler(bytes(payload))
        if result is None:
            return None
        return bytes(result)

    def serve(self, subject: str, handler: Callable[[bytes], Awaitable[bytes | None]]) -> None:
        self.request_handlers[str(subject)] = handler

    def unserve(self, subject: str, handler: Callable[[bytes], Awaitable[bytes | None]]) -> None:
        existing = self.request_handlers.get(str(subject))
        if existing is handler:
            self.request_handlers.pop(str(subject), None)


class _WatchHandle:
    def __init__(self, cluster: InMemoryCluster, bucket: str, pattern: str, cb: Callable[[str, bytes], Awaitable[None]]):
        self._cluster = cluster
        self._bucket = bucket
        self._pattern = pattern
        self._cb = cb

    async def stop(self) -> None:
        self._cluster.remove_kv_watch(self._bucket, self._pattern, self._cb)


class _ServeHandle:
    def __init__(
        self,
        cluster: InMemoryCluster,
        subject: str,
        handler: Callable[[bytes], Awaitable[bytes | None]],
    ) -> None:
        self._cluster = cluster
        self._subject = subject
        self._handler = handler

    async def unsubscribe(self) -> None:
        self._cluster.unserve(self._subject, self._handler)

    async def stop(self) -> None:
        await self.unsubscribe()


class InMemoryTransport:
    """
    Minimal in-memory transport for async tests (KV + pub/sub).

    This mirrors only the subset of methods used by ServiceBus.
    """

    def __init__(self, *, cluster: InMemoryCluster, kv_bucket: str) -> None:
        self._cluster = cluster
        self._kv_bucket = str(kv_bucket)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def require_client(self) -> Any:
        return self

    async def publish(self, subject: str, payload: bytes) -> None:
        await self._cluster.publish(str(subject), bytes(payload))

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> Any:
        if cb is None:
            return None
        cluster = self._cluster
        subject_name = str(subject)
        cluster.subscribe(subject_name, cb)

        class _Sub:
            async def unsubscribe(self) -> None:
                cluster.unsubscribe(subject_name, cb)

        return _Sub()

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        try:
            return await asyncio.wait_for(self._cluster.request(str(subject), bytes(payload)), timeout=float(timeout))
        except asyncio.TimeoutError:
            if raise_on_error:
                raise
            return None

    async def serve(self, subject: str, handler: Callable[[bytes], Awaitable[bytes | None]]) -> _ServeHandle:
        subject_name = str(subject)
        self._cluster.serve(subject_name, handler)
        return _ServeHandle(self._cluster, subject_name, handler)

    async def kv_put(self, key: str, value: bytes) -> None:
        await self._cluster.kv_put(self._kv_bucket, str(key), bytes(value))

    async def kv_get(self, key: str) -> bytes | None:
        return await self._cluster.kv_get(self._kv_bucket, str(key))

    async def kv_watch(self, key_pattern: str, *, cb: Callable[[str, bytes], Awaitable[None]]) -> Any:
        return await self.kv_watch_in_bucket(self._kv_bucket, str(key_pattern), cb=cb)

    async def kv_watch_in_bucket(
        self, bucket: str, key_pattern: str, *, cb: Callable[[str, bytes], Awaitable[None]]
    ) -> Any:
        self._cluster.add_kv_watch(str(bucket), str(key_pattern), cb)
        handle = _WatchHandle(self._cluster, str(bucket), str(key_pattern), cb)
        task = asyncio.create_task(asyncio.sleep(0), name=f"mem_kv_watch:{bucket}:{key_pattern}")
        return (handle, task)

    async def kv_get_in_bucket(self, bucket: str, key: str, *, timeout: float | None = None) -> bytes | None:
        del timeout
        return await self._cluster.kv_get(str(bucket), str(key))
