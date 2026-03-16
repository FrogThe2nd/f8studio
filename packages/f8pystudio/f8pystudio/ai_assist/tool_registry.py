from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from .graph_agent_tools import AgentToolName


@dataclass(frozen=True)
class AgentResponseShapeDefinition:
    response_type: str
    description: str
    example_json: str


@dataclass(frozen=True)
class AgentToolExample:
    situation: str
    example_json: str


@dataclass(frozen=True)
class AgentToolDefinition:
    name: AgentToolName
    purpose: str
    use_when: str
    avoid_when: str
    arguments_schema: str


def graph_agent_response_shapes() -> tuple[AgentResponseShapeDefinition, ...]:
    return (
        AgentResponseShapeDefinition(
            response_type="tool_call",
            description="Request exactly one read-only tool invocation for the next missing detail.",
            example_json='{"type":"tool_call","tool_name":"resolve_nodes","arguments":{"query":"CVKit Template Match"},"reason":"Need to identify the node."}',
        ),
        AgentResponseShapeDefinition(
            response_type="final_answer",
            description="Return the final user-facing answer once the available seed context and tool results are sufficient.",
            example_json='{"type":"final_answer","answer_markdown":"The pinned graph routes detections into the validator and then onward to the sink."}',
        ),
        AgentResponseShapeDefinition(
            response_type="clarifying_question",
            description="Ask a user question only when the needed intent truly cannot be derived from the pinned graph context or tools.",
            example_json='{"type":"clarifying_question","question":"Do you want the summary focused on dataflow or on node configuration details?"}',
        ),
    )


def graph_agent_tool_registry() -> tuple[AgentToolDefinition, ...]:
    return (
        AgentToolDefinition(
            name="resolve_nodes",
            purpose="Locate a node only when the relevant node has not already been identified.",
            use_when="You have a short node-like query such as a node id, node name, label, serviceClass, or operatorClass.",
            avoid_when="Do not use this for whole user requests, graph summaries, port questions, or any case where a node_id is already known.",
            arguments_schema='{"query":"short node-like search string","limit":"optional integer <= 8"}',
        ),
        AgentToolDefinition(
            name="get_node_overview",
            purpose="Fetch high-level metadata, port names, state field names, and neighbor counts for known nodes.",
            use_when="Use this as the first detail step for graph summaries once you already know the target node_ids.",
            avoid_when="Do not use this when you need full port schema/valueSchema details or concrete state values.",
            arguments_schema='{"node_ids":["known node ids"]}',
        ),
        AgentToolDefinition(
            name="get_node_spec",
            purpose="Fetch full spec fragments for ports, state fields, or commands of a known node.",
            use_when="Use this for port schema/type questions, command questions, or detailed state-field metadata.",
            avoid_when="Do not use this when you only need current state values or a quick overview.",
            arguments_schema='{"node_id":"known node id","sections":["data_in_ports"|"data_out_ports"|"state_fields"|"commands"]}',
        ),
        AgentToolDefinition(
            name="get_state_field_details",
            purpose="Fetch state-field metadata plus current value previews for known fields on a known node.",
            use_when="Use this for valueSchema, uiLanguage, access mode, or current-value questions about state fields.",
            avoid_when="Do not use this for data ports or for broad graph-structure questions.",
            arguments_schema='{"node_id":"known node id","field_names":["known state field names"],"include_values":"optional bool","max_value_chars":"optional integer"}',
        ),
        AgentToolDefinition(
            name="get_connections",
            purpose="Fetch one-hop connections and neighbor summaries for known nodes.",
            use_when="Use this for dataflow, upstream/downstream, and graph-structure reasoning.",
            avoid_when="Do not use this when the question is only about a node's internal schema or state values.",
            arguments_schema='{"node_ids":["known node ids"],"direction":"in|out|both"}',
        ),
    )


def graph_agent_tool_examples() -> tuple[AgentToolExample, ...]:
    return (
        AgentToolExample(
            situation="If you know node_id `3Q7h` and need the output port schema.",
            example_json='{"type":"tool_call","tool_name":"get_node_spec","arguments":{"node_id":"3Q7h","sections":["data_out_ports"]},"reason":"Need output port schema."}',
        ),
        AgentToolExample(
            situation="If you know node_id `3Q7h` and need current details for a state field.",
            example_json='{"type":"tool_call","tool_name":"get_state_field_details","arguments":{"node_id":"3Q7h","field_names":["foo"]},"reason":"Need current state value."}',
        ),
        AgentToolExample(
            situation="If the user asks for a high-level summary of the pinned graph.",
            example_json='{"type":"tool_call","tool_name":"get_node_overview","arguments":{"node_ids":["3Q7h"]},"reason":"Need overview for the selected nodes before summarizing."}',
        ),
        AgentToolExample(
            situation="If the user asks about the currently selected node's data output port types.",
            example_json='{"type":"tool_call","tool_name":"get_node_spec","arguments":{"node_id":"3Q7h","sections":["data_out_ports"]},"reason":"User asked about the selected node output port types."}',
        ),
    )


def render_graph_agent_response_shapes() -> str:
    lines = ["Allowed response shapes:"]
    for shape in graph_agent_response_shapes():
        lines.append(f"- `{shape.response_type}`: {shape.description}")
        lines.append(shape.example_json)
    return "\n".join(lines)


def render_graph_agent_tool_registry() -> str:
    lines = ["Available tools:"]
    for tool in graph_agent_tool_registry():
        lines.append(f"- `{tool.name}`: {tool.purpose}")
        lines.append(f"  Use when: {tool.use_when}")
        lines.append(f"  Avoid when: {tool.avoid_when}")
        lines.append(f"  Arguments: `{tool.arguments_schema}`")
    return "\n".join(lines)


def render_graph_agent_tool_examples() -> str:
    lines = ["Tool selection examples:"]
    for example in graph_agent_tool_examples():
        lines.append(f"- {example.situation}")
        lines.append(example.example_json)
    return "\n".join(lines)


def load_graph_agent_tool_guide() -> str:
    resource = resources.files(__package__).joinpath("graph_agent_tool_guide.md")
    return resource.read_text(encoding="utf-8").strip()


__all__ = [
    "AgentResponseShapeDefinition",
    "AgentToolDefinition",
    "AgentToolExample",
    "graph_agent_response_shapes",
    "graph_agent_tool_examples",
    "graph_agent_tool_registry",
    "load_graph_agent_tool_guide",
    "render_graph_agent_response_shapes",
    "render_graph_agent_tool_examples",
    "render_graph_agent_tool_registry",
]
