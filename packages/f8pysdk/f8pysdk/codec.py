from __future__ import annotations

from typing import Any, TypeVar

import msgspec

from .msgspec_codec import dump_json

T = TypeVar("T")


def _msgpack_enc_hook(obj: Any) -> Any:
    if isinstance(obj, msgspec.UnsetType):
        return None
    normalized = dump_json(obj, mode="json")
    if normalized is obj:
        raise TypeError(f"unsupported msgpack object: {type(obj).__name__}")
    return normalized


_MSGPACK_ENCODER = msgspec.msgpack.Encoder(enc_hook=_msgpack_enc_hook)
_MSGPACK_DICT_DECODER = msgspec.msgpack.Decoder(type=dict[str, Any])


def encode_obj(obj: Any) -> bytes:
    try:
        return _MSGPACK_ENCODER.encode(obj)
    except (TypeError, ValueError, msgspec.EncodeError) as exc:
        raise ValueError(f"msgpack encode failed: {exc}") from exc


def decode_obj(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = _MSGPACK_DICT_DECODER.decode(raw)
    except (TypeError, ValueError, msgspec.DecodeError, msgspec.ValidationError) as exc:
        raise ValueError(f"msgpack decode failed: {exc}") from exc
    return decoded


def decode_as(raw: bytes, model_type: type[T]) -> T:
    if not raw:
        raise ValueError("msgpack decode failed: empty payload")
    try:
        return msgspec.msgpack.decode(raw, type=model_type)
    except (TypeError, ValueError, msgspec.DecodeError, msgspec.ValidationError) as exc:
        raise ValueError(f"msgpack decode failed: {exc}") from exc

__all__ = ["decode_as", "decode_obj", "encode_obj"]
