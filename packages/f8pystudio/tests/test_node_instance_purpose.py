from __future__ import annotations

from types import SimpleNamespace

from qtpy import QtCore, QtWidgets

from f8pystudio.nodegraph.node_model import F8StudioNodeModel
import f8pystudio.ui.widgets.node_property_panel.editor as editor_module
from f8pystudio.ui.widgets.node_property_panel.editor import (
    F8StudioSingleNodePropertiesWidget,
    _NodePropEditorViewState,
)


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


class _FakeEditor(QtWidgets.QWidget):
    property_changed = QtCore.Signal(str, str, object)
    property_changing = QtCore.Signal(str, str, object)
    property_closed = QtCore.Signal(str)

    def __init__(self, parent=None, *, node=None):
        super().__init__(parent)
        self.node = node
        self.restored_state: _NodePropEditorViewState | None = None
        self.snapshot_state = _NodePropEditorViewState(
            current_tab=getattr(node, "current_tab", None),
            tab_scroll_positions=dict(getattr(node, "scroll_positions", {})),
        )

    def snapshot_view_state(self) -> _NodePropEditorViewState:
        return self.snapshot_state

    def restore_view_state(self, state: _NodePropEditorViewState | None) -> bool:
        self.restored_state = state
        if state is None:
            return False
        available_tabs = set(getattr(self.node, "available_tabs", []))
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


def test_property_panel_reloads_when_graph_layers_change() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(_editor=SimpleNamespace(reload=lambda: seen.append("reload")))

    F8StudioSingleNodePropertiesWidget._on_graph_layers_changed(fake_self)

    assert seen == ["reload"]


def test_property_panel_keeps_same_tab_and_scroll_when_switching_nodes(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(editor_module, "F8StudioNodePropEditorWidget", _FakeEditor)

    widget = F8StudioSingleNodePropertiesWidget(node_graph=_FakeGraph())

    node_a = SimpleNamespace(
        id="nodeA",
        current_tab="Node",
        scroll_positions={"Node": 128},
        available_tabs=["State", "Node"],
    )
    node_b = SimpleNamespace(
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
