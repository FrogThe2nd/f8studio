from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pystudio.nodegraph.node_base import F8StudioBaseNode
from f8pystudio.nodegraph.backdrop_nodeitem import F8StudioBackdropNodeItem
from f8pystudio.nodegraph.container_basenode import F8StudioContainerNodeItem
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeDoubleClickEvent:
    def __init__(self, scene_pos: QtCore.QPointF) -> None:
        self._scene_pos = scene_pos
        self.ignored = False

    def button(self) -> QtCore.Qt.MouseButton:
        return QtCore.Qt.LeftButton

    def scenePos(self) -> QtCore.QPointF:
        return self._scene_pos

    def ignore(self) -> None:
        self.ignored = True


class _FakeView:
    def __init__(self, node_id: str, *, container_item: Any | None, selected: bool = False) -> None:
        self.id = node_id
        self._container_item = container_item
        self.selected = selected


class _FakeUndoStack:
    def __init__(self) -> None:
        self.macros: list[str] = []
        self.commands: list[Any] = []

    def beginMacro(self, label: str) -> None:
        self.macros.append(label)

    def push(self, command: Any) -> None:
        self.commands.append(command)

    def endMacro(self) -> None:
        self.macros.append("end")


class _FakeGraphNode:
    def __init__(self, view: Any, *, x: float, y: float) -> None:
        self.view = view
        self.model = SimpleNamespace(pos=[x, y])

    def pos(self) -> list[float]:
        return list(self.model.pos)


class _FakeModel:
    def __init__(self) -> None:
        self.properties: dict[str, object] = {"disabled": False, "name": ""}
        self.custom_properties: dict[str, object] = {}
        self.f8_sys: dict[str, object] = {}
        self.f8_ui_overrides: dict[str, object] = {}
        self.f8_ui_state: dict[str, object] = {}
        self.set_calls: list[tuple[str, object]] = []

    def set_property(self, name: str, value: object) -> None:
        self.set_calls.append((name, value))
        self.properties[name] = value


class _FakePersistentNode:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, object, bool]] = []

    def set_property(self, name: str, value: object, push_undo: bool = True) -> None:
        self.set_calls.append((str(name), value, bool(push_undo)))


def test_filter_redundant_container_child_moves_drops_child_when_container_also_moved() -> None:
    container_view = _FakeView("svc", container_item=None)
    child_view = _FakeView("op", container_item=container_view)
    node_data = {
        container_view: [10.0, 20.0],
        child_view: [30.0, 40.0],
    }

    filtered = F8StudioGraph._filter_redundant_container_child_moves(node_data)

    assert filtered == {container_view: [10.0, 20.0]}


def test_on_nodes_moved_pushes_only_container_command_for_nested_selection() -> None:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._undo_stack = _FakeUndoStack()

    container_view = _FakeView("svc", container_item=None)
    child_view = _FakeView("op", container_item=container_view)
    container_node = _FakeGraphNode(container_view, x=100.0, y=120.0)
    child_node = _FakeGraphNode(child_view, x=140.0, y=180.0)
    graph._model = SimpleNamespace(
        nodes={
            "svc": container_node,
            "op": child_node,
        }
    )

    graph._on_nodes_moved(
        {
            child_view: [30.0, 40.0],
            container_view: [10.0, 20.0],
        }
    )

    assert graph._undo_stack.macros == ["move nodes", "end"]
    assert len(graph._undo_stack.commands) == 1
    command = graph._undo_stack.commands[0]
    assert command.node is container_node
    assert command.prev_pos == [10.0, 20.0]
    assert command.pos == [100.0, 120.0]


def test_child_views_to_translate_during_drag_skips_selected_children() -> None:
    selected_child = _FakeView("selected", container_item=None, selected=True)
    unselected_child = _FakeView("plain", container_item=None, selected=False)
    container_item = SimpleNamespace(selected=True, _child_views=[selected_child, unselected_child])

    result = F8StudioContainerNodeItem._child_views_to_translate_during_drag(container_item)

    assert result == [unselected_child]


def test_update_model_does_not_persist_container_forced_child_disabled() -> None:
    node = F8StudioBaseNode.__new__(F8StudioBaseNode)
    model = _FakeModel()
    node._model = model
    node._view = SimpleNamespace(
        properties={"disabled": True, "name": "Child"},
        widgets={},
        f8_container_forced_disabled=True,
    )

    F8StudioBaseNode.update_model(node)

    assert ("disabled", True) not in model.set_calls
    assert ("name", "Child") in model.set_calls
    assert model.properties["disabled"] is False


def test_container_restore_resets_forced_child_view_and_persistent_disabled_state() -> None:
    _ensure_app()
    node = _FakePersistentNode()
    graph = SimpleNamespace(get_node_by_id=lambda node_id: node if str(node_id) == "op" else None)
    viewer = F8StudioNodeViewer()
    viewer.set_graph(graph)

    container = F8StudioContainerNodeItem(name="Service Container")
    container.viewer = lambda: viewer  # type: ignore[method-assign]
    child_view = SimpleNamespace(
        id="op",
        disabled=True,
        f8_container_forced_disabled=True,
    )
    container._forced_child_ids = {"op"}
    container._forced_child_prev_disabled = {"op": False}

    container._restore_forced_child_if_needed(child_view)

    assert child_view.disabled is False
    assert child_view.f8_container_forced_disabled is False
    assert node.set_calls == [("disabled", False, False)]


def test_container_title_double_click_enters_inline_rename() -> None:
    _ensure_app()
    scene = QtWidgets.QGraphicsScene()
    item = F8StudioContainerNodeItem(name="Service Container")
    scene.addItem(item)
    scene_pos = item.text_item.sceneBoundingRect().center()
    event = _FakeDoubleClickEvent(scene_pos)

    item.mouseDoubleClickEvent(event)

    assert event.ignored is True
    assert item.text_item.textInteractionFlags() != QtCore.Qt.NoTextInteraction


def test_backdrop_title_double_click_enters_inline_rename() -> None:
    _ensure_app()
    scene = QtWidgets.QGraphicsScene()
    item = F8StudioBackdropNodeItem(name="Region")
    scene.addItem(item)
    scene_pos = item.text_item.sceneBoundingRect().center()
    event = _FakeDoubleClickEvent(scene_pos)

    item.mouseDoubleClickEvent(event)

    assert event.ignored is True
    assert item.text_item.textInteractionFlags() != QtCore.Qt.NoTextInteraction
