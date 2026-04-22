from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
import json
import os
from typing import TypeAlias, cast
from urllib import parse
from uuid import uuid4
import zlib

JsonObject: TypeAlias = dict[str, object]
ASSET_CLOUD_BASE_URL_ENV: str = "F8_ASSET_CLOUD_BASE_URL"
_REDACTED_VALUE = "[redacted]"
_SENSITIVE_JSON_KEYS = frozenset(
    {
        "accessToken",
        "authorization",
        "cookie",
        "idToken",
        "password",
        "refreshToken",
        "session",
        "sessionCookie",
        "setCookie",
        "token",
    },
)


def now_iso() -> str:
    return canonicalize_iso_utc(datetime.now(timezone.utc))


def canonicalize_iso_utc(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_timestamp_for_local_display(text)
    if parsed is None:
        return text
    normalized = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_timestamp_for_local_display(value: object, *, local_tz: tzinfo | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_timestamp_for_local_display(text)
    if parsed is None:
        return text
    display_dt = parsed.astimezone(local_tz)
    return display_dt.strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp_tooltip(value: object, *, local_tz: tzinfo | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_timestamp_for_local_display(text)
    if parsed is None:
        return text
    display_dt = parsed.astimezone(local_tz)
    display_text = display_dt.strftime("%Y-%m-%d %H:%M:%S")
    timezone_name = str(display_dt.tzname() or "").strip()
    if timezone_name:
        return f"{display_text} {timezone_name}"
    return display_text


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


def mapping_optional_int(mapping: Mapping[object, object], key: str) -> int | None:
    value = mapping[key]
    if value is None:
        return None
    return int(str(value))


def origin_headers_for_base_url(base_url: str) -> dict[str, str]:
    parsed = parse.urlsplit(str(base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return {}
    origin = f"{parsed.scheme}://{parsed.netloc}"
    referer_path = parsed.path.rstrip("/")
    referer = f"{origin}{referer_path}/" if referer_path else f"{origin}/"
    return {
        "Origin": origin,
        "Referer": referer,
    }


def resolve_asset_cloud_base_url(*, saved_base_url: str, default_base_url: str, fallback_base_url: str = "") -> str:
    configured_base_url = str(os.environ.get(ASSET_CLOUD_BASE_URL_ENV) or "").strip().rstrip("/")
    if configured_base_url:
        return configured_base_url
    normalized_saved_base_url = str(saved_base_url or "").strip().rstrip("/")
    if normalized_saved_base_url:
        return normalized_saved_base_url
    normalized_fallback_base_url = str(fallback_base_url or "").strip().rstrip("/")
    if normalized_fallback_base_url:
        return normalized_fallback_base_url
    return str(default_base_url or "").strip().rstrip("/")


def decode_http_response_text(raw_bytes: bytes, *, content_encoding: str) -> str:
    decoded_bytes = bytes(raw_bytes)
    normalized_encoding = str(content_encoding or "").strip().lower()
    if "gzip" in normalized_encoding:
        decoded_bytes = zlib.decompress(decoded_bytes, wbits=31)
    return decoded_bytes.decode("utf-8", errors="replace")


def redact_json_for_log(value: object) -> object:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_json_key(key_text):
                out[key_text] = _REDACTED_VALUE
            else:
                out[key_text] = redact_json_for_log(item)
        return out
    if isinstance(value, list):
        return [redact_json_for_log(item) for item in value]
    return value


def redact_http_body_for_log(body_text: str, *, max_chars: int) -> str:
    text = str(body_text or "")
    try:
        value = stable_json_loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text[:max_chars]
    return stable_json_dumps(redact_json_for_log(value))[:max_chars]


def _is_sensitive_json_key(key: str) -> bool:
    normalized = key.replace("-", "").replace("_", "").lower()
    for sensitive_key in _SENSITIVE_JSON_KEYS:
        sensitive_normalized = sensitive_key.replace("-", "").replace("_", "").lower()
        if normalized == sensitive_normalized:
            return True
    return False


def _parse_timestamp_for_local_display(text: str) -> datetime | None:
    normalized_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized_text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
