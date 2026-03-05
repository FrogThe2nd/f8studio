from __future__ import annotations

from typing import Any


def unwrap_json_value(value: Any) -> Any:
    """
    Convert possible schema/value wrappers into plain Python JSON-like values.
    """
    if value is None or isinstance(value, (str, int, float, bool, list, dict, tuple)):
        return value

    try:
        from .msgspec_codec import dump_json

        dumped = dump_json(value, mode="json")
    except (AttributeError, TypeError, ValueError):
        return value
    return dumped
