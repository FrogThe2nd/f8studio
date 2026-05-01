from __future__ import annotations

import os
import sys
from typing import Any

from qtpy import QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.nodegraph import graph_monitor_actions as graph_monitor_actions_module  # noqa: E402
from f8pystudio.nodegraph.graph_monitor_actions import GraphMonitorActionsMixin  # noqa: E402
from f8pystudio.nodegraph.missing_service_basenode import F8StudioServiceMissingNode  # noqa: E402


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeOperatorNodeClass:
    type_ = "test.operator"


class _FakeNodesMenu:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []

    def add_command(self, label: str, *, func: Any, node_type: str) -> object:
        _ = func
        self.commands.append((str(label), str(node_type)))
        return object()


class _FakeBridge:
    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        _ = service_id
        _ = limit
        return []


class _FakeHost(GraphMonitorActionsMixin):
    def __init__(self, bridge: _FakeBridge | None = None) -> None:
        self._monitor_menu_node_types: set[str] = set()
        self._nodes_menu = _FakeNodesMenu()
        self._service_bridge = bridge

    def context_nodes_menu(self) -> _FakeNodesMenu:
        return self._nodes_menu

    def tr(self, text: str) -> str:
        return str(text)

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return None


def test_install_monitor_context_menu_only_targets_service_nodes() -> None:
    host = _FakeHost()

    host.install_monitor_context_menu_for_nodes([F8StudioServiceMissingNode, _FakeOperatorNodeClass])
    host.install_monitor_context_menu_for_nodes([F8StudioServiceMissingNode])

    assert host._nodes_menu.commands == [
        ("View Monitor Stream...", str(F8StudioServiceMissingNode.type_)),
    ]


def test_monitor_context_menu_action_opens_service_monitor_stream(monkeypatch) -> None:
    _ensure_app()
    bridge = _FakeBridge()
    host = _FakeHost(bridge=bridge)
    node = F8StudioServiceMissingNode()
    node.model.id = "svcA"
    node.view.id = "svcA"
    opened: list[tuple[str, object]] = []

    def _open_dialog(*, parent: QtWidgets.QWidget | None, bridge: object, service_id: str) -> None:
        _ = parent
        opened.append((str(service_id), bridge))

    monkeypatch.setattr(graph_monitor_actions_module, "open_monitor_stream_dialog", _open_dialog)

    host._on_view_monitor_stream_menu_action(host, node)

    assert opened == [("svcA", bridge)]
