from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


TransportCallback = Callable[[str, bytes], Awaitable[None]]
RequestHandler = Callable[[bytes], Awaitable[bytes | None]]


@runtime_checkable
class RuntimeTransport(Protocol):
    """
    Explicit runtime transport contract used by ServiceBus.

    The contract is intentionally small and concrete: key-expression pub/sub for
    transient data, command-key request/serve for sparse control endpoints, and
    retained key samples for latest-value state/status snapshots.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, key: str, payload: bytes) -> None: ...

    async def subscribe(
        self,
        key_expr: str,
        *,
        queue: str | None = None,
        cb: TransportCallback | None = None,
    ) -> Any: ...

    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None: ...

    async def serve(self, key: str, handler: RequestHandler) -> Any: ...

    async def retained_put(self, key: str, value: bytes) -> None: ...

    async def retained_get(self, key: str) -> bytes | None: ...

    async def retained_watch(self, key_expr: str, *, cb: TransportCallback, with_initial: bool = True) -> Any: ...


__all__ = [
    "RequestHandler",
    "RuntimeTransport",
    "TransportCallback",
]
