from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec, F8ServiceSpec
from f8pystudio.nodegraph.graph_identity_actions import GraphIdentityActionsMixin


class _PortModel:
    def __init__(self, connected_ports: dict[str, list[str]] | None = None) -> None:
        self.connected_ports: dict[str, list[str]] = dict(connected_ports or {})


class _Port:
    def __init__(self, connected_ports: dict[str, list[str]] | None = None) -> None:
        self.model = _PortModel(connected_ports)


class _NodeModel:
    def __init__(self, node_id: str) -> None:
        self.id = str(node_id)
        self.properties: dict[str, Any] = {}
        self.custom_properties: dict[str, Any] = {}


class _NodeView:
    def __init__(self, node_id: str) -> None:
        self.id = str(node_id)
        self.draw_calls = 0
        self.update_calls = 0

    def draw_node(self) -> None:
        self.draw_calls += 1

    def update(self) -> None:
        self.update_calls += 1


class _Node:
    def __init__(
        self,
        *,
        node_id: str,
        spec: Any,
        svc_id: str = "",
        input_ports: list[_Port] | None = None,
        output_ports: list[_Port] | None = None,
    ) -> None:
        self.spec = spec
        self.model = _NodeModel(node_id)
        self.view = _NodeView(node_id)
        self.svcId = str(svc_id)
        self._input_ports = list(input_ports or [])
        self._output_ports = list(output_ports or [])

    @property
    def id(self) -> str:
        return str(self.model.id)

    @id.setter
    def id(self, value: str) -> None:
        self.model.id = str(value)

    def input_ports(self) -> list[_Port]:
        return list(self._input_ports)

    def output_ports(self) -> list[_Port]:
        return list(self._output_ports)

    def set_property(self, name: str, value: Any, push_undo: bool = True) -> None:
        _ = push_undo
        if name in self.model.properties:
            self.model.properties[name] = value
            return
        self.model.custom_properties[name] = value


class _GraphHarness(GraphIdentityActionsMixin):
    def __init__(self, nodes: list[_Node]) -> None:
        self._nodes = list(nodes)
        self.model = SimpleNamespace(nodes={node.id: node for node in nodes})
        self._service_bridge = None
        self._reclaim_timers: dict[str, Any] = {}

    def tr(self, text: str) -> str:
        return str(text)

    def all_nodes(self) -> list[_Node]:
        return list(self._nodes)

    def get_node_by_id(self, node_id: str) -> _Node | None:
        return self.model.nodes.get(str(node_id))

    @staticmethod
    def _is_operator_node(node: _Node) -> bool:
        return isinstance(node.spec, F8OperatorSpec)


def _service_spec(service_class: str) -> F8ServiceSpec:
    return F8ServiceSpec(serviceClass=service_class, label="Service")


def _operator_spec(*, service_class: str, operator_class: str) -> F8OperatorSpec:
    return F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass=service_class,
        operatorClass=operator_class,
        version="0.0.1",
        label="Operator",
    )


def test_validate_new_node_id_rejects_duplicate_id() -> None:
    node_a = _Node(node_id="svc_a", spec=_service_spec("f8.tests"))
    node_b = _Node(node_id="svc_b", spec=_service_spec("f8.tests"))
    graph = _GraphHarness([node_a, node_b])

    ok, message = graph._validate_new_node_id(node=node_a, new_id="svc_b")

    assert ok is False
    assert "already exists" in message


def test_rewrite_connected_port_node_id_references_moves_entries() -> None:
    node = _Node(
        node_id="op.a",
        spec=_operator_spec(service_class="f8.tests", operator_class="f8.tests.op"),
        input_ports=[_Port({"svc.old": ["in[D]"], "svc.keep": ["x[D]"]})],
    )
    graph = _GraphHarness([node])

    graph._rewrite_connected_port_node_id_references(old_id="svc.old", new_id="svc.new")

    connected = node.input_ports()[0].model.connected_ports
    assert "svc.old" not in connected
    assert connected["svc.new"] == ["in[D]"]
    assert connected["svc.keep"] == ["x[D]"]


def test_rename_node_identity_updates_operator_mapping_and_property() -> None:
    op_node = _Node(
        node_id="op_1",
        spec=_operator_spec(service_class="f8.tests", operator_class="f8.tests.op"),
        svc_id="svc_1",
        input_ports=[_Port({"peer": ["out[E]"]})],
    )
    op_node.model.properties["operatorId"] = "op_1"
    peer_node = _Node(node_id="peer", spec=_service_spec("f8.peer"))

    graph = _GraphHarness([op_node, peer_node])

    ok, message = graph._rename_node_identity(node=op_node, new_id="op_2")

    assert ok is True
    assert message == ""
    assert op_node.id == "op_2"
    assert op_node.view.id == "op_2"
    assert "op_1" not in graph.model.nodes
    assert graph.model.nodes["op_2"] is op_node
    assert op_node.model.properties["operatorId"] == "op_2"
