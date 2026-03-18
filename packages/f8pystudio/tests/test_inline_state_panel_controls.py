from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtGui, QtWidgets
from NodeGraphQt.custom_widgets.properties_bin.node_property_factory import NodePropertyWidgetFactory

from f8pysdk import F8StateAccess, F8StateSpec, integer_schema, string_schema
from f8pystudio.nodegraph.items.state_inline_controls import (
    build_state_inline_control,
    sync_state_inline_controls_from_graph_property,
)
from f8pystudio.nodegraph.items.node_item_core import StateFieldInfo
from f8pystudio.nodegraph.items.service_toolbar_host import F8ForceGlobalToolTipFilter
from f8pystudio.widgets.editor_controls import F8OptionCombo
from f8pystudio.widgets.state_value_controls import F8IncrementButtonEditor
from f8pystudio.widgets.state_controls import build_state_panel_control
from f8pystudio.widgets.value_controls.wave_controls import (
    WaveHeatmapControl,
    WavePatternEditorControl,
    WavePreviewControl,
    graph_draw_rect,
    point_to_widget_pos,
)


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

    def _is_state_inline_input_connected(self, field_name: str) -> bool:
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




def _wave_heatmap_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="heatmap",
        label="Heatmap",
        tooltip="Wave heatmap.",
        show_on_node=True,
        access="ro",
        access_str="ro",
        required=True,
        ui_control="wave_heatmap",
        ui_language=None,
        value_schema=None,
    )


def _selected_axis_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="selectedAxis",
        label="Selected Axis",
        tooltip="Axis selector.",
        show_on_node=True,
        access="rw",
        access_str="rw",
        required=True,
        ui_control="options:[allAxes]",
        ui_language=None,
        value_schema=None,
    )

def _wave_pattern_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="points",
        label="Points",
        tooltip="Editable control points.",
        show_on_node=True,
        access="rw",
        access_str="rw",
        required=True,
        ui_control="wave_pattern_editor",
        ui_language=None,
        value_schema=None,
    )


def _button_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="playTrigger",
        label="Play",
        tooltip="Increment to trigger playback.",
        show_on_node=True,
        access="rw",
        access_str="rw",
        required=True,
        ui_control="button",
        ui_language=None,
        value_schema=integer_schema(),
    )


def _invalid_button_field() -> StateFieldInfo:
    return StateFieldInfo(
        name="badTrigger",
        label="Bad",
        tooltip="Wrong schema for button.",
        show_on_node=True,
        access="rw",
        access_str="rw",
        required=True,
        ui_control="button",
        ui_language=None,
        value_schema=string_schema(),
    )


class _FakePropertyNode:
    def __init__(self, field: F8StateSpec) -> None:
        self._field = field

    def effective_state_fields(self) -> list[F8StateSpec]:
        return [self._field]


def _mouse_event(
    event_type: QtCore.QEvent.Type,
    pos: QtCore.QPointF,
    *,
    button: QtCore.Qt.MouseButton,
    buttons: QtCore.Qt.MouseButton,
) -> QtGui.QMouseEvent:
    return QtGui.QMouseEvent(
        event_type,
        pos,
        pos,
        pos,
        button,
        buttons,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )


def test_build_state_inline_control_code_uses_push_button_and_style() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="a\nb")
    control = build_state_inline_control(node_item, _code_field())

    assert isinstance(control, QtWidgets.QPushButton)
    style = str(control.styleSheet() or "")
    assert "border:" in style
    assert "text-align: center" in style


def test_build_state_inline_control_code_installs_tooltip_filter_and_multiline_tooltip() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="a\nb")
    control = build_state_inline_control(node_item, _code_field())
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


def test_build_state_inline_control_wave_preview_restores_widget() -> None:
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

    control = build_state_inline_control(node_item, _wave_preview_field())

    assert isinstance(control, WavePreviewControl)




def test_build_state_inline_control_wave_heatmap_restores_widget() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "heatmap": [0.0, 0.5, 1.0],
            "maxT": 12.0,
        }
    )

    control = build_state_inline_control(node_item, _wave_heatmap_field())

    assert isinstance(control, WaveHeatmapControl)


def test_build_state_inline_control_selected_axis_uses_option_pool() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "selectedAxis": "TopLevel",
            "allAxes": ["TopLevel", "L1", "R1"],
        }
    )

    control = build_state_inline_control(node_item, _selected_axis_field())

    assert isinstance(control, F8OptionCombo)


def test_build_state_inline_control_button_increments_integer_value() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode({"playTrigger": 0})

    control = build_state_inline_control(node_item, _button_field())

    assert isinstance(control, F8IncrementButtonEditor)
    assert control.text() == "Play"
    control.click()
    assert node_item._backend.get_property("playTrigger") == 1
    control.click()
    assert node_item._backend.get_property("playTrigger") == 2


def test_build_state_inline_control_button_disables_non_numeric_schema() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode({"badTrigger": "abc"})

    control = build_state_inline_control(node_item, _invalid_button_field())

    assert isinstance(control, F8IncrementButtonEditor)
    assert not control.isEnabled()
    assert "integer or number" in str(control.toolTip() or "")


def test_build_state_panel_control_button_uses_field_label_and_increments() -> None:
    _ensure_app()
    field = F8StateSpec(
        name="playTrigger",
        label="Play",
        valueSchema=integer_schema(),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    node = _FakePropertyNode(field)
    widget = build_state_panel_control(
        node=node,
        prop_name="playTrigger",
        widget_type=1,
        widget_factory=NodePropertyWidgetFactory(),
    )

    assert isinstance(widget, F8IncrementButtonEditor)
    assert widget.text() == "Play"
    seen: list[object] = []
    widget.value_changed.connect(lambda _name, value: seen.append(value))  # type: ignore[attr-defined]
    widget.click()
    widget.click()
    assert seen == [1, 2]


def test_option_combo_read_only_toggle_does_not_call_qlineedit_text_interaction_flags() -> None:
    _ensure_app()
    control = F8OptionCombo()
    control.set_options(["TopLevel", "L1", "R1"])
    control.set_value("TopLevel")

    control.set_read_only(True)

    assert control.isEditable()
    line_edit = control.lineEdit()
    assert line_edit is not None
    assert line_edit.isReadOnly()

    control.set_read_only(False)

    assert not control.isEditable()


def test_sync_state_inline_controls_from_graph_property_updates_wave_heatmap() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    seen: list[Any] = []

    def _record_heatmap(value: Any) -> None:
        seen.append(value)

    node_item._state_inline_updaters["heatmap"] = _record_heatmap
    sync_state_inline_controls_from_graph_property(node_item, node_item._backend, "heatmap", [0.0, 1.0, 0.0])

    assert seen == [[0.0, 1.0, 0.0]]

def test_build_state_inline_control_wave_pattern_restores_widget() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "points": [[0.0, 0.0], [10.0, 0.0]],
            "preview": [[0.0, 0.0], [1.0, 0.1], [2.0, 0.0]],
            "minValue": 0.0,
            "maxValue": 1.0,
            "maxT": 10.0,
        }
    )

    control = build_state_inline_control(node_item, _wave_pattern_field())

    assert isinstance(control, WavePatternEditorControl)


def test_sync_state_inline_controls_from_graph_property_refreshes_wave_preview_from_bounds_change() -> None:
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

    sync_state_inline_controls_from_graph_property(node_item, node_item._backend, "maxT", 8.0)

    assert seen == [[[0.0, 0.0], [0.1, 1.0]]]


def test_sync_state_inline_controls_from_graph_property_refreshes_wave_pattern_from_preview_dependency() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "points": [[0.0, 0.0], [10.0, 0.0]],
            "preview": [[0.0, 0.0], [0.1, 1.0]],
            "minValue": 0.0,
            "maxValue": 1.0,
            "maxT": 10.0,
        }
    )
    seen: list[Any] = []

    def _record_points(value: Any) -> None:
        seen.append(value)

    node_item._state_inline_updaters["points"] = _record_points

    sync_state_inline_controls_from_graph_property(node_item, node_item._backend, "preview", [[0.0, 0.0], [0.2, 0.9]])

    assert seen == [[[0.0, 0.0], [10.0, 0.0]]]


def test_wave_pattern_editor_preserves_hidden_points_when_max_t_shrinks() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "points": [[1.0, 0.1], [6.0, 0.6], [12.0, 1.2]],
            "preview": [[0.0, 0.1], [2.5, 0.1]],
            "minValue": 0.0,
            "maxValue": 2.0,
            "maxT": 12.0,
        }
    )

    control = build_state_inline_control(node_item, _wave_pattern_field())
    assert isinstance(control, WavePatternEditorControl)

    node_item._backend.set_property("maxT", 5.0)
    sync_state_inline_controls_from_graph_property(node_item, node_item._backend, "maxT", 5.0)

    assert node_item._backend.get_property("points") == [[1.0, 0.1], [6.0, 0.6], [12.0, 1.2]]

def test_wave_pattern_editor_add_move_delete_updates_backend_points() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(code_value="")
    node_item._backend = _FakeBackendNode(
        {
            "points": [[0.0, 0.0], [10.0, 0.0]],
            "preview": [[0.0, 0.0], [5.0, 0.5], [9.0, 0.0]],
            "minValue": 0.0,
            "maxValue": 1.0,
            "maxT": 10.0,
        }
    )

    control = build_state_inline_control(node_item, _wave_pattern_field())
    assert isinstance(control, WavePatternEditorControl)
    control.resize(240, 84)

    add_pos = QtCore.QPointF(120.0, 18.0)
    control.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress,
            add_pos,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    control.mouseReleaseEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonRelease,
            add_pos,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.NoButton,
        )
    )

    points_after_add = node_item._backend.get_property("points")
    assert len(points_after_add) == 3

    rect = graph_draw_rect(control.rect())
    move_from = point_to_widget_pos(points_after_add[1][0], points_after_add[1][1], rect=rect, max_t=10.0, y_range=(0.0, 1.0))
    move_to = QtCore.QPointF(move_from.x() + 20.0, move_from.y() + 18.0)
    control.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress,
            move_from,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    control.mouseMoveEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseMove,
            move_to,
            button=QtCore.Qt.MouseButton.NoButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    control.mouseReleaseEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonRelease,
            move_to,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.NoButton,
        )
    )

    points_after_move = node_item._backend.get_property("points")
    assert points_after_move[1][0] > points_after_add[1][0]
    assert points_after_move[1][1] < points_after_add[1][1]

    moved_pos = point_to_widget_pos(points_after_move[1][0], points_after_move[1][1], rect=rect, max_t=10.0, y_range=(0.0, 1.0))
    control.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress,
            moved_pos,
            button=QtCore.Qt.MouseButton.RightButton,
            buttons=QtCore.Qt.MouseButton.RightButton,
        )
    )

    points_after_delete = node_item._backend.get_property("points")
    assert len(points_after_delete) == 2


def test_wave_preview_auto_zoom_when_min_gte_max() -> None:
    y_range = WavePreviewControl._coerce_preview_y_range(0.0, 0.0)
    assert y_range is None
