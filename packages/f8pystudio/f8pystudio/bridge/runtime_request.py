from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from f8pysdk.codec import decode_as, encode_obj
from f8pysdk.runtime_transport import RuntimeTransport


class RuntimeRequester(Protocol):
    async def request(self, subject: str, payload: bytes, timeout: float) -> Any: ...


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


T = TypeVar("T")


async def request_typed(
    requester: RuntimeRequester,
    *,
    subject: str,
    payload: Any,
    timeout_s: float,
    response_type: type[T],
) -> T:
    raw_payload = encode_obj(payload)
    message = await requester.request(str(subject), raw_payload, timeout=float(timeout_s))
    raw = bytes(message.data or b"")
    if not raw:
        raise RuntimeError("empty response")
    try:
        return decode_as(raw, response_type)
    except ValueError as exc:
        raise RuntimeError(f"response is not a valid MsgPack object: {exc}") from exc


__all__ = ["RuntimeMessage", "RuntimeRequester", "RuntimeTransportRequester", "request_typed"]
