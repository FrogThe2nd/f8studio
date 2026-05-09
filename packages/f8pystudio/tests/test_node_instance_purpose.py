from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from qtpy import QtCore, QtTest, QtWidgets

from f8pystudio.nodegraph.node_model import F8StudioNodeModel
from f8pystudio.ui.support.qt_lifecycle import qt_object_is_valid
import f8pystudio.ui.widgets.node_property_panel.editor as editor_module
from f8pystudio.ui.widgets.node_property_panel.editor import (
    F8StudioSingleNodePropertiesWidget,
    _NodePropEditorViewState,
)
from f8pystudio.ui.widgets.node_property_panel.editor_view_state_mixin import NodePropertyEditorViewStateMixin
from f8pystudio.ui.widgets.node_property_panel.graph_sync_mixin import NodePropertyPanelGraphSyncMixin
from f8pystudio.ui.widgets.node_property_panel.containers import _F8ReorderList


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _Signal:
    def connect(self, _callback) -> None:
        return


class _FakeGraph:
    def __init__(self) -> None:
        self.node_selected = _Signal()
        self.node_double_clicked = _Signal()
        self.node_selection_changed = _Signal()
        self.nodes_deleted = _Signal()
        self.property_changed = _Signal()
        self.layers_changed = _Signal()
        self.port_connected = _Signal()
        self.port_disconnected = _Signal()

    def selected_nodes(self):
        return []

    def get_node_by_id(self, _node_id: str):
        return None


@dataclass
class _FakeNode:
    id: str
    current_tab: str | None = None
    scroll_positions: dict[str, int] = field(default_factory=dict)
    available_tabs: list[str] = field(default_factory=list)
    ui: dict[str, object] = field(default_factory=dict)

    def ui_overrides(self) -> dict[str, object]:
        return self.ui


class _FakeEditor(QtWidgets.QWidget):
    property_changed = QtCore.Signal(str, str, object)
    property_changing = QtCore.Signal(str, str, object)
    property_closed = QtCore.Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        node=None,
        inspect_mode: bool = False,
        outer_scroll_getter=None,
        outer_scroll_restorer=None,
    ):
        super().__init__(parent)
        self.node = node
        self.inspect_mode = bool(inspect_mode)
        self.outer_scroll_getter = outer_scroll_getter
        self.outer_scroll_restorer = outer_scroll_restorer
        self.restored_state: _NodePropEditorViewState | None = None
        current_tab = None if node is None else node.current_tab
        scroll_positions = {} if node is None else node.scroll_positions
        self.snapshot_state = _NodePropEditorViewState(
            current_tab=current_tab,
            tab_scroll_positions=dict(scroll_positions),
        )

    def snapshot_view_state(self) -> _NodePropEditorViewState:
        return self.snapshot_state

    def restore_view_state(self, state: _NodePropEditorViewState | None) -> bool:
        self.restored_state = state
        if state is None:
            return False
        available_tabs = set([] if self.node is None else self.node.available_tabs)
        return bool(state.current_tab) and state.current_tab in available_tabs


def test_node_model_node_purpose_round_trips_through_f8_sys() -> None:
    model = F8StudioNodeModel()

    model.nodePurpose = "  Map the unpacked payload into canonical fields.  "

    assert model.nodePurpose == "Map the unpacked payload into canonical fields."
    assert model.f8_sys["nodePurpose"] == "Map the unpacked payload into canonical fields."


def test_node_model_set_property_normalizes_node_purpose() -> None:
    model = F8StudioNodeModel()

    model.set_property("nodePurpose", "  Extract skeleton joints  ")

    assert model.nodePurpose == "Extract skeleton joints"


def test_node_model_emits_graph_property_changed_for_f8_ui_state() -> None:
    seen: list[tuple[object, str, object]] = []
    model = F8StudioNodeModel()
    owner = SimpleNamespace()
    owner.graph = SimpleNamespace(
        property_changed=SimpleNamespace(emit=lambda node, name, value: seen.append((node, name, value)))
    )
    model._owner_node = owner

    model.set_property("f8_ui_state", {"stateFieldHotkeys": {"trigger": "F10"}})

    assert seen == [(owner, "f8_ui_state", {"stateFieldHotkeys": {"trigger": "F10"}})]


def test_node_model_skips_system_property_emit_when_graph_is_none() -> None:
    model = F8StudioNodeModel()
    model._owner_node = SimpleNamespace(graph=None)

    model.set_property("f8_ui_state", {"stateFieldHotkeys": {"trigger": "F10"}})


def test_property_panel_reloads_when_system_spec_changes() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(
        _editor=SimpleNamespace(reload=lambda: seen.append("reload")),
        _node_id="nodeA",
    )
    node = SimpleNamespace(id="nodeA")

    F8StudioSingleNodePropertiesWidget._on_graph_property_changed(fake_self, node, "f8_spec", {"stateFields": []})

    assert seen == ["reload"]


def test_property_panel_skips_reload_for_order_and_visibility_ui_override_change() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(
        _editor=SimpleNamespace(reload=lambda: seen.append("reload")),
        _node_id="nodeA",
        _last_ui_overrides_reload_fingerprint=NodePropertyPanelGraphSyncMixin._ui_overrides_reload_fingerprint({}),
    )
    node = SimpleNamespace(id="nodeA")

    F8StudioSingleNodePropertiesWidget._on_graph_property_changed(
        fake_self,
        node,
        "f8_ui_overrides",
        {
            "listOrder": {"stateFields": ["gain", "mode"]},
            "stateFields": {"mode": {"showOnNode": True}},
            "dataPorts": {"in": {"image": {"showOnNode": False}}},
            "commands": {"run": {"showOnNode": True}},
        },
    )

    assert seen == []


def test_property_panel_reloads_for_structural_ui_override_change() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(
        _editor=SimpleNamespace(reload=lambda: seen.append("reload")),
        _node_id="nodeA",
        _last_ui_overrides_reload_fingerprint=NodePropertyPanelGraphSyncMixin._ui_overrides_reload_fingerprint({}),
    )
    node = SimpleNamespace(id="nodeA")

    F8StudioSingleNodePropertiesWidget._on_graph_property_changed(
        fake_self,
        node,
        "f8_ui_overrides",
        {"stateFields": {"mode": {"showOnNode": True, "uiControl": "select[allModes]"}}},
    )

    assert seen == ["reload"]


def test_property_panel_reloads_when_graph_layers_change() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(_editor=SimpleNamespace(reload=lambda: seen.append("reload")))

    F8StudioSingleNodePropertiesWidget._on_graph_layers_changed(fake_self)

    assert seen == ["reload"]


def test_property_panel_keeps_same_tab_and_scroll_when_switching_nodes(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(editor_module, "F8StudioNodePropEditorWidget", _FakeEditor)

    widget = F8StudioSingleNodePropertiesWidget(node_graph=_FakeGraph())

    node_a = _FakeNode(
        id="nodeA",
        current_tab="Node",
        scroll_positions={"Node": 128},
        available_tabs=["State", "Node"],
    )
    node_b = _FakeNode(
        id="nodeB",
        current_tab="State",
        scroll_positions={},
        available_tabs=["State", "Node"],
    )

    widget.set_node(node_a)
    assert isinstance(widget._editor, _FakeEditor)
    widget._scroll.verticalScrollBar().setValue(37)
    previous_scroll_value = widget._scroll.verticalScrollBar().value()

    widget.set_node(node_b)
    QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()

    assert isinstance(widget._editor, _FakeEditor)
    assert widget._editor.restored_state == _NodePropEditorViewState(
        current_tab="Node",
        tab_scroll_positions={"Node": 128},
    )
    assert widget._scroll.verticalScrollBar().value() == previous_scroll_value


def test_property_editor_reload_preserves_outer_scroll_callback() -> None:
    events: list[object] = []

    class _FakeReloadNode:
        def sync_from_spec(self) -> None:
            events.append("sync")

    class _FakeMissingBanner:
        def setVisible(self, value: bool) -> None:
            events.append(("missing_visible", bool(value)))

        def setText(self, value: str) -> None:
            events.append(("missing_text", value))

    class _FakeReloadHost:
        _reload_pending = True
        _node = _FakeReloadNode()
        _F8StudioNodePropEditorWidget__tab_windows: dict[str, object] = {"State": object()}
        _option_pool_dependents: dict[str, list[object]] = {"mode": []}
        _missing_banner = _FakeMissingBanner()

        def snapshot_view_state(self) -> str:
            events.append("snapshot_view")
            return "view-state"

        def snapshot_outer_scroll_position(self) -> int:
            events.append("snapshot_outer")
            return 73

        def _clear_tabs(self) -> None:
            events.append("clear_tabs")

        def _read_node(self, node: object) -> str:
            events.append(("read_node", node))
            return "ports"

        def _apply_missing_lock_read_only(self) -> None:
            events.append("missing_lock")

        def restore_view_state(self, state: object) -> bool:
            events.append(("restore_view", state))
            return True

        def restore_outer_scroll_position_later(self, value: int | None) -> None:
            events.append(("restore_outer", value))

    host = _FakeReloadHost()

    NodePropertyEditorViewStateMixin._reload_now(host)  # type: ignore[arg-type]

    assert host._reload_pending is False
    assert host._F8StudioNodePropEditorWidget__tab_windows == {}
    assert host._option_pool_dependents == {}
    assert "snapshot_outer" in events
    assert ("restore_outer", 73) in events


def test_restore_view_state_retries_tab_scroll_after_layout_updates() -> None:
    _ensure_app()
    tab_widget = QtWidgets.QTabWidget()
    page = QtWidgets.QWidget(tab_widget)
    page_layout = QtWidgets.QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    scroll = QtWidgets.QScrollArea(page)
    scroll.setWidgetResizable(True)
    content = QtWidgets.QWidget(scroll)
    content.setMinimumHeight(10)
    scroll.setWidget(content)
    page_layout.addWidget(scroll)
    tab_widget.addTab(page, "State")
    tab_widget.resize(260, 140)
    tab_widget.show()
    QtWidgets.QApplication.processEvents()

    class _RestoreHost(NodePropertyEditorViewStateMixin):
        def __init__(self) -> None:
            self._F8StudioNodePropEditorWidget__tab = tab_widget

    host = _RestoreHost()
    state = _NodePropEditorViewState(current_tab="State", tab_scroll_positions={"State": 180})

    NodePropertyEditorViewStateMixin.restore_view_state(host, state)  # type: ignore[arg-type]
    content.setMinimumHeight(900)
    content.updateGeometry()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.qWait(80)
    QtWidgets.QApplication.processEvents()

    assert scroll.verticalScrollBar().value() == min(180, scroll.verticalScrollBar().maximum())

    tab_widget.close()
    tab_widget.deleteLater()


def test_restore_tab_scroll_positions_ignores_deleted_tab_widget() -> None:
    _ensure_app()
    tab_widget = QtWidgets.QTabWidget()
    tab_widget.show()
    tab_widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, int(QtCore.QEvent.Type.DeferredDelete))
    QtWidgets.QApplication.processEvents()

    assert qt_object_is_valid(tab_widget) is False
    NodePropertyEditorViewStateMixin._restore_tab_scroll_positions(tab_widget, {"State": 180})


def test_reorder_list_drop_indicator_does_not_affect_rows_or_order() -> None:
    _ensure_app()
    reorder_list = _F8ReorderList()
    rows = []
    for name in ("alpha", "beta", "gamma"):
        row = QtWidgets.QLabel(name)
        row.setProperty("_order_key", name)
        reorder_list.add_row(row)
        rows.append(row)

    reorder_list._show_drop_indicator(1)

    assert reorder_list._drop_indicator.isHidden() is False
    assert reorder_list.rows() == rows
    assert reorder_list.order_keys() == ["alpha", "beta", "gamma"]

    reorder_list._hide_drop_indicator()

    assert reorder_list._drop_indicator.isHidden() is True
    assert reorder_list.rows() == rows


