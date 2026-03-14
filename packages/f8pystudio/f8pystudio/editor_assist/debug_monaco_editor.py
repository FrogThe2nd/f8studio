from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk import F8OperatorSpec
from f8pysdk.msgspec_codec import dump_json, validate_as

from ..app_logging import configure_root_logging_from_env
from ..editor_assist.session import EditorSessionKey
from ..qt_font_utils import normalize_application_font
from ..webengine_utils import configure_default_webengine_profile
from ..widgets.monaco_editor_dialog import open_code_editor_window
from .protocol import editor_assist_context_for_field
from .workspace import EditorAssistContext

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_PATH = Path(__file__).parent.parent.parent / "tests/test_pyscript.json"
_TARGET_OPERATOR_CLASS = "f8.python_script"
_TARGET_SERVICE_CLASS = "f8.pyengine"
_TARGET_FIELD_NAME = "code"


@dataclass(frozen=True)
class SessionEditorTarget:
    session_path: Path
    node_id: str
    spec: F8OperatorSpec
    code: str
    context: EditorAssistContext


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
    return operator_class == _TARGET_OPERATOR_CLASS and service_class == _TARGET_SERVICE_CLASS


def load_session_editor_targets(session_path: Path) -> list[SessionEditorTarget]:
    resolved_path = session_path.expanduser().resolve()
    payload = _load_json_file(resolved_path)
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
            field_key=_TARGET_FIELD_NAME,
            language="python",
        )
        if context is None:
            raise ValueError(f"Editor assist context missing for node {node_id}")

        raw_custom = node_payload.get("custom")
        custom = raw_custom if isinstance(raw_custom, dict) else {}
        raw_code = custom.get(_TARGET_FIELD_NAME)
        if raw_code is None:
            for field in spec.stateFields:
                if str(field.name or "").strip() != _TARGET_FIELD_NAME:
                    continue
                schema = getattr(field, "valueSchema", None)
                schema_json = dump_json(schema)
                if isinstance(schema_json, dict):
                    default_value = schema_json.get("default")
                    raw_code = default_value if isinstance(default_value, str) else None
                break
        code = str(raw_code or "")
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
        f"operatorClass={_TARGET_OPERATOR_CLASS!r}, serviceClass={_TARGET_SERVICE_CLASS!r}"
    )


def load_session_editor_target(session_path: Path) -> SessionEditorTarget:
    return load_session_editor_targets(session_path)[0]


@dataclass
class _DebugSessionState:
    target: SessionEditorTarget
    code: str

    @classmethod
    def from_target(cls, target: SessionEditorTarget) -> _DebugSessionState:
        return cls(target=target, code=target.code)

    def context(self) -> EditorAssistContext:
        return self.target.context


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()
    parser = argparse.ArgumentParser(description="Standalone Monaco editor debug launcher")
    parser.add_argument(
        "--session",
        default=str(_DEFAULT_SESSION_PATH),
        help="Session JSON to inspect for all f8.pyengine / f8.python_script nodes.",
    )
    args = parser.parse_args(argv)

    targets = load_session_editor_targets(Path(str(args.session or _DEFAULT_SESSION_PATH)))
    for target in targets:
        logger.info(
            "Loaded debug session target: session=%s node=%s serviceClass=%s operatorClass=%s",
            target.session_path,
            target.node_id,
            target.spec.serviceClass,
            target.spec.operatorClass,
        )

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([])
        app.setOrganizationName("Feel8")
        app.setApplicationName("F8PyStudio")
    normalize_application_font(app)
    configure_default_webengine_profile()

    states = [_DebugSessionState.from_target(target) for target in targets]
    for state in states:
        session_key = EditorSessionKey.debug_target(
            session_path=state.target.session_path,
            node_id=state.target.node_id,
            field_name=_TARGET_FIELD_NAME,
        )

        def _on_code_saved(code: str, current_state: _DebugSessionState = state) -> None:
            current_state.code = str(code or "")
            logger.info(
                "Debug editor saved code length=%d node=%s field=%s",
                len(current_state.code),
                current_state.target.node_id,
                _TARGET_FIELD_NAME,
            )

        _ = open_code_editor_window(
            None,
            title=f"Monaco Debug - {state.target.node_id}.{_TARGET_FIELD_NAME}",
            code=state.code,
            language="python",
            on_saved=_on_code_saved,
            assist_context=state.context(),
            assist_context_provider=state.context,
            session_key=session_key,
        )

    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
