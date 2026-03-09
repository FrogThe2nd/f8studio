from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.nodegraph.node_graph import F8StudioGraph


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeView:
    def __init__(self) -> None:
        self.draw_calls = 0

    def draw_node(self) -> None:
        self.draw_calls += 1


class _FakeNode:
    def __init__(self, node_id: str) -> None:
        self.id = node_id
        self.view = _FakeView()


class _GraphStub:
    def __init__(self) -> None:
        self._layout_stabilize_pending = False
        self._nodes = [_FakeNode("a"), _FakeNode("b")]

    def all_nodes(self) -> list[_FakeNode]:
        return list(self._nodes)


def test_schedule_node_layout_stabilization_debounces() -> None:
    app = _ensure_app()
    graph = _GraphStub()

    F8StudioGraph._schedule_node_layout_stabilization(graph)  # type: ignore[arg-type]
    F8StudioGraph._schedule_node_layout_stabilization(graph)  # type: ignore[arg-type]
    app.processEvents()

    assert graph._layout_stabilize_pending is False
    assert graph._nodes[0].view.draw_calls == 1
    assert graph._nodes[1].view.draw_calls == 1
