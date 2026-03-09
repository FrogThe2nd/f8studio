from __future__ import annotations

import numbers
from typing import Any, TypeVar, cast

import msgspec


T = TypeVar("T")
_UNSET_SENTINEL = object()


def _strip_unset(value: Any, *, _seen: set[int] | None = None) -> Any:
    if isinstance(value, msgspec.UnsetType):
        return _UNSET_SENTINEL
    if isinstance(value, dict):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return None
        _seen.add(value_id)
        out: dict[Any, Any] = {}
        for key, item in value.items():
            cleaned = _strip_unset(item, _seen=_seen)
            if cleaned is _UNSET_SENTINEL:
                continue
            out[key] = cleaned
        _seen.discard(value_id)
        return out
    if isinstance(value, list):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return None
        _seen.add(value_id)
        out_list: list[Any] = []
        for item in value:
            cleaned = _strip_unset(item, _seen=_seen)
            if cleaned is _UNSET_SENTINEL:
                continue
            out_list.append(cleaned)
        _seen.discard(value_id)
        return out_list
    if isinstance(value, tuple):
        out_tuple: list[Any] = []
        for item in value:
            cleaned = _strip_unset(item, _seen=_seen)
            if cleaned is _UNSET_SENTINEL:
                continue
            out_tuple.append(cleaned)
        return tuple(out_tuple)
    if isinstance(value, (str, int, float, bool, bytes, bytearray, memoryview, type(None))):
        return value
    try:
        converted = msgspec.to_builtins(value)
    except (TypeError, ValueError):
        return value
    if converted is value:
        return value
    return _strip_unset(converted, _seen=_seen)


def _coerce_json_compatible(value: Any, *, _seen: set[int] | None = None) -> Any:
    if value is None:
        return value
    if type(value) in (str, int, float, bool):
        return value
    if isinstance(value, msgspec.UnsetType):
        return None
    if isinstance(value, dict):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return None
        _seen.add(value_id)
        out: dict[str, Any] = {}
        for key, item in value.items():
            out[str(key)] = _coerce_json_compatible(item, _seen=_seen)
        _seen.discard(value_id)
        return out
    if isinstance(value, (list, tuple, set)):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return None
        _seen.add(value_id)
        out_list = [_coerce_json_compatible(item, _seen=_seen) for item in value]
        _seen.discard(value_id)
        return out_list
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)

    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _coerce_json_compatible(item_fn(), _seen=_seen)
        except (TypeError, ValueError):
            pass

    try:
        converted = msgspec.to_builtins(value)
    except (TypeError, ValueError):
        converted = value
    if converted is not value:
        return _coerce_json_compatible(converted, _seen=_seen)

    return str(value)


def validate_as(model_type: type[T], value: Any, *_args: Any, **_kwargs: Any) -> T:
    """Decode a JSON-like payload into a typed msgspec object."""
    return msgspec.convert(value, type=model_type)


def dump_json(value: Any, *_args: Any, **_kwargs: Any) -> Any:
    """Convert msgspec structs/enums/unions into plain JSON-compatible values."""
    try:
        raw = msgspec.to_builtins(value)
    except (TypeError, ValueError):
        raw = value
    cleaned = _strip_unset(raw)
    if cleaned is _UNSET_SENTINEL:
        return None
    return _coerce_json_compatible(cleaned)


def copy_model(value: T, *_args: Any, **kwargs: Any) -> T:
    """
    Replace-style copy for msgspec structs.

    Legacy call sites may pass `deep=True` and `update=...`; `deep` is ignored.
    """
    update_obj = kwargs.get("update")
    if isinstance(value, msgspec.Struct):
        if isinstance(update_obj, dict):
            return msgspec.structs.replace(value, **update_obj)
        return msgspec.structs.replace(value)
    if isinstance(value, dict):
        copied = dict(value)
        if isinstance(update_obj, dict):
            copied.update(update_obj)
        return cast(T, copied)
    if isinstance(update_obj, dict) and (value is None or isinstance(value, msgspec.UnsetType)):
        return cast(T, dict(update_obj))
    return value
