from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NullFixStats:
    dropped_null_keys: int = 0
    updated_type_null: int = 0
    inferred_type_null: int = 0


def _looks_schema_like(obj: dict[str, Any]) -> bool:
    schema_keys = {
        "type",
        "enum",
        "default",
        "properties",
        "items",
        "required",
        "oneOf",
        "anyOf",
        "allOf",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "description",
        "title",
        "$comment",
    }
    return any(key in obj for key in schema_keys)


def _normalize_null_type_value(value: Any) -> tuple[bool, str | None]:
    if value is None:
        return True, "null"
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"none", "nil", "<null>"}:
            return True, "null"
    return False, None


def sanitize_legacy_nulls(
    value: Any,
    *,
    keep_null_keys: set[str] | None = None,
    stats: NullFixStats | None = None,
) -> Any:
    """
    Sanitize legacy JSON payloads where optional metadata was serialized as null.

    msgspec structs typically expect optional keys to be omitted rather than
    present with a null value.
    """
    if keep_null_keys is None:
        keep_null_keys = {"default", "value"}
    if stats is None:
        stats = NullFixStats()

    def _fix_tree(v: Any) -> Any:
        if isinstance(v, list):
            return [_fix_tree(item) for item in v]
        if not isinstance(v, dict):
            return v

        out: dict[str, Any] = {}
        for key, item in v.items():
            key_s = str(key)
            fixed_item = _fix_tree(item)
            if fixed_item is None and key_s not in keep_null_keys:
                stats.dropped_null_keys += 1
                continue
            out[key_s] = fixed_item

        if "type" in out and _looks_schema_like(out):
            hit, normalized = _normalize_null_type_value(out.get("type"))
            if hit and normalized is not None:
                out["type"] = normalized
                stats.updated_type_null += 1

        if "type" not in out and _looks_schema_like(out):
            enum_value = out.get("enum")
            if isinstance(enum_value, list) and any(item is None for item in enum_value):
                out["type"] = "null"
                stats.inferred_type_null += 1

        return out

    return _fix_tree(value)

