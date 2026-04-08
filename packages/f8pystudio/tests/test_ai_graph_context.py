from __future__ import annotations

from dataclasses import dataclass, field

from f8pysdk.specs import F8DataPortSpec, F8OperatorSpec, F8StateAccess, F8StateSpec
from f8pysdk.schema_helpers import boolean_schema, complex_object_schema, integer_schema, string_schema

from f8pystudio.ai_assist.graph_context import build_graph_context_snapshot, format_graph_context_snapshot


class _FakePort:
    def __init__(self, name: str, node: "_FakeNode") -> None:
        self._name = name
        self._node = node
        self._connected_ports: list[_FakePort] = []

    def name(self) -> str:
        return self._name

    def node(self) -> "_FakeNode":
        return self._node

    def connected_ports(self) -> list["_FakePort"]:
        return list(self._connected_ports)

    def connect_to(self, other: "_FakePort") -> None:
        if other not in self._connected_ports:
            self._connected_ports.append(other)
        if self not in other._connected_ports:
            other._connected_ports.append(self)


@dataclass
class _FakeNode:
    id: str
    _name: str
    spec: F8OperatorSpec
    properties: dict[str, object]
    nodePurpose: str = ""
    _input_ports: list[_FakePort] = field(default_factory=list)
    _output_ports: list[_FakePort] = field(default_factory=list)

    def name(self) -> str:
        return self._name

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])

    def get_property(self, name: str) -> object:
        return self.properties.get(name)

    def input_ports(self) -> list[_FakePort]:
        return list(self._input_ports)

    def output_ports(self) -> list[_FakePort]:
        return list(self._output_ports)

    def add_input_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._input_ports.append(port)
        return port

    def add_output_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._output_ports.append(port)
        return port


def _make_subgraph() -> tuple[_FakeNode, _FakeNode, _FakeNode, _FakeNode]:
    selected_a = _FakeNode(
        id="node-sorter",
        _name="Detection Sorter",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.detection_sorter",
            label="Detection Sorter",
            description="Sorts detections into classes.",
            dataInPorts=[
                F8DataPortSpec(name="detections", valueSchema=complex_object_schema(properties={"frameId": integer_schema()})),
            ],
            dataOutPorts=[
                F8DataPortSpec(name="sorted", valueSchema=complex_object_schema(properties={"count": integer_schema()})),
            ],
            stateFields=[
                F8StateSpec(name="enabled", valueSchema=boolean_schema(), access=F8StateAccess.rw, label="Enabled"),
                F8StateSpec(name="retries", valueSchema=integer_schema(), access=F8StateAccess.rw, label="Retries"),
                F8StateSpec(
                    name="clsWeights",
                    valueSchema=complex_object_schema(properties={"car": integer_schema(), "person": integer_schema()}),
                    access=F8StateAccess.rw,
                    label="Class Weights",
                    uiControl="code[json]",
                ),
                F8StateSpec(name="scriptBody", valueSchema=string_schema(), access=F8StateAccess.rw, uiControl="code[python]"),
                F8StateSpec(name="blob", valueSchema=string_schema(), access=F8StateAccess.rw),
                F8StateSpec(name="previewImage", valueSchema=string_schema(), access=F8StateAccess.rw),
            ],
        ),
        properties={
            "enabled": True,
            "retries": 3,
            "clsWeights": '{"car": 2, "person": 1}',
            "scriptBody": "def run(x):\n    import os\n    return x\n" * 20,
            "blob": "a" * 140,
            "previewImage": {"mime": "image/png", "content": "A" * 140},
        },
        nodePurpose="Sort detections into the canonical class buckets used downstream.",
    )
    selected_b = _FakeNode(
        id="node-validator",
        _name="Validator",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.validator",
            label="Validator",
            dataInPorts=[F8DataPortSpec(name="sorted", valueSchema=complex_object_schema(properties={"count": integer_schema()}))],
            dataOutPorts=[F8DataPortSpec(name="validated", valueSchema=complex_object_schema(properties={"ok": boolean_schema()}))],
            stateFields=[
                F8StateSpec(name="strict", valueSchema=boolean_schema(), access=F8StateAccess.rw, label="Strict"),
            ],
        ),
        properties={"strict": False},
    )
    producer = _FakeNode(
        id="node-source",
        _name="Source",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.source",
            label="Source",
            dataOutPorts=[F8DataPortSpec(name="detections", valueSchema=complex_object_schema(properties={"frameId": integer_schema()}))],
        ),
        properties={},
    )
    sink = _FakeNode(
        id="node-sink",
        _name="Sink",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.sink",
            label="Sink",
            dataInPorts=[F8DataPortSpec(name="validated", valueSchema=complex_object_schema(properties={"ok": boolean_schema()}))],
        ),
        properties={},
    )

    producer_out = producer.add_output_port("detections[D]")
    selected_a_in = selected_a.add_input_port("[D]detections")
    producer_out.connect_to(selected_a_in)

    selected_a_out = selected_a.add_output_port("sorted[D]")
    selected_b_in = selected_b.add_input_port("[D]sorted")
    selected_a_out.connect_to(selected_b_in)

    selected_b_out = selected_b.add_output_port("validated[D]")
    sink_in = sink.add_input_port("[D]validated")
    selected_b_out.connect_to(sink_in)
    return selected_a, selected_b, producer, sink


def test_build_graph_context_snapshot_builds_selected_subgraph_with_one_hop_nodes() -> None:
    selected_a, selected_b, _producer, _sink = _make_subgraph()

    snapshot = build_graph_context_snapshot(studio_graph=None, nodes=[selected_a, selected_b])

    assert snapshot is not None
    assert snapshot.selection_label == "2 selected nodes"
    assert snapshot.total_selected_count == 2
    assert snapshot.total_one_hop_count == 2
    assert len(snapshot.selected_nodes) == 2
    assert len(snapshot.one_hop_nodes) == 2
    assert {node.node_name for node in snapshot.one_hop_nodes} == {"Source", "Sink"}
    connection_pairs = {(edge.from_node_name, edge.to_node_name) for edge in snapshot.connections}
    assert ("Source", "Detection Sorter") in connection_pairs
    assert ("Detection Sorter", "Validator") in connection_pairs
    assert ("Validator", "Sink") in connection_pairs
    selected_a_summary = next(node for node in snapshot.selected_nodes if node.node_name == "Detection Sorter")
    current_value_names = {value.field_name for value in selected_a_summary.current_values}
    assert "enabled" in current_value_names
    assert "retries" in current_value_names
    assert "clsWeights" in current_value_names
    assert "scriptBody" not in current_value_names
    assert "blob" not in current_value_names
    assert "previewImage" not in current_value_names
    one_hop_source = next(node for node in snapshot.one_hop_nodes if node.node_name == "Source")
    assert one_hop_source.current_values == ()
    assert selected_a_summary.instance_purpose == "Sort detections into the canonical class buckets used downstream."


def test_format_graph_context_snapshot_respects_length_limit() -> None:
    selected_a, selected_b, _producer, _sink = _make_subgraph()
    snapshot = build_graph_context_snapshot(studio_graph=None, nodes=[selected_a, selected_b])

    text = format_graph_context_snapshot(snapshot, max_chars=280)

    assert len(text) <= 280
    assert "Focused Graph Subgraph Snapshot" in text
    assert "2 selected nodes" in text


def test_format_graph_context_snapshot_distinguishes_type_description_and_instance_purpose() -> None:
    selected_a, _selected_b, _producer, _sink = _make_subgraph()
    snapshot = build_graph_context_snapshot(studio_graph=None, nodes=[selected_a])

    text = format_graph_context_snapshot(snapshot, max_chars=2000)

    assert "Type Description: Sorts detections into classes." in text
    assert "Instance Purpose: Sort detections into the canonical class buckets used downstream." in text


def test_build_graph_context_snapshot_counts_one_hop_and_connections_from_all_selected_nodes() -> None:
    selected_nodes: list[_FakeNode] = []
    source_nodes: list[_FakeNode] = []

    for index in range(9):
        selected = _FakeNode(
            id=f"selected-{index}",
            _name=f"Selected {index}",
            spec=F8OperatorSpec(
                serviceClass="f8.pydl",
                operatorClass=f"f8.selected_{index}",
                label=f"Selected {index}",
                dataInPorts=[F8DataPortSpec(name="input", valueSchema=string_schema())],
            ),
            properties={},
        )
        source = _FakeNode(
            id=f"source-{index}",
            _name=f"Source {index}",
            spec=F8OperatorSpec(
                serviceClass="f8.pydl",
                operatorClass=f"f8.source_{index}",
                label=f"Source {index}",
                dataOutPorts=[F8DataPortSpec(name="output", valueSchema=string_schema())],
            ),
            properties={},
        )
        source_out = source.add_output_port("output[D]")
        selected_in = selected.add_input_port("[D]input")
        source_out.connect_to(selected_in)
        selected_nodes.append(selected)
        source_nodes.append(source)

    snapshot = build_graph_context_snapshot(studio_graph=None, nodes=selected_nodes)

    assert snapshot is not None
    assert snapshot.total_selected_count == 9
    assert len(snapshot.selected_nodes) == 8
    assert snapshot.truncated_selected_nodes is True
    assert snapshot.total_one_hop_count == 9
    assert len(snapshot.one_hop_nodes) == 9
    assert snapshot.total_connection_count == 9
    assert len(snapshot.connections) == 8
    assert snapshot.truncated_connections is True
    displayed_connection_sources = {edge.from_node_id for edge in snapshot.connections}
    assert "source-8" not in displayed_connection_sources
