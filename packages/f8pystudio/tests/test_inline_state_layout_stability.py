from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from qtpy import QtCore, QtWidgets
from f8pysdk.schema_helpers import string_schema

from f8pystudio.nodegraph.items import inline_state_panel as isp


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _InlineBackendNode:
    def __init__(self) -> None:
        self.id = "node.inline"
        self._ui: dict[str, Any] = {}
        self._fields = [
            SimpleNamespace(
                name="code",
                showOnNode=True,
                label="Code",
                description="Code",
                uiControl="",
                uiLanguage="",
                valueSchema=string_schema(),
                access="rw",
                required=False,
            )
        ]

    def effective_state_fields(self) -> list[Any]:
        return list(self._fields)

    @property
    def spec(self) -> Any:
        return SimpleNamespace(stateFields=list(self._fields))

    def ui_overrides(self) -> dict[str, Any]:
        return dict(self._ui)

    def get_property(self, name: str) -> Any:
        del name
        return ""


class _InlineNodeItemStub(QtWidgets.QGraphicsRectItem):
    def __init__(self, backend_node: _InlineBackendNode) -> None:
        super().__init__(0.0, 0.0, 240.0, 120.0)
        self._backend = backend_node
        self._state_inline_proxies: dict[str, QtWidgets.QGraphicsProxyWidget] = {}
        self._state_inline_controls: dict[str, QtWidgets.QWidget] = {}
        self._state_inline_updaters: dict[str, Any] = {}
        self._state_inline_toggles: dict[str, QtWidgets.QToolButton] = {}
        self._state_inline_headers: dict[str, QtWidgets.QWidget] = {}
        self._state_inline_bodies: dict[str, QtWidgets.QWidget] = {}
        self._state_inline_expanded: dict[str, bool] = {}
        self._state_inline_option_pools: dict[str, str] = {}
        self._state_inline_ctrl_serial: dict[str, str] = {}
        self._tooltip_filters: list[QtCore.QObject] = []
        self._open_code_editors: list[QtWidgets.QDialog] = []

    def _ensure_graph_property_hook(self) -> None:
        return

    def _backend_node(self) -> _InlineBackendNode:
        return self._backend

    def _schema_enum_items(self, value_schema: Any) -> list[str]:
        del value_schema
        return []

    def _schema_numeric_range(self, value_schema: Any) -> tuple[float | None, float | None]:
        del value_schema
        return None, None

    def _make_state_inline_control(self, info: Any, *, parent: QtWidgets.QWidget | None = None) -> QtWidgets.QWidget:
        del info
        return QtWidgets.QLineEdit(parent)

    def _select_node_from_embedded_widget(self) -> None:
        return

    def _on_state_toggle(self, name: str, expanded: bool) -> None:
        del name, expanded
        return


def test_ensure_inline_state_widgets_reports_layout_dirty_and_visibility_changes() -> None:
    _ensure_app()
    scene = QtWidgets.QGraphicsScene()
    backend = _InlineBackendNode()
    item = _InlineNodeItemStub(backend)
    scene.addItem(item)

    first = isp.ensure_inline_state_widgets(item)
    second = isp.ensure_inline_state_widgets(item)
    assert first is True
    assert second is False
    assert item._state_inline_bodies["code"].isVisible() is False

    item._state_inline_expanded["code"] = True
    third = isp.ensure_inline_state_widgets(item)
    fourth = isp.ensure_inline_state_widgets(item)
    assert third is True
    assert fourth is False
    assert item._state_inline_bodies["code"].isVisible() is True
