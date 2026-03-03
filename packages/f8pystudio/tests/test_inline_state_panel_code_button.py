from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from f8pystudio.nodegraph.items.inline_state_panel import make_state_inline_control
from f8pystudio.nodegraph.items.node_item_core import StateFieldInfo
from f8pystudio.nodegraph.items.service_toolbar_host import F8ForceGlobalToolTipFilter


class _FakeBackendNode:
    def __init__(self, props: dict[str, Any]) -> None:
        self._props = dict(props)
        self.spec = None

    def get_property(self, name: str) -> Any:
        return self._props.get(str(name), None)

    def set_property(self, name: str, value: Any, *, push_undo: bool = True) -> None:
        del push_undo
        self._props[str(name)] = value


class _FakeNodeItem:
    def __init__(self, *, code_value: str) -> None:
        self.name = "nodeA"
        self._backend = _FakeBackendNode({"code": code_value})
        self._state_inline_updaters: dict[str, Any] = {}
        self._state_inline_option_pools: dict[str, str] = {}
        self._tooltip_filters: list[Any] = []
        self._open_code_editors: list[QtWidgets.QDialog] = []

    def _schema_enum_items(self, schema: Any) -> list[str]:
        del schema
        return []

    def _schema_numeric_range(self, schema: Any) -> tuple[float | None, float | None]:
        del schema
        return None, None

    def _inline_state_input_is_connected(self, field_name: str) -> bool:
        del field_name
        return False

    def _backend_node(self) -> _FakeBackendNode:
        return self._backend


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _code_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="code",
        label="Code",
        tooltip="Python source code.",
        show_on_node=True,
        access="rw",
        access_str="rw",
        required=True,
        ui_control="code",
        ui_language="python",
        value_schema=None,
    )


def test_make_state_inline_control_code_uses_push_button_and_style() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="a\nb")
    control = make_state_inline_control(node_item, _code_field())

    assert isinstance(control, QtWidgets.QPushButton)
    style = str(control.styleSheet() or "")
    assert "border:" in style
    assert "text-align: center" in style


def test_make_state_inline_control_code_installs_tooltip_filter_and_multiline_tooltip() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="a\nb")
    control = make_state_inline_control(node_item, _code_field())
    assert isinstance(control, QtWidgets.QPushButton)

    assert len(node_item._tooltip_filters) == 1
    tooltip_filter = node_item._tooltip_filters[0]
    assert isinstance(tooltip_filter, F8ForceGlobalToolTipFilter)
    assert tooltip_filter.parent() is control

    assert "2 lines" in str(control.toolTip() or "")

    updater = node_item._state_inline_updaters.get("code")
    assert callable(updater)
    updater("x\ny\nz")
    assert "3 lines" in str(control.toolTip() or "")
