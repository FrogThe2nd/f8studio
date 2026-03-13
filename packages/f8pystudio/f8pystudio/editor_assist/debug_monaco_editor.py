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


def load_session_editor_target(session_path: Path) -> SessionEditorTarget:
    resolved_path = session_path.expanduser().resolve()
    payload = _load_json_file(resolved_path)

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
        return SessionEditorTarget(
            session_path=resolved_path,
            node_id=node_id,
            spec=spec,
            code=code,
            context=context,
        )

    raise ValueError(
        "No matching node found for "
        f"operatorClass={_TARGET_OPERATOR_CLASS!r}, serviceClass={_TARGET_SERVICE_CLASS!r}"
    )


@dataclass
class _DebugSessionState:
    target: SessionEditorTarget
    code: str

    @classmethod
    def from_target(cls, target: SessionEditorTarget) -> _DebugSessionState:
        return cls(target=target, code=target.code)

    def context(self) -> EditorAssistContext:
        return self.target.context


class MonacoEditorDebugLauncher(QtWidgets.QWidget):
    def __init__(self, *, target: SessionEditorTarget) -> None:
        super().__init__()
        self._state = _DebugSessionState.from_target(target)
        self._editor_window: QtWidgets.QDialog | None = None

        self.setWindowTitle("F8 Monaco Editor Debug Launcher")
        self.resize(760, 170)

        self._status_label = QtWidgets.QLabel(self)
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        self._open_button = QtWidgets.QPushButton("Open Monaco Editor", self)
        self._open_button.clicked.connect(self._open_editor)  # type: ignore[attr-defined]

        self._quit_button = QtWidgets.QPushButton("Quit", self)
        self._quit_button.clicked.connect(self.close)  # type: ignore[attr-defined]

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self._open_button)
        button_row.addStretch(1)
        button_row.addWidget(self._quit_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._status_label)
        layout.addLayout(button_row)
        layout.addStretch(1)

        self._refresh_status()
        QtCore.QTimer.singleShot(0, self._open_editor)

    def _refresh_status(self) -> None:
        target = self._state.target
        port_names = ", ".join(port.name for port in target.context.data_in_ports) or "(none)"
        field_names = ", ".join(field.name for field in target.context.state_fields) or "(none)"
        self._status_label.setText(
            f"Session: {target.session_path}\n"
            f"Node: {target.node_id} | {target.spec.serviceClass} / {target.spec.operatorClass}\n"
            f"Editing state field `{_TARGET_FIELD_NAME}` with Monaco + LSP + editorAssist stubs enabled.\n"
            f"dataInPorts: {port_names}\n"
            f"stateFields: {field_names}"
        )

    def _open_editor(self) -> None:
        if self._editor_window is not None and self._editor_window.isVisible():
            self._editor_window.raise_()
            self._editor_window.activateWindow()
            return
        self._editor_window = open_code_editor_window(
            self,
            title=f"Monaco Debug - {self._state.target.node_id}.{_TARGET_FIELD_NAME}",
            code=self._state.code,
            language="python",
            on_saved=self._on_code_saved,
            assist_context=self._state.context(),
            assist_context_provider=self._state.context,
        )
        self._editor_window.destroyed.connect(self._on_editor_destroyed)  # type: ignore[attr-defined]

    def _on_code_saved(self, code: str) -> None:
        self._state.code = str(code or "")
        logger.info(
            "Debug editor saved code length=%d node=%s field=%s",
            len(self._state.code),
            self._state.target.node_id,
            _TARGET_FIELD_NAME,
        )

    @QtCore.Slot()
    def _on_editor_destroyed(self) -> None:
        self._editor_window = None


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()
    parser = argparse.ArgumentParser(description="Standalone Monaco editor debug launcher")
    parser.add_argument(
        "--session",
        default=str(_DEFAULT_SESSION_PATH),
        help="Session JSON to inspect for the first f8.pyengine / f8.python_script node.",
    )
    args = parser.parse_args(argv)

    target = load_session_editor_target(Path(str(args.session or _DEFAULT_SESSION_PATH)))
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
    configure_default_webengine_profile()

    launcher = MonacoEditorDebugLauncher(target=target)
    launcher.show()
    launcher.raise_()
    launcher.activateWindow()

    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
