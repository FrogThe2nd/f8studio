from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pystudio.nodegraph.container_basenode import F8StudioContainerNodeItem
from f8pystudio.nodegraph.node_graph import F8StudioGraph


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
