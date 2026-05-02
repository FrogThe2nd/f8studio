from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from f8pysdk.time_utils import now_ms
from msgspec import UNSET
from qtpy import QtWidgets

from f8pystudio.ui.support.ui_notifications import show_keyed_error, show_keyed_warning

_DEFAULT_DEBOUNCE_MS = 1000
_REPEAT_SUMMARY_COUNTS = frozenset({10, 100, 1000})
_MAX_MESSAGE_CHARS = 700
_TOAST_SEVERITIES = frozenset({"warning", "error", "critical"})


@dataclass
class _AlertDebounceState:
    last_shown_wall_ms: int = 0
    last_event_ts_ms: int = 0
    last_repeat_count: int = 0
    shown_repeat_counts: set[int] = field(default_factory=set)


def _text(value: Any) -> str:
    if value is None or value is UNSET:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or value is UNSET:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    return text[: _MAX_MESSAGE_CHARS - 3].rstrip() + "..."


def _toast_message(*, code: str, message: str, repeat_count: int) -> str:
    compact = _compact_message(message)
    if code:
        compact = f"{code}: {compact}" if compact else code
    if repeat_count > 1:
        compact = f"{compact}\nRepeated {repeat_count} times."
    return compact


class MonitorAlertNotifier:
    def __init__(self, *, debounce_ms: int = _DEFAULT_DEBOUNCE_MS) -> None:
        self._debounce_ms = max(0, int(debounce_ms))
        self._state_by_key: dict[tuple[str, str, str], _AlertDebounceState] = {}

    def handle_snapshot(self, payload: dict[str, Any], *, parent: QtWidgets.QWidget | None = None) -> bool:
        service_id = str(payload.get("serviceId") or "").strip()
        if not service_id:
            return False
        error_obj = payload.get("error")
        if not isinstance(error_obj, dict):
            return False

        severity = _text(error_obj.get("lastSeverity")).strip().lower()
        if severity not in _TOAST_SEVERITIES:
            return False
        message = _text(error_obj.get("lastMessage"))
        if not message.strip():
            return False
        last_ts_ms = _int_or_none(error_obj.get("lastTsMs"))
        if last_ts_ms is None:
            return False

        node_id = _text(error_obj.get("lastNodeId")).strip() or service_id
        fingerprint = _text(error_obj.get("lastFingerprint")).strip()
        if not fingerprint:
            code_for_fingerprint = _text(error_obj.get("lastCode")).strip()
            fingerprint = f"{node_id}:{code_for_fingerprint}:{message}"
        key = (service_id, node_id, fingerprint)

        repeat_count = _int_or_none(error_obj.get("lastRepeatCount"))
        repeat_count_value = 0 if repeat_count is None else max(0, int(repeat_count))
        state = self._state_by_key.get(key)
        if state is None:
            state = _AlertDebounceState()
            self._state_by_key[key] = state

        previous_event_ts_ms = int(state.last_event_ts_ms)
        previous_repeat_count = int(state.last_repeat_count)
        if last_ts_ms <= previous_event_ts_ms:
            return False

        wall_ts_ms = int(now_ms())
        reached_repeat_summary = (
            repeat_count_value in _REPEAT_SUMMARY_COUNTS
            and previous_repeat_count < repeat_count_value
            and repeat_count_value not in state.shown_repeat_counts
        )
        debounce_elapsed = (wall_ts_ms - int(state.last_shown_wall_ms)) >= self._debounce_ms
        should_show = state.last_shown_wall_ms <= 0 or debounce_elapsed or reached_repeat_summary

        state.last_event_ts_ms = int(last_ts_ms)
        state.last_repeat_count = int(repeat_count_value)

        if not should_show:
            return False

        if reached_repeat_summary:
            state.shown_repeat_counts.add(int(repeat_count_value))
        state.last_shown_wall_ms = wall_ts_ms

        title = f"{service_id}/{node_id} {severity}"
        body = _toast_message(
            code=_text(error_obj.get("lastCode")).strip(),
            message=message,
            repeat_count=repeat_count_value,
        )
        toast_key = f"monitor:{service_id}:{node_id}:{fingerprint}"
        if severity == "warning":
            show_keyed_warning(parent, toast_key, title, body, repeat_count=repeat_count_value)
        else:
            show_keyed_error(parent, toast_key, title, body, repeat_count=repeat_count_value)
        return True
