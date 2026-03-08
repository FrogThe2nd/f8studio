from __future__ import annotations

from typing import Any

from f8pystudio.nodegraph.graph_factory_flow import GraphFactoryFlowMixin
from f8pystudio.nodegraph.graph_service_reclaim import GraphServiceReclaimMixin


class _FakeSignal:
    def emit(self, _value: Any) -> None:
        return


class _TeardownNode:
    def __init__(self, *, node_id: str, events: list[str]) -> None:
        self.id = str(node_id or "")
        self.spec = object()
        self._events = events
        self.teardown_calls = 0

    def on_graph_teardown(self) -> None:
        self.teardown_calls += 1
        self._events.append(f"teardown:{self.id}")


class _GraphBase:
    def __init__(self, *, nodes: list[_TeardownNode], events: list[str]) -> None:
        self._nodes = list(nodes)
        self._events = events
        self.nodes_deleted = _FakeSignal()
        self._reclaim_timers: dict[str, Any] = {}
        self._service_bridge = None

    def all_nodes(self) -> list[_TeardownNode]:
        return list(self._nodes)

    def repair_stale_port_connection_refs(self) -> None:
        return

    def delete_node(self, node: _TeardownNode, push_undo: bool = True) -> str:
        _ = push_undo
        self._events.append(f"delete_node:{node.id}")
        self._nodes = [candidate for candidate in self._nodes if candidate is not node]
        return "deleted_one"

    def delete_nodes(self, nodes: list[_TeardownNode], push_undo: bool = True) -> str:
        _ = push_undo
        ids = ",".join(str(node.id or "") for node in nodes)
        self._events.append(f"delete_nodes:{ids}")
        node_refs = set(nodes)
        self._nodes = [candidate for candidate in self._nodes if candidate not in node_refs]
        return "deleted_many"

    def clear_session(self, *args, **kwargs) -> None:
        _ = args
        _ = kwargs
        self._events.append("clear_session_super")
        self._nodes = []

    def _schedule_service_reclaim(self, service_id: str, *, delay_ms: int = 3000) -> None:
        _ = service_id
        _ = delay_ms
        return


class _GraphHarness(GraphServiceReclaimMixin, GraphFactoryFlowMixin, _GraphBase):
    pass


def test_delete_nodes_calls_teardown_before_graph_delete() -> None:
    events: list[str] = []
    node = _TeardownNode(node_id="n1", events=events)
    graph = _GraphHarness(nodes=[node], events=events)

    result = graph._delete_nodes_expanded([node], push_undo=False)

    assert result == "deleted_one"
    assert node.teardown_calls == 1
    assert events == ["teardown:n1", "delete_node:n1"]


def test_clear_session_calls_teardown_for_all_nodes() -> None:
    events: list[str] = []
    node_a = _TeardownNode(node_id="a", events=events)
    node_b = _TeardownNode(node_id="b", events=events)
    graph = _GraphHarness(nodes=[node_a, node_b], events=events)

    graph.clear_session()

    assert node_a.teardown_calls == 1
    assert node_b.teardown_calls == 1
    assert events == ["teardown:a", "teardown:b", "clear_session_super"]
    assert graph.all_nodes() == []
