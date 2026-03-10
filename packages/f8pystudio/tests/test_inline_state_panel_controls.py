from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from f8pystudio.nodegraph.items.inline_state_panel import make_state_inline_control, on_graph_property_changed
from f8pystudio.nodegraph.items.node_item_core import StateFieldInfo
from f8pystudio.nodegraph.items.service_toolbar_host import F8ForceGlobalToolTipFilter
from f8pystudio.nodegraph.items.wave_preview import WavePreviewControl


class _FakeBackendNode:
    def __init__(self, props: dict[str, Any]) -> None:
        self._props = dict(props)
        self.spec = None
        self.id = "nodeA"

    def get_property(self, name: str) -> Any:
        return self._props.get(str(name), None)

    def set_property(self, name: str, value: Any, *, push_undo: bool = True) -> None:
        del push_undo
        self._props[str(name)] = value


class _FakeNodeItem:
    def __init__(self, *, code_value: str) -> None:
        self.id = "nodeA"
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


def _wave_preview_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="preview",
        label="Preview",
        tooltip="Preview waveform.",
        show_on_node=True,
        access="ro",
        access_str="ro",
        required=True,
        ui_control="wave_preview",
        ui_language=None,
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


def test_make_state_inline_control_wave_preview_restores_widget() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "express": "0.5 + 0.5 * cos(t)",
            "preview": [[0.0, 0.0], [0.1, 0.5], [0.2, 1.0]],
            "minValue": -1.0,
            "maxValue": 1.0,
            "maxT": 10.0,
        }
    )

    control = make_state_inline_control(node_item, _wave_preview_field())

    assert isinstance(control, WavePreviewControl)


def test_on_graph_property_changed_refreshes_wave_preview_from_bounds_change() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "express": "sin(t)",
            "preview": [[0.0, 0.0], [0.1, 1.0]],
            "minValue": -1.0,
            "maxValue": 1.0,
            "maxT": 4.0,
        }
    )
    seen: list[Any] = []

    def _record_preview(value: Any) -> None:
        seen.append(value)

    node_item._state_inline_updaters["preview"] = _record_preview

    on_graph_property_changed(node_item, node_item._backend, "maxT", 8.0)

    assert seen == [[[0.0, 0.0], [0.1, 1.0]]]


def test_wave_preview_auto_zoom_when_min_gte_max() -> None:
    y_range = WavePreviewControl._coerce_preview_y_range(0.0, 0.0)
    assert y_range is None
