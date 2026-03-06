from __future__ import annotations

from typing import Any

import msgspec

from .state_binding import ValueAdapter


def normalize_script_output_value(value: Any, *, _seen: set[int] | None = None) -> Any:
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if isinstance(value, msgspec.Struct):
        return msgspec.to_builtins(value)
    value = ValueAdapter.unwrap(value)
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if isinstance(value, dict):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return None
        _seen.add(value_id)
        out: dict[str, Any] = {}
        for key, item in value.items():
            out[str(key)] = normalize_script_output_value(item, _seen=_seen)
        _seen.discard(value_id)
        return out
    if isinstance(value, list):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return []
        _seen.add(value_id)
        out_list = [normalize_script_output_value(item, _seen=_seen) for item in value]
        _seen.discard(value_id)
        return out_list
    if isinstance(value, tuple):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return ()
        _seen.add(value_id)
        out_tuple = tuple(normalize_script_output_value(item, _seen=_seen) for item in value)
        _seen.discard(value_id)
        return out_tuple
    if isinstance(value, set):
        if _seen is None:
            _seen = set()
        value_id = id(value)
        if value_id in _seen:
            return []
        _seen.add(value_id)
        out_set = [normalize_script_output_value(item, _seen=_seen) for item in value]
        _seen.discard(value_id)
        return out_set
    return value


def normalize_script_output_value_fast(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return normalize_script_output_value(value)
