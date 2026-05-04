from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


TransportCallback = Callable[[str, bytes], Awaitable[None]]
RequestHandler = Callable[[bytes], Awaitable[bytes | None]]


@runtime_checkable
class RuntimeTransport(Protocol):
    """
    Explicit runtime transport contract used by ServiceBus.

    The contract is intentionally small and concrete: pub/sub for transient data,
    request/serve for control endpoints, and a latest-value KV facade for state.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, subject: str, payload: bytes) -> None: ...

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: TransportCallback | None = None,
    ) -> Any: ...

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None: ...

    async def serve(self, subject: str, handler: RequestHandler) -> Any: ...

    async def kv_put(self, key: str, value: bytes) -> None: ...

    async def kv_get(self, key: str) -> bytes | None: ...

    async def kv_watch(self, key_pattern: str, *, cb: TransportCallback) -> Any: ...

    async def kv_watch_in_bucket(
        self,
        bucket: str,
        key_pattern: str,
        *,
        cb: TransportCallback,
    ) -> Any: ...

    async def kv_get_in_bucket(
        self,
        bucket: str,
        key: str,
        *,
        timeout: float | None = None,
    ) -> bytes | None: ...


__all__ = [
    "RequestHandler",
    "RuntimeTransport",
    "TransportCallback",
]
