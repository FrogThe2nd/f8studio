from __future__ import annotations

from dataclasses import dataclass, field

from f8pysdk import F8DataPortSpec, F8OperatorSpec, F8StateAccess, F8StateSpec
from f8pysdk.schema_helpers import boolean_schema, complex_object_schema, integer_schema, string_schema
from f8pysdk.msgspec_codec import copy_model

from f8pystudio.ai_assist.graph_agent_tools import AgentToolCall, GraphAgentToolExecutor
from f8pystudio.ai_assist.graph_context import build_graph_agent_seed_context, build_graph_context_snapshot


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
    _effective_state_fields: list[F8StateSpec] | None = None
    _input_ports: list[_FakePort] = field(default_factory=list)
    _output_ports: list[_FakePort] = field(default_factory=list)

    def name(self) -> str:
        return self._name

    def effective_state_fields(self) -> list[F8StateSpec]:
        if self._effective_state_fields is not None:
            return list(self._effective_state_fields)
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


class _FakeGraph:
    def __init__(self, nodes: list[_FakeNode]) -> None:
        self._nodes = list(nodes)

    def all_nodes(self) -> list[_FakeNode]:
        return list(self._nodes)


def _make_graph() -> tuple[_FakeGraph, _FakeNode, _FakeNode, _FakeNode]:
    sorter_spec = F8OperatorSpec(
        serviceClass="f8.pydl",
        operatorClass="f8.sorter",
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
            F8StateSpec(name="clsWeights", valueSchema=complex_object_schema(properties={"car": integer_schema()}), access=F8StateAccess.rw, uiLanguage="json"),
            F8StateSpec(name="scriptBody", valueSchema=string_schema(), access=F8StateAccess.rw, uiLanguage="python"),
            F8StateSpec(name="previewImage", valueSchema=string_schema(), access=F8StateAccess.rw),
        ],
    )
    overridden_fields = list(sorter_spec.stateFields or [])
    overridden_fields[0] = copy_model(overridden_fields[0], update={"description": "UI override description."})
    sorter = _FakeNode(
        id="node-sorter",
        _name="Sorter A",
        spec=sorter_spec,
        properties={
            "enabled": True,
            "clsWeights": {"car": 2, "person": 1},
            "scriptBody": "def run(x):\n    return x + 1\n" * 40,
            "previewImage": {"mime": "image/png", "content": "A" * 160},
        },
        nodePurpose="Sort detections for downstream consumers.",
        _effective_state_fields=overridden_fields,
    )
    validator = _FakeNode(
        id="node-validator",
        _name="Validator",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.validator",
            label="Validator",
            dataInPorts=[F8DataPortSpec(name="sorted", valueSchema=string_schema())],
            dataOutPorts=[F8DataPortSpec(name="validated", valueSchema=string_schema())],
            stateFields=[F8StateSpec(name="strict", valueSchema=boolean_schema(), access=F8StateAccess.rw)],
        ),
        properties={"strict": False},
    )
    source = _FakeNode(
        id="node-source",
        _name="Source",
        spec=F8OperatorSpec(
            serviceClass="f8.pydl",
            operatorClass="f8.source",
            label="Source",
            dataOutPorts=[F8DataPortSpec(name="detections", valueSchema=string_schema())],
        ),
        properties={},
    )
    source_out = source.add_output_port("detections[D]")
    sorter_in = sorter.add_input_port("[D]detections")
    source_out.connect_to(sorter_in)
    sorter_out = sorter.add_output_port("sorted[D]")
    validator_in = validator.add_input_port("[D]sorted")
    sorter_out.connect_to(validator_in)
    return _FakeGraph([source, sorter, validator]), sorter, validator, source


def test_graph_agent_seed_context_omits_schema_and_values() -> None:
    graph, sorter, validator, _source = _make_graph()
    snapshot = build_graph_context_snapshot(graph, [sorter, validator])
    seed = build_graph_agent_seed_context(snapshot)

    assert seed is not None
    payload = seed.to_dict()
    assert payload["selected_node_ids"] == ("node-sorter", "node-validator")
    assert payload["focus_node_ids"] == ("node-sorter", "node-validator")
    assert payload["focus_node_names"] == ("Sorter A", "Validator")
    selected = payload["selected_nodes"][0]
    assert "state_field_names" in selected
    assert "value_schema" not in str(payload)
    assert "scriptBody" in selected["state_field_names"]
    assert "current_values" not in str(payload)


def test_resolve_nodes_prefers_exact_id_then_name_then_fuzzy() -> None:
    graph, sorter, _validator, _source = _make_graph()
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(AgentToolCall(tool_name="resolve_nodes", arguments={"query": "node-sorter"}))

    assert result.success is True
    assert result.payload["matches"][0]["node_id"] == sorter.id
    assert result.payload["matches"][0]["match_reason"] == "exact_node_id"


def test_resolve_nodes_matches_node_name_embedded_in_longer_phrase() -> None:
    graph, _sorter, _validator, source = _make_graph()
    source._name = "CVKit Template Match"
    source.spec = copy_model(source.spec, update={"label": "CVKit Template Match", "serviceClass": "f8.cvkit.templatematch"})
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(tool_name="resolve_nodes", arguments={"query": "CVKit Template Match detections"})
    )

    assert result.success is True
    assert result.payload["matches"][0]["node_name"] == "CVKit Template Match"
    assert result.payload["matches"][0]["match_reason"] in {"query_contains_exact_node_name", "token_overlap"}


def test_resolve_nodes_rejects_full_task_instruction_queries() -> None:
    graph, _sorter, _validator, _source = _make_graph()
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(
            tool_name="resolve_nodes",
            arguments={"query": "Summarize the provided Feel8 Studio node graph and explain the dataflow from inputs to outputs."},
        )
    )

    assert result.success is False
    assert "whole user instruction" in result.payload["hint"]
    assert "must be a node identifier" in result.error


def test_resolve_nodes_rejection_suggests_direct_spec_call_when_node_is_already_known() -> None:
    graph, _sorter, _validator, source = _make_graph()
    source.id = "3Q7h"
    source._name = "CVKit Template Match"
    source.spec = copy_model(source.spec, update={"label": "CVKit Template Match", "serviceClass": "f8.cvkit.templatematch"})
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(
            tool_name="resolve_nodes",
            arguments={"query": "node 3Q7h CVKit Template Match ports detections data type"},
        )
    )

    assert result.success is False
    suggested = result.payload["suggested_next_call"]
    assert suggested["tool_name"] == "get_node_spec"
    assert suggested["arguments"]["node_id"] == "3Q7h"
    assert suggested["arguments"]["sections"] == ["data_out_ports"]


def test_get_node_spec_uses_effective_state_fields() -> None:
    graph, sorter, _validator, _source = _make_graph()
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(
            tool_name="get_node_spec",
            arguments={"node_id": sorter.id, "sections": ["state_fields"]},
        )
    )

    assert result.success is True
    state_fields = result.payload["state_fields"]
    enabled_field = next(field for field in state_fields if field["name"] == "enabled")
    assert enabled_field["description"] == "UI override description."


def test_get_state_field_details_truncates_script_and_omits_binary() -> None:
    graph, sorter, _validator, _source = _make_graph()
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(
            tool_name="get_state_field_details",
            arguments={"node_id": sorter.id, "field_names": ["scriptBody", "previewImage"], "max_value_chars": 120},
        )
    )

    assert result.success is True
    fields = result.payload["fields"]
    script_detail = next(field for field in fields if field["name"] == "scriptBody")
    image_detail = next(field for field in fields if field["name"] == "previewImage")
    assert script_detail["current_value"]["value_kind"] == "string"
    assert script_detail["current_value"]["truncated"] is True
    assert image_detail["current_value"]["omitted"] is True
    assert "Binary" in image_detail["current_value"]["omitted_reason"] or "blob" in image_detail["current_value"]["omitted_reason"]


def test_get_connections_returns_one_hop_neighbors() -> None:
    graph, sorter, _validator, _source = _make_graph()
    executor = GraphAgentToolExecutor(graph)

    result = executor.execute_tool_call(
        AgentToolCall(tool_name="get_connections", arguments={"node_ids": [sorter.id], "direction": "both"})
    )

    assert result.success is True
    pairs = {(item["from_node_id"], item["to_node_id"]) for item in result.payload["connections"]}
    assert ("node-source", "node-sorter") in pairs
    assert ("node-sorter", "node-validator") in pairs
