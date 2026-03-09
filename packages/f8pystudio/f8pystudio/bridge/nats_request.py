from __future__ import annotations

from typing import Any, Protocol, TypeVar

from f8pysdk.service_bus.codec import decode_as, encode_obj


class NatsRequester(Protocol):
    async def request(self, subject: str, payload: bytes, timeout: float) -> Any: ...

T = TypeVar("T")


async def request_typed(nc: NatsRequester, *, subject: str, payload: Any, timeout_s: float, response_type: type[T]) -> T:
    raw_payload = encode_obj(payload)
    message = await nc.request(str(subject), raw_payload, timeout=float(timeout_s))
    raw = bytes(message.data or b"")
    if not raw:
        raise RuntimeError("empty response")
    try:
        return decode_as(raw, response_type)
    except ValueError as exc:
        raise RuntimeError(f"response is not a valid MsgPack object: {exc}") from exc
