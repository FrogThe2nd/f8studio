from __future__ import annotations

import json
import logging
import sys
from typing import Any

from f8pysdk import F8DataTypeSchema
from f8pysdk.msgspec_codec import dump_json

from qtpy import QtCore, QtWidgets

from ...editor_assist.protocol import editor_assist_context_for_field
from ...editor_assist.workspace import EditorAssistContext
from ...ui_icons import StudioIcon, icon_for
from ...shared_ui.json_text_editor import attach_json_enhancements
from ...shared_ui.schema_builder_dialog import (
    schema_from_json_obj as _schema_from_json_obj_strict,
    schema_to_json_obj as _schema_to_json_obj_strict,
)
from ..state_controls.readonly_policy import set_widget_read_only as _set_widget_read_only
from ...nodegraph.node_text_fields import resolve_node
from ..state_controls import schema_type_any as _schema_type


logger = logging.getLogger(__name__)

_PROPERTY_PANEL_MIN_WIDTH = 250
_TAB_PANEL_MARGIN = 4
_TAB_PANEL_SPACING = 5
_TAB_HEADER_STYLE = """
QTabWidget#f8NodePropTabs::pane {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    background: rgba(18, 18, 18, 0.92);
    top: -1px;
}
QTabWidget#f8NodePropTabs QTabBar {
    qproperty-drawBase: 0;
}
QTabWidget#f8NodePropTabs QTabBar::tab {
    color: rgba(220, 220, 220, 0.88);
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-bottom-color: rgba(255, 255, 255, 0.03);
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 4px 7px;
    margin-right: 1px;
    margin-top: 1px;
    min-width: 0px;
}
QTabWidget#f8NodePropTabs QTabBar::tab:hover {
    color: rgba(245, 245, 245, 0.96);
    background: rgba(255, 255, 255, 0.09);
    border-color: rgba(255, 255, 255, 0.12);
}
QTabWidget#f8NodePropTabs QTabBar::tab:selected {
    color: rgb(255, 255, 255);
    background: rgba(42, 42, 42, 0.96);
    border-color: rgba(255, 255, 255, 0.14);
    border-bottom-color: rgba(42, 42, 42, 0.96);
    margin-top: 0px;
    padding-top: 5px;
    padding-bottom: 5px;
}
QTabWidget#f8NodePropTabs QTabBar::tab:!selected {
    margin-top: 1px;
}
"""


def _apply_read_only_widget(widget: QtWidgets.QWidget) -> None:
    _set_widget_read_only(widget, read_only=True)


def _set_read_only_widget(widget: QtWidgets.QWidget, *, read_only: bool) -> None:
    _set_widget_read_only(widget, read_only=bool(read_only))


def _wrap_tab_page(content: QtWidgets.QWidget) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget(content.parentWidget())
    layout = QtWidgets.QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    scroll = QtWidgets.QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
    try:
        scroll.viewport().setAutoFillBackground(False)
    except (AttributeError, RuntimeError, TypeError):
        pass
    content.setObjectName("f8TabPageContent")
    content.setStyleSheet("#f8TabPageContent { background: transparent; }")
    scroll.setWidget(content)
    layout.addWidget(scroll)
    return page


def _state_input_is_connected(node: Any, field_name: str) -> bool:
    name = str(field_name or "").strip()
    if not name:
        return False
    p = node.get_input(f"[S]{name}")
    if p is None:
        return False
    try:
        return bool(p.connected_ports())
    except AttributeError as exc:
        logger.warning("state input connection has stale node reference; field=%s err=%s", name, exc)
        try:
            graph = node.graph
        except (AttributeError, RuntimeError, TypeError):
            graph = None
        if graph is None:
            return False
        try:
            valid_ids = {str(n.id or "").strip() for n in list(graph.all_nodes() or []) if str(n.id or "").strip()}
        except (AttributeError, RuntimeError, TypeError):
            return False
        connected = p.model.connected_ports
        stale_ids = [nid for nid in list(connected.keys()) if str(nid or "") not in valid_ids]
        for stale_id in stale_ids:
            connected.pop(stale_id, None)
        return False


def _get_node_spec(node: Any) -> Any | None:
    try:
        return node.spec
    except Exception:
        return None


def _build_editor_assist_context(
    graph: Any, *, node_id: str, prop_name: str, language: str = "python"
) -> EditorAssistContext | None:
    field_name = str(prop_name or "").strip()
    if not field_name:
        return None

    node = resolve_node(graph, node_id)
    if node is None:
        return None
    spec = _get_node_spec(node)
    if spec is None:
        return None
    return editor_assist_context_for_field(
        spec, field_kind="state", field_key=field_name, language=language, node=node
    )


def _node_missing_lock_info(node: Any) -> tuple[bool, str]:
    if node is None:
        return False, ""
    try:
        model = node.model
    except Exception:
        return False, ""
    try:
        f8_sys = model.f8_sys
    except Exception:
        return False, ""
    if not isinstance(f8_sys, dict):
        return False, ""
    missing_locked = bool(f8_sys.get("missingLocked"))
    missing_type = str(f8_sys.get("missingType") or "").strip()
    return missing_locked, missing_type


def _to_jsonable(value: Any) -> Any:
    """
    Best-effort conversion to JSON-serializable primitives (dict/list/str/num/bool/None).
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    # Enum-like: use `.value` if present.
    if not isinstance(value, (bytes, bytearray)):
        try:
            return _to_jsonable(value.value)
        except AttributeError:
            pass
    # msgspec structs.
    try:
        dump = dump_json(value, mode="json")
    except Exception:
        try:
            dump = dump_json(
                value,
            )
        except Exception:
            dump = None
    if dump is not None:
        return _to_jsonable(dump)
    return str(value)


def _schema_to_json_obj(schema: Any) -> Any:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return dict(schema)
    if isinstance(schema, list):
        return list(schema)
    if isinstance(schema, (str, int, float, bool)) or schema is None:
        return schema
    schema_type = _schema_type(schema)
    if schema_type in {"string", "number", "integer", "boolean", "null", "object", "array", "any"}:
        try:
            return _schema_to_json_obj_strict(schema)
        except Exception:
            logger.exception("strict schema_to_json_obj failed for F8DataTypeSchema")
            return None
    try:
        schema_typed = _schema_from_json_obj_strict(schema)
    except Exception:
        schema_typed = None
    if schema_typed is not None:
        try:
            return _schema_to_json_obj_strict(schema_typed)
        except Exception:
            logger.exception("strict schema_to_json_obj failed after coercion")
            return None
    try:
        return dump_json(schema, mode="json")
    except (AttributeError, TypeError, ValueError):
        pass
    return str(schema)


def _schema_from_json_obj(obj: Any) -> F8DataTypeSchema:
    if isinstance(obj, dict):
        return _schema_from_json_obj_strict(obj)

    schema_kind = _schema_type(obj)
    if schema_kind in {"string", "number", "integer", "boolean", "null", "object", "array", "any"}:
        return obj
    return _schema_from_json_obj_strict(obj)


def _package_attr(name: str, default: Any) -> Any:
    package_module = sys.modules.get(__package__)
    if package_module is None:
        return default
    return getattr(package_module, name, default)


class _F8JsonEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str, value: Any):
        super().__init__(parent)
        self.setWindowTitle(title)

        self._edit = QtWidgets.QPlainTextEdit()
        attach_json_enhancements(self._edit, read_only=False)
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = json.dumps(_to_jsonable(value), ensure_ascii=False, indent=2)
        self._edit.setPlainText(text)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._edit, 1)
        layout.addWidget(buttons)

    def value(self) -> Any:
        text = self._edit.toPlainText().strip()
        if not text:
            return None
        return json.loads(text)
