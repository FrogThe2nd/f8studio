from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from typing import TypeAlias, cast
from uuid import uuid4

JsonObject: TypeAlias = dict[str, object]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_asset_id() -> str:
    return str(uuid4())


def stable_json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_json_loads(raw: str) -> object:
    return json.loads(str(raw or ""))


def json_object_from_value(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object payload.")
    return cast(JsonObject, value)


def json_object_loads(raw: object, *, default_raw: str = "{}") -> JsonObject:
    if isinstance(raw, dict):
        return json_object_from_value(raw)
    value = json.loads(default_raw if raw is None else str(raw))
    return json_object_from_value(value)


def json_string_list_loads(raw: object, *, default_raw: str = "[]") -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    value = json.loads(default_raw if raw is None else str(raw))
    if not isinstance(value, list):
        raise ValueError("Expected JSON array payload.")
    return [str(item) for item in value]


def mapping_str(mapping: Mapping[object, object], key: str) -> str:
    return str(mapping[key])


def mapping_optional_str(mapping: Mapping[object, object], key: str) -> str | None:
    value = mapping[key]
    if value is None:
        return None
    return str(value)


def mapping_int(mapping: Mapping[object, object], key: str) -> int:
    return int(str(mapping[key]))
