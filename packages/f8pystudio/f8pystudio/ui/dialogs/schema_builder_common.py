from __future__ import annotations

import json
from typing import Any

from qtpy import QtCore

from f8pysdk.codec import dump_json, validate_as
from f8pysdk.specs import F8DataTypeSchema

_SCHEMA_TYPE_VALUES: tuple[str, ...] = (
    "string",
    "number",
    "integer",
    "boolean",
    "null",
    "object",
    "array",
    "any",
)

_COMMON_KEYS: set[str] = {
    "type",
    "title",
    "description",
    "default",
    "examples",
    "$comment",
}

_PRIMITIVE_KEYS: set[str] = _COMMON_KEYS | {
    "enum",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
}

_OBJECT_KEYS: set[str] = _COMMON_KEYS | {
    "properties",
    "required",
    "additionalProperties",
}

_ARRAY_KEYS: set[str] = _COMMON_KEYS | {
    "items",
}

_ANY_KEYS: set[str] = set(_COMMON_KEYS)

_PATH_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1


def _encode_path(path: tuple[str, ...]) -> str:
    return json.dumps(list(path), ensure_ascii=False)


def _decode_path(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    out: list[str] = []
    for item in data:
        out.append(str(item))
    return tuple(out)


def schema_to_json_obj(schema: F8DataTypeSchema) -> dict[str, Any]:
    obj = dump_json(schema, mode="json", by_alias=True, exclude_none=True)
    if isinstance(obj, dict):
        return obj
    raise ValueError("schema must serialize to a JSON object")


def schema_from_json_obj(obj: Any) -> F8DataTypeSchema:
    return validate_as(F8DataTypeSchema, obj)


def validate_schema_json_unknown_keys(obj: Any) -> list[str]:
    unknown: list[str] = []

    def _join(path: str, segment: str) -> str:
        if segment.startswith("["):
            return path + segment
        return path + "." + segment

    def _allowed_keys(schema_type: str) -> set[str]:
        if schema_type in {"string", "number", "integer", "boolean", "null"}:
            return _PRIMITIVE_KEYS
        if schema_type == "object":
            return _OBJECT_KEYS
        if schema_type == "array":
            return _ARRAY_KEYS
        if schema_type == "any":
            return _ANY_KEYS
        return _COMMON_KEYS

    def _visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return

        schema_type = str(node.get("type") or "").strip().lower()
        allowed = _allowed_keys(schema_type)
        for key in node.keys():
            if key not in allowed:
                unknown.append(_join(path, str(key)))

        if schema_type == "object":
            properties = node.get("properties")
            if isinstance(properties, dict):
                for prop_name, prop_schema in properties.items():
                    _visit(prop_schema, _join(_join(path, "properties"), str(prop_name)))
            return

        if schema_type == "array":
            _visit(node.get("items"), _join(path, "items"))
            return

    _visit(obj, "$")
    return sorted(unknown)
