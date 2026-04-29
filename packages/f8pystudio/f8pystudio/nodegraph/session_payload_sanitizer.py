from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from f8pysdk.codec import dump_json

from .session_schema import extract_layout, wrap_layout_for_save

_RUNTIME_ONLY_STATE_FIELD_NAMES = frozenset({"lastError"})
_PERSISTED_READONLY_STATE_FIELD_NAMES = frozenset({"svcId", "operatorId"})


@dataclass(frozen=True)
class _StateFieldRule:
    access: str
    redact_on_publish: bool
    value_schema: Any


def sanitize_session_payload_for_persistence(
    payload: dict[str, Any],
    *,
    redact_publish_state_values: bool = False,
) -> dict[str, Any]:
    layout = extract_layout(payload)
    sanitized_layout = sanitize_session_layout_for_persistence(
        layout,
        redact_publish_state_values=redact_publish_state_values,
    )
    return wrap_layout_for_save(sanitized_layout)


def sanitize_session_layout_for_persistence(
    layout_data: dict[str, Any],
    *,
    redact_publish_state_values: bool = False,
) -> dict[str, Any]:
    sanitized_layout = copy.deepcopy(layout_data)
    nodes = sanitized_layout.get("nodes")
    if not isinstance(nodes, dict):
        return sanitized_layout

    for node_data in nodes.values():
        if not isinstance(node_data, dict):
            continue
        _sanitize_node_data_for_persistence(
            node_data,
            redact_publish_state_values=redact_publish_state_values,
        )
    return sanitized_layout


def strip_runtime_only_state_fields_from_layout(layout_data: dict[str, Any]) -> dict[str, Any]:
    nodes = layout_data.get("nodes")
    if not isinstance(nodes, dict):
        return layout_data
    for node_data in nodes.values():
        if not isinstance(node_data, dict):
            continue
        raw_spec = node_data.get("f8_spec")
        if isinstance(raw_spec, dict):
            _strip_runtime_only_state_fields_from_spec(raw_spec)
        custom = node_data.get("custom")
        if isinstance(custom, dict):
            for field_name in _RUNTIME_ONLY_STATE_FIELD_NAMES:
                custom.pop(field_name, None)
        ui_overrides = node_data.get("f8_ui_overrides")
        if isinstance(ui_overrides, dict):
            state_fields_ui = ui_overrides.get("stateFields")
            if isinstance(state_fields_ui, dict):
                for field_name in _RUNTIME_ONLY_STATE_FIELD_NAMES:
                    state_fields_ui.pop(field_name, None)
        f8_sys = node_data.get("f8_sys")
        if isinstance(f8_sys, dict):
            missing_spec = f8_sys.get("missingSpec")
            if isinstance(missing_spec, dict):
                _strip_runtime_only_state_fields_from_spec(missing_spec)
    return layout_data


def sanitize_session_content_for_persistence(
    content: dict[str, Any],
    *,
    redact_publish_state_values: bool = False,
) -> dict[str, Any]:
    return sanitize_session_payload_for_persistence(
        content,
        redact_publish_state_values=redact_publish_state_values,
    )


def _strip_runtime_only_state_fields_from_spec(raw_spec: dict[str, Any]) -> None:
    raw_state_fields = raw_spec.get("stateFields")
    if not isinstance(raw_state_fields, list):
        return
    raw_spec["stateFields"] = [
        raw_field
        for raw_field in raw_state_fields
        if not (
            isinstance(raw_field, dict)
            and str(raw_field.get("name") or "").strip() in _RUNTIME_ONLY_STATE_FIELD_NAMES
        )
    ]


def _sanitize_node_data_for_persistence(
    node_data: dict[str, Any],
    *,
    redact_publish_state_values: bool,
) -> None:
    raw_spec = node_data.get("f8_spec")
    if not isinstance(raw_spec, dict):
        return

    raw_spec.pop("launch", None)
    state_rules = _state_field_rules(raw_spec)

    custom = node_data.get("custom")
    if not isinstance(custom, dict) or not custom:
        return

    sanitized_custom: dict[str, Any] = {}
    for key, value in custom.items():
        field_name = str(key)
        rule = state_rules.get(field_name)
        if rule is None:
            sanitized_custom[field_name] = value
            continue
        if field_name in _RUNTIME_ONLY_STATE_FIELD_NAMES:
            continue
        if rule.access == "ro" and field_name not in _PERSISTED_READONLY_STATE_FIELD_NAMES:
            continue
        if redact_publish_state_values and rule.redact_on_publish:
            sanitized_custom[field_name] = _json_default_redacted_value(rule.value_schema)
            continue
        sanitized_custom[field_name] = value

    if sanitized_custom != custom:
        node_data["custom"] = sanitized_custom


def _state_field_rules(raw_spec: dict[str, Any]) -> dict[str, _StateFieldRule]:
    raw_state_fields = raw_spec.get("stateFields")
    if not isinstance(raw_state_fields, list):
        return {}
    rules: dict[str, _StateFieldRule] = {}
    for raw_field in raw_state_fields:
        if not isinstance(raw_field, dict):
            continue
        field_name = str(raw_field.get("name") or "").strip()
        if not field_name:
            continue
        rules[field_name] = _StateFieldRule(
            access=str(raw_field.get("access") or "").strip(),
            redact_on_publish=bool(raw_field.get("redactOnPublish")),
            value_schema=raw_field.get("valueSchema"),
        )
    return rules


def _json_default_redacted_value(value_schema: Any) -> Any:
    try:
        schema_json = dump_json(value_schema, mode="json")
    except (AttributeError, TypeError, ValueError):
        schema_json = value_schema
    if not isinstance(schema_json, dict):
        return None

    if "default" in schema_json:
        return copy.deepcopy(schema_json.get("default"))

    schema_type = schema_json.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if isinstance(item, str) and item != "null"]
        schema_type = non_null_types[0] if non_null_types else None

    if schema_type == "string":
        return ""
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    if schema_type == "number":
        return 0
    if schema_type == "integer":
        return 0
    if schema_type == "boolean":
        return False
    return None


__all__ = [
    "sanitize_session_content_for_persistence",
    "sanitize_session_layout_for_persistence",
    "sanitize_session_payload_for_persistence",
    "strip_runtime_only_state_fields_from_layout",
]
