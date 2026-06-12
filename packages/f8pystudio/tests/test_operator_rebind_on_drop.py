from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pysdk.specs import F8OperatorSpec

from f8pystudio.nodegraph.node_graph import F8StudioGraph


class _FakeContainer:
    def __init__(self, node_id: str, service_class: str) -> None:
        self.id = node_id
        self.spec = SimpleNamespace(serviceClass=service_class)
        self._child_nodes: list[Any] = []
        self.view = SimpleNamespace(_child_views=[])

    def add_child(self, node: Any) -> None:
        if node not in self._child_nodes:
            self._child_nodes.append(node)
        if node.view not in self.view._child_views:
            self.view._child_views.append(node.view)
        node.view._container_item = self

    def remove_child(self, node: Any) -> None:
        self._child_nodes = [n for n in self._child_nodes if n is not node]
        self.view._child_views = [v for v in self.view._child_views if v is not node.view]
        if node.view._container_item is self:
            node.view._container_item = None


class _FakeOperator:
    def __init__(self, node_id: str, service_class: str, svc_id: str, *, x: float, y: float) -> None:
        self.id = node_id
        self.spec = F8OperatorSpec(
            serviceClass=service_class,
            operatorClass=f"test.{node_id}",
            label=node_id,
            execInPorts=["in"],
            execOutPorts=["next"],
        )
        self.svcId = svc_id
        self.model = SimpleNamespace(properties={"svcId": svc_id}, custom_properties={}, pos=[x, y])
        self.view = SimpleNamespace(id=node_id, _container_item=None, xy_pos=[x, y])
        self._properties: dict[str, Any] = {"svcId": svc_id}
        self._input_ports: list[Any] = []
        self._output_ports: list[Any] = []

    def set_property(self, name: str, value: Any, push_undo: bool = True) -> None:
        _ = push_undo
        self._properties[str(name)] = value

    def input_ports(self) -> list[Any]:
        return list(self._input_ports)

    def output_ports(self) -> list[Any]:
        return list(self._output_ports)


class _FakePort:
    def __init__(self, name: str, node: _FakeOperator) -> None:
        self._name = name
        self._node = node
        self._connected_ports: list[_FakePort] = []
        self.disconnect_calls: list[tuple[_FakePort, bool, bool]] = []

    def name(self) -> str:
        return self._name

    def node(self) -> _FakeOperator:
        return self._node

    def connected_ports(self) -> list["_FakePort"]:
        return list(self._connected_ports)

    def connect_to(self, port: "_FakePort") -> None:
        if port not in self._connected_ports:
            self._connected_ports.append(port)
        if self not in port._connected_ports:
            port._connected_ports.append(self)

    def disconnect_from(self, port: "_FakePort", push_undo: bool = True, emit_signal: bool = True) -> None:
        self.disconnect_calls.append((port, bool(push_undo), bool(emit_signal)))
        if port in self._connected_ports:
            self._connected_ports.remove(port)
        if self in port._connected_ports:
            port._connected_ports.remove(self)


def _new_graph(*, stub_disconnect: bool = True) -> F8StudioGraph:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._is_operator_node = lambda node: isinstance(node, _FakeOperator)  # type: ignore[method-assign]
    graph._is_container_node = lambda node: isinstance(node, _FakeContainer)  # type: ignore[method-assign]
    if stub_disconnect:
        graph._disconnect_invalid_connections_for_operator = lambda op: 0  # type: ignore[method-assign]
    graph._notification_parent = lambda: None  # type: ignore[method-assign]
    graph.selected_nodes = lambda: []  # type: ignore[method-assign]
    return graph


def test_on_operator_drop_rebinds_operator_to_new_same_class_container(monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("f8pystudio.nodegraph.graph_container_binding.show_warning", lambda *args, **kwargs: warnings.append("w"))

    old_container = _FakeContainer("svc_old", "f8.pyengine")
    new_container = _FakeContainer("svc_new", "f8.pyengine")
    operator = _FakeOperator("op1", "f8.pyengine", "svc_old", x=10.0, y=10.0)
    old_container.add_child(operator)

    graph = _new_graph()
    nodes = {"op1": operator, "svc_old": old_container, "svc_new": new_container}
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: new_container  # type: ignore[method-assign]

    ok, msg = graph.on_operator_drop(node_id="op1", start_pos=(10.0, 10.0), start_container_id="svc_old")

    assert ok is True
    assert msg == ""
    assert operator.svcId == "svc_new"
    assert operator._properties["svcId"] == "svc_new"
    assert operator in new_container._child_nodes
    assert operator not in old_container._child_nodes
    assert warnings == []


def test_on_operator_drop_reverts_when_target_container_service_class_mismatch(monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("f8pystudio.nodegraph.graph_container_binding.show_warning", lambda *args, **kwargs: warnings.append("w"))

    old_container = _FakeContainer("svc_old", "f8.pyengine")
    wrong_container = _FakeContainer("svc_other", "f8.audio")
    operator = _FakeOperator("op1", "f8.pyengine", "svc_old", x=33.0, y=44.0)
    old_container.add_child(operator)
    operator.view.xy_pos = [120.0, 220.0]
    operator.model.pos = [120.0, 220.0]

    graph = _new_graph()
    nodes = {"op1": operator, "svc_old": old_container, "svc_other": wrong_container}
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: wrong_container  # type: ignore[method-assign]

    ok, _msg = graph.on_operator_drop(node_id="op1", start_pos=(33.0, 44.0), start_container_id="svc_old")

    assert ok is False
    assert operator.svcId == "svc_old"
    assert operator.view.xy_pos == [33.0, 44.0]
    assert operator.model.pos == [33.0, 44.0]
    assert operator in old_container._child_nodes
    assert warnings == ["w"]


def test_on_operator_drop_reverts_when_not_dropped_inside_container(monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("f8pystudio.nodegraph.graph_container_binding.show_warning", lambda *args, **kwargs: warnings.append("w"))

    old_container = _FakeContainer("svc_old", "f8.pyengine")
    operator = _FakeOperator("op1", "f8.pyengine", "svc_old", x=5.0, y=8.0)
    old_container.add_child(operator)
    operator.view.xy_pos = [300.0, 400.0]
    operator.model.pos = [300.0, 400.0]

    graph = _new_graph()
    nodes = {"op1": operator, "svc_old": old_container}
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: None  # type: ignore[method-assign]

    ok, _msg = graph.on_operator_drop(node_id="op1", start_pos=(5.0, 8.0), start_container_id="svc_old")

    assert ok is False
    assert operator.svcId == "svc_old"
    assert operator.view.xy_pos == [5.0, 8.0]
    assert operator.model.pos == [5.0, 8.0]
    assert warnings == ["w"]


def test_on_operator_drop_rebinds_selected_batch_before_pruning_exec_edges(monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("f8pystudio.nodegraph.graph_container_binding.show_warning", lambda *args, **kwargs: warnings.append(str(args[2])))

    old_container = _FakeContainer("svc_old", "f8.pyengine")
    new_container = _FakeContainer("svc_new", "f8.pyengine")
    first = _FakeOperator("op1", "f8.pyengine", "svc_old", x=10.0, y=10.0)
    second = _FakeOperator("op2", "f8.pyengine", "svc_old", x=30.0, y=10.0)
    old_container.add_child(first)
    old_container.add_child(second)

    out_port = _FakePort("next[E]", first)
    in_port = _FakePort("[E]in", second)
    out_port.connect_to(in_port)
    first._output_ports = [out_port]
    second._input_ports = [in_port]

    graph = _new_graph(stub_disconnect=False)
    nodes = {"op1": first, "op2": second, "svc_old": old_container, "svc_new": new_container}
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: new_container  # type: ignore[method-assign]
    graph.selected_nodes = lambda: [first, second]  # type: ignore[method-assign]

    ok, msg = graph.on_operator_drop(node_id="op1", start_pos=(10.0, 10.0), start_container_id="svc_old")

    assert ok is True
    assert msg == ""
    assert first.svcId == "svc_new"
    assert second.svcId == "svc_new"
    assert first in new_container._child_nodes
    assert second in new_container._child_nodes
    assert first not in old_container._child_nodes
    assert second not in old_container._child_nodes
    assert out_port.connected_ports() == [in_port]
    assert in_port.connected_ports() == [out_port]
    assert out_port.disconnect_calls == []
    assert warnings == []


def test_on_operator_drop_still_prunes_exec_edge_to_unmoved_operator(monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("f8pystudio.nodegraph.graph_container_binding.show_warning", lambda *args, **kwargs: warnings.append(str(args[2])))

    old_container = _FakeContainer("svc_old", "f8.pyengine")
    new_container = _FakeContainer("svc_new", "f8.pyengine")
    moved = _FakeOperator("op1", "f8.pyengine", "svc_old", x=10.0, y=10.0)
    unmoved = _FakeOperator("op2", "f8.pyengine", "svc_old", x=30.0, y=10.0)
    old_container.add_child(moved)
    old_container.add_child(unmoved)

    out_port = _FakePort("next[E]", moved)
    in_port = _FakePort("[E]in", unmoved)
    out_port.connect_to(in_port)
    moved._output_ports = [out_port]
    unmoved._input_ports = [in_port]

    graph = _new_graph(stub_disconnect=False)
    nodes = {"op1": moved, "op2": unmoved, "svc_old": old_container, "svc_new": new_container}
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: new_container if node is moved else old_container  # type: ignore[method-assign]
    graph.selected_nodes = lambda: [moved]  # type: ignore[method-assign]

    ok, msg = graph.on_operator_drop(node_id="op1", start_pos=(10.0, 10.0), start_container_id="svc_old")

    assert ok is True
    assert msg == ""
    assert moved.svcId == "svc_new"
    assert unmoved.svcId == "svc_old"
    assert out_port.connected_ports() == []
    assert in_port.connected_ports() == []
    assert out_port.disconnect_calls == [(in_port, False, False)]
    assert warnings == ["Moved operator to service `svc_new` and dropped 1 invalid connection(s)."]


def test_rebind_container_children_restores_persisted_svc_id_before_geometry() -> None:
    container = _FakeContainer("svc_engine", "f8.pyengine")
    operator = _FakeOperator("op1", "f8.pyengine", "svc_engine", x=900.0, y=700.0)

    graph = _new_graph()
    nodes = {"svc_engine": container, "op1": operator}
    graph.all_nodes = lambda: [container, operator]  # type: ignore[method-assign]
    graph.get_node_by_id = lambda node_id: nodes.get(str(node_id))  # type: ignore[method-assign]
    graph._container_at_node = lambda node: None  # type: ignore[method-assign]

    graph._rebind_container_children()

    assert operator.svcId == "svc_engine"
    assert operator in container._child_nodes
    assert operator.view in container.view._child_views
    assert operator.view._container_item is container
