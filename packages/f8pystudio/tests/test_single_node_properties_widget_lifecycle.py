from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtWidgets

from f8pystudio.widgets import node_property_widgets as npw


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeNode:
    def __init__(self, node_id: str) -> None:
        self.id = str(node_id or "")
        self.set_calls: list[tuple[str, Any, bool]] = []

    def set_property(self, name: str, value: Any, *, push_undo: bool = True) -> None:
        self.set_calls.append((str(name or ""), value, bool(push_undo)))


class _FakeGraph(QtCore.QObject):
    node_selected = QtCore.Signal(object)
    node_double_clicked = QtCore.Signal(object)
    node_selection_changed = QtCore.Signal(object, object)
    nodes_deleted = QtCore.Signal(object)
    property_changed = QtCore.Signal(object, str, object)
    port_connected = QtCore.Signal(object, object)
    port_disconnected = QtCore.Signal(object, object)

    def __init__(self, *, nodes: list[_FakeNode]) -> None:
        super().__init__()
        self._by_id = {str(node.id): node for node in nodes}
        self._selected: list[_FakeNode] = []

    def get_node_by_id(self, node_id: str) -> _FakeNode | None:
        return self._by_id.get(str(node_id or ""))

    def selected_nodes(self) -> list[_FakeNode]:
        return list(self._selected)

    def set_selected_nodes(self, nodes: list[_FakeNode]) -> None:
        self._selected = list(nodes)


class _FakeEditor(QtWidgets.QWidget):
    property_changed = QtCore.Signal(str, str, object)
    property_changing = QtCore.Signal(str, str, object)
    property_closed = QtCore.Signal(str)

    def __init__(self, parent=None, node=None) -> None:
        super().__init__(parent)
        self.bind_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.capture_state: dict[str, Any] = {"active_tab": "", "scroll_pos": {}}
        self._node_id = ""
        if node is not None:
            try:
                self._node_id = str(node.id or "")
            except Exception:
                self._node_id = ""

    def bind_node(self, node: Any, *, view_state: dict[str, Any] | None = None) -> None:
        self._node_id = str(getattr(node, "id", "") or "")
        self.bind_calls.append((self._node_id, view_state))

    def capture_view_state(self) -> dict[str, Any]:
        return dict(self.capture_state)

    def get_widget(self, _name: str) -> Any | None:
        return None


def test_single_node_panel_reuses_single_editor_instance(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(npw, "F8StudioNodePropEditorWidget", _FakeEditor)
    node_a = _FakeNode("a")
    node_b = _FakeNode("b")
    graph = _FakeGraph(nodes=[node_a, node_b])
    panel = npw.F8StudioSingleNodePropertiesWidget(node_graph=graph)

    panel.set_node(node_a)
    first_editor = panel._editor
    assert first_editor is not None
    panel.set_node(node_b)
    assert panel._editor is first_editor
    assert isinstance(panel._editor, _FakeEditor)
    assert panel._editor.bind_calls == [("a", None), ("b", None)]


def test_single_node_panel_property_changed_still_writes_back(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(npw, "F8StudioNodePropEditorWidget", _FakeEditor)
    node_a = _FakeNode("a")
    graph = _FakeGraph(nodes=[node_a])
    panel = npw.F8StudioSingleNodePropertiesWidget(node_graph=graph)

    panel.set_node(node_a)
    assert panel._editor is not None
    panel._editor.property_changed.emit("a", "foo", 123)

    assert node_a.set_calls == [("foo", 123, True)]


def test_single_node_panel_restores_view_state_per_node(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(npw, "F8StudioNodePropEditorWidget", _FakeEditor)
    node_a = _FakeNode("a")
    node_b = _FakeNode("b")
    graph = _FakeGraph(nodes=[node_a, node_b])
    panel = npw.F8StudioSingleNodePropertiesWidget(node_graph=graph)

    panel.set_node(node_a)
    assert isinstance(panel._editor, _FakeEditor)
    panel._editor.capture_state = {"active_tab": "Port", "scroll_pos": {"Port": 42}}

    panel.set_node(node_b)
    panel._editor.capture_state = {"active_tab": "State", "scroll_pos": {"State": 9}}

    panel.set_node(node_a)
    assert panel._editor.bind_calls[-1] == ("a", {"active_tab": "Port", "scroll_pos": {"Port": 42}})


def test_single_node_panel_does_not_spawn_visible_top_level_windows_on_switch(monkeypatch) -> None:
    app = _ensure_app()
    monkeypatch.setattr(npw, "F8StudioNodePropEditorWidget", _FakeEditor)
    node_a = _FakeNode("a")
    node_b = _FakeNode("b")
    graph = _FakeGraph(nodes=[node_a, node_b])
    panel = npw.F8StudioSingleNodePropertiesWidget(node_graph=graph)

    before_ids = {id(widget) for widget in app.topLevelWidgets() if widget.isVisible()}
    panel.set_node(node_a)
    panel.set_node(node_b)
    panel.set_node(node_a)
    app.processEvents()
    after_ids = {id(widget) for widget in app.topLevelWidgets() if widget.isVisible()}
    assert after_ids - before_ids == set()


def test_get_property_editor_widget_returns_current_editor_for_selected_node(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(npw, "F8StudioNodePropEditorWidget", _FakeEditor)
    node_a = _FakeNode("a")
    node_b = _FakeNode("b")
    graph = _FakeGraph(nodes=[node_a, node_b])
    panel = npw.F8StudioSingleNodePropertiesWidget(node_graph=graph)

    panel.set_node(node_a)
    editor = panel.get_property_editor_widget(node_a)
    assert editor is panel._editor
    assert panel.get_property_editor_widget(node_b) is None

