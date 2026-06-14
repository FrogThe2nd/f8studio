from __future__ import annotations

import re
from typing import Any

from .models import JsonObject

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_ABSOLUTE_RE = re.compile(r"^\\\\[^\\]+\\[^\\]+")
_POSIX_ABSOLUTE_RE = re.compile(r"^/")
_REDACTED_PATH = "[local-path-redacted]"


def redact_local_paths(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_local_only_key(key_text):
                continue
            out[key_text] = redact_local_paths(item)
        return out
    if isinstance(value, list):
        return [redact_local_paths(item) for item in value]
    if isinstance(value, str):
        return _redact_path_text(value)
    return value


def validate_no_absolute_local_paths(value: Any) -> None:
    found = _find_absolute_path(value, path="$")
    if found:
        raise ValueError(f"modding recipe publish content contains an absolute local path at {found}")


def sanitized_recipe_content(content: JsonObject) -> JsonObject:
    redacted = redact_local_paths(content)
    if not isinstance(redacted, dict):
        raise ValueError("modding recipe content must be a JSON object")
    validate_no_absolute_local_paths(redacted)
    return {str(key): item for key, item in redacted.items()}


def _is_local_only_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized in {"lasttargetpath", "selectedpath", "resolvedgameroot", "executablepath", "gamepath", "targetpath"}


def _redact_path_text(text: str) -> str:
    stripped = str(text or "").strip()
    if _looks_like_absolute_path(stripped):
        return _REDACTED_PATH
    return text


def _find_absolute_path(value: Any, *, path: str) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_absolute_path(item, path=f"{path}.{key}")
            if found:
                return found
        return ""
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_absolute_path(item, path=f"{path}[{index}]")
            if found:
                return found
        return ""
    if isinstance(value, str) and _looks_like_absolute_path(value):
        return path
    return ""


def _looks_like_absolute_path(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _WINDOWS_ABSOLUTE_RE.match(value) is not None:
        return True
    if _UNC_ABSOLUTE_RE.match(value) is not None:
        return True
    return _POSIX_ABSOLUTE_RE.match(value) is not None


__all__ = [
    "redact_local_paths",
    "sanitized_recipe_content",
    "validate_no_absolute_local_paths",
]
