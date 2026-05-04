from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pysdk.runtime_transport import RuntimeTransport


@dataclass(frozen=True)
class RuntimeMessage:
    data: bytes


@dataclass
class RuntimeTransportRequester:
    transport: RuntimeTransport

    async def request(self, subject: str, payload: bytes, timeout: float) -> Any:
        raw = await self.transport.request(
            str(subject),
            bytes(payload),
            timeout=float(timeout),
            raise_on_error=True,
        )
        if raw is None:
            raise TimeoutError(f"runtime request timed out subject={subject!r}")
        return RuntimeMessage(data=bytes(raw))


__all__ = ["RuntimeMessage", "RuntimeTransportRequester"]
