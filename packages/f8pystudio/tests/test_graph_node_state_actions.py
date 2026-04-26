from __future__ import annotations

from typing import Any

from qtpy import QtWidgets
from NodeGraphQt import BaseNode

from f8pystudio.nodegraph.graph_node_state_actions import GraphNodeStateActionsMixin


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeNodeClass:
    type_ = "test.node"


class _FakeNodesMenu:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []

    def add_command(self, label: str, *, func: Any, node_type: str) -> object:
        self.commands.append((str(label), str(node_type)))
        return object()


class _FakeHost(GraphNodeStateActionsMixin):
    def __init__(self) -> None:
        self._node_state_menu_node_types: set[str] = set()
        self._nodes_menu = _FakeNodesMenu()
        self.selected: list[BaseNode] = []
        self.undo_events: list[str] = []

    def context_nodes_menu(self) -> _FakeNodesMenu:
        return self._nodes_menu

    def tr(self, text: str) -> str:
        return str(text)

    def selected_nodes(self) -> list[BaseNode]:
        return list(self.selected)

    def begin_undo(self, name: str) -> None:
        self.undo_events.append(str(name))

    def end_undo(self) -> None:
        self.undo_events.append("end")


def test_install_node_state_context_menu_for_nodes_adds_enable_disable_commands() -> None:
    host = _FakeHost()

    host.install_node_state_context_menu_for_nodes([_FakeNodeClass])
    host.install_node_state_context_menu_for_nodes([_FakeNodeClass])

    assert host._nodes_menu.commands == [
        ("Enable Selected", "test.node"),
        ("Disable Selected", "test.node"),
    ]


def test_node_state_menu_action_disables_selected_nodes_as_batch() -> None:
    _ensure_app()
    host = _FakeHost()
    first = BaseNode()
    second = BaseNode()
    clicked = BaseNode()
    host.selected = [first, second]

    host._on_disable_selected_nodes_menu_action(host, first)

    assert first.disabled() is True
    assert second.disabled() is True
    assert clicked.disabled() is False
    assert host.undo_events == ["disable selected nodes", "end"]


def test_node_state_menu_action_uses_clicked_node_when_click_is_outside_selection() -> None:
    _ensure_app()
    host = _FakeHost()
    selected = BaseNode()
    clicked = BaseNode()
    selected.set_disabled(True)
    clicked.set_disabled(True)
    host.selected = [selected]

    host._on_enable_selected_nodes_menu_action(host, clicked)

    assert selected.disabled() is True
    assert clicked.disabled() is False
    assert host.undo_events == ["enable selected nodes", "end"]

