from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from f8pysdk.codec import dump_json, validate_as
from f8pysdk.specs import F8OperatorSpec

from f8pystudio.editor_assist.protocol import editor_assist_context_for_field
from f8pystudio.editor_assist.workspace import EditorAssistContext
from f8pystudio.nodegraph.session_payload_sanitizer import strip_runtime_only_state_fields_from_layout

PYENGINE_SERVICE_CLASS = "f8.pyengine"
PYTHON_SCRIPT_OPERATOR_CLASS = "f8.python_script"
PYTHON_SCRIPT_CODE_FIELD = "code"


@dataclass(frozen=True)
class SessionEditorTarget:
    session_path: Path
    node_id: str
    spec: F8OperatorSpec
    code: str
    context: EditorAssistContext


def load_python_script_editor_targets(session_path: Path) -> list[SessionEditorTarget]:
    resolved_path = session_path.expanduser().resolve()
    payload = _load_json_file(resolved_path)
    raw_layout = payload.get("layout")
    if isinstance(raw_layout, dict):
        strip_runtime_only_state_fields_from_layout(raw_layout)
    targets: list[SessionEditorTarget] = []

    for node_id, node_payload in _iter_layout_nodes(payload):
        raw_spec = node_payload.get("f8_spec")
        if not isinstance(raw_spec, dict):
            continue
        if not _target_matches(raw_spec):
            continue

        spec = validate_as(F8OperatorSpec, raw_spec)
        context = editor_assist_context_for_field(
            spec,
            field_kind="state",
            field_key=PYTHON_SCRIPT_CODE_FIELD,
            language="python",
        )
        if context is None:
            raise ValueError(f"Editor assist context missing for node {node_id}")

        code = _node_code_or_default(node_payload, spec)
        targets.append(
            SessionEditorTarget(
                session_path=resolved_path,
                node_id=node_id,
                spec=spec,
                code=code,
                context=context,
            )
        )

    if targets:
        return targets
    raise ValueError(
        "No matching node found for "
        f"operatorClass={PYTHON_SCRIPT_OPERATOR_CLASS!r}, serviceClass={PYENGINE_SERVICE_CLASS!r}"
    )


def load_python_script_editor_target(session_path: Path) -> SessionEditorTarget:
    return load_python_script_editor_targets(session_path)[0]


def _load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Session root must be a JSON object")
    return payload


def _iter_layout_nodes(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("Session JSON missing layout object")
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("Session JSON missing layout.nodes object")

    result: list[tuple[str, dict[str, Any]]] = []
    for raw_node_id, raw_node in nodes.items():
        node_id = str(raw_node_id or "").strip()
        if not node_id or not isinstance(raw_node, dict):
            continue
        result.append((node_id, raw_node))
    return result


def _target_matches(raw_spec: dict[str, Any]) -> bool:
    operator_class = str(raw_spec.get("operatorClass") or "").strip()
    service_class = str(raw_spec.get("serviceClass") or "").strip()
    return operator_class == PYTHON_SCRIPT_OPERATOR_CLASS and service_class == PYENGINE_SERVICE_CLASS


def _node_code_or_default(node_payload: dict[str, Any], spec: F8OperatorSpec) -> str:
    raw_custom = node_payload.get("custom")
    custom = raw_custom if isinstance(raw_custom, dict) else {}
    raw_code = custom.get(PYTHON_SCRIPT_CODE_FIELD)
    if raw_code is not None:
        return str(raw_code or "")

    for field in spec.stateFields:
        if str(field.name or "").strip() != PYTHON_SCRIPT_CODE_FIELD:
            continue
        schema_json = dump_json(field.valueSchema)
        if not isinstance(schema_json, dict):
            return ""
        default_value = schema_json.get("default")
        return default_value if isinstance(default_value, str) else ""
    return ""
