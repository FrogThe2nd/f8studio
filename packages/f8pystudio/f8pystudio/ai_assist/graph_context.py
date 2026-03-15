from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Iterable

import msgspec

from f8pysdk import F8OperatorSpec, F8ServiceSpec
from f8pysdk.msgspec_codec import dump_json

from ..nodegraph.edge_rules import EDGE_KIND_DATA, port_kind

_MAX_CURRENT_VALUE_FIELDS = 8
_MAX_VALUE_TEXT_LENGTH = 200
_MAX_GRAPH_CONTEXT_TEXT_LENGTH = 2000
_MAX_SELECTED_NODE_SUMMARIES = 8
_MAX_ONE_HOP_NODE_SUMMARIES = 12
_MAX_SUBGRAPH_CONNECTIONS = 24
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_SCRIPT_FIELD_NAMES = {
    "code",
    "script",
    "source",
    "source_code",
    "program",
    "body",
    "template",
    "prompt",
}
_SCRIPT_UI_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "tsx",
    "jsx",
    "bash",
    "shell",
    "sh",
    "powershell",
    "sql",
    "html",
    "css",
    "scss",
    "cpp",
    "c",
    "rust",
    "go",
    "java",
    "csharp",
}
_SAFE_TEXT_UI_LANGUAGES = {"", "text", "plaintext", "json", "jsonc", "yaml", "yml", "toml"}


@dataclass(frozen=True)
class GraphContextPortSummary:
    name: str
    schema_summary: str
    description: str
    required: bool


@dataclass(frozen=True)
class GraphContextStateFieldSummary:
    name: str
    label: str
    access: str
    schema_summary: str
    description: str
    required: bool
    ui_language: str


@dataclass(frozen=True)
class GraphContextValueSummary:
    field_name: str
    field_label: str
    priority: int
    summary_text: str
    value_kind: str
    truncated: bool


@dataclass(frozen=True)
class GraphContextNodeSummary:
    node_id: str
    node_name: str
    node_label: str
    node_kind: str
    service_class: str
    operator_class: str
    description: str
    data_in_ports: tuple[GraphContextPortSummary, ...]
    data_out_ports: tuple[GraphContextPortSummary, ...]
    state_fields: tuple[GraphContextStateFieldSummary, ...]
    current_values: tuple[GraphContextValueSummary, ...] = ()
    is_selected: bool = False


@dataclass(frozen=True)
class GraphContextEdgeSummary:
    edge_kind: str
    from_node_id: str
    from_node_name: str
    from_node_kind: str
    from_port: str
    to_node_id: str
    to_node_name: str
    to_node_kind: str
    to_port: str


@dataclass(frozen=True)
class GraphContextSnapshot:
    selection_label: str = ""
    selected_node_ids: tuple[str, ...] = ()
    selected_nodes: tuple[GraphContextNodeSummary, ...] = ()
    one_hop_nodes: tuple[GraphContextNodeSummary, ...] = ()
    connections: tuple[GraphContextEdgeSummary, ...] = ()
    total_selected_count: int = 0
    total_one_hop_count: int = 0
    total_connection_count: int = 0
    truncated_selected_nodes: bool = False
    truncated_one_hop_nodes: bool = False
    truncated_connections: bool = False

    @property
    def node_name(self) -> str:
        return str(self.selection_label or "").strip()

    @property
    def node_id(self) -> str:
        if len(self.selected_node_ids) == 1:
            return self.selected_node_ids[0]
        if self.selected_node_ids:
            return ",".join(self.selected_node_ids)
        return ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_graph_context_snapshot(studio_graph: Any, nodes: Any) -> GraphContextSnapshot | None:
    _ = studio_graph
    selected_nodes = _normalize_selected_nodes(nodes)
    if not selected_nodes:
        return None

    all_selected_nodes = sorted(selected_nodes, key=_node_sort_key)
    selected_node_ids = tuple(_node_id(node) for node in all_selected_nodes if _node_id(node))
    all_selected_node_ids = {_node_id(node) for node in all_selected_nodes if _node_id(node)}
    all_one_hop_nodes = _collect_one_hop_nodes(all_selected_nodes, exclude_ids=all_selected_node_ids)
    all_included_nodes = list(all_selected_nodes) + list(all_one_hop_nodes)
    all_included_node_ids = {_node_id(node) for node in all_included_nodes if _node_id(node)}
    connections_all = _collect_subgraph_connections(all_included_nodes, included_node_ids=all_included_node_ids)

    displayed_selected_nodes = all_selected_nodes[:_MAX_SELECTED_NODE_SUMMARIES]
    truncated_selected_nodes = len(all_selected_nodes) > len(displayed_selected_nodes)
    displayed_one_hop_nodes = all_one_hop_nodes[:_MAX_ONE_HOP_NODE_SUMMARIES]
    truncated_one_hop_nodes = len(all_one_hop_nodes) > len(displayed_one_hop_nodes)

    displayed_included_node_ids = {
        _node_id(node)
        for node in list(displayed_selected_nodes) + list(displayed_one_hop_nodes)
        if _node_id(node)
    }
    displayed_connections_candidates = [
        connection
        for connection in connections_all
        if connection.from_node_id in displayed_included_node_ids and connection.to_node_id in displayed_included_node_ids
    ]
    displayed_connections = displayed_connections_candidates[:_MAX_SUBGRAPH_CONNECTIONS]
    truncated_connections = len(connections_all) > len(displayed_connections)

    return GraphContextSnapshot(
        selection_label=_selection_label(all_selected_nodes),
        selected_node_ids=selected_node_ids,
        selected_nodes=tuple(_node_summary(node, include_current_values=True, is_selected=True) for node in displayed_selected_nodes),
        one_hop_nodes=tuple(_node_summary(node, include_current_values=False, is_selected=False) for node in displayed_one_hop_nodes),
        connections=tuple(displayed_connections),
        total_selected_count=len(all_selected_nodes),
        total_one_hop_count=len(all_one_hop_nodes),
        total_connection_count=len(connections_all),
        truncated_selected_nodes=truncated_selected_nodes,
        truncated_one_hop_nodes=truncated_one_hop_nodes,
        truncated_connections=truncated_connections,
    )


def format_graph_context_snapshot(
    snapshot: GraphContextSnapshot | None,
    *,
    max_chars: int = _MAX_GRAPH_CONTEXT_TEXT_LENGTH,
) -> str:
    if snapshot is None:
        return ""

    lines = [
        "## Focused Graph Subgraph Snapshot",
        "",
        "### Scope",
        f"- Selection: {snapshot.selection_label}",
        f"- Selected nodes: {snapshot.total_selected_count}",
        f"- One-hop context nodes: {snapshot.total_one_hop_count}",
        f"- Included connections: {snapshot.total_connection_count}",
        "- This is a frozen snapshot of the user's pinned graph subgraph.",
        "- Seed nodes are the user's current selection. One-hop neighbor nodes were added as immediate structure context.",
        "- This is not the full graph. Base your answer on this snapshot unless the user asks for broader graph reasoning.",
    ]
    if snapshot.truncated_selected_nodes or snapshot.truncated_one_hop_nodes or snapshot.truncated_connections:
        lines.append("- Note: Snapshot display is truncated to keep token usage bounded.")

    if snapshot.selected_nodes:
        lines.append("")
        lines.append("### Selected Nodes")
        for node in snapshot.selected_nodes:
            lines.extend(_format_detailed_node_summary(node))
        if snapshot.truncated_selected_nodes:
            lines.append("- … additional selected nodes omitted.")

    if snapshot.one_hop_nodes:
        lines.append("")
        lines.append("### One-Hop Context Nodes")
        for node in snapshot.one_hop_nodes:
            lines.extend(_format_compact_node_summary(node))
        if snapshot.truncated_one_hop_nodes:
            lines.append("- … additional one-hop nodes omitted.")

    if snapshot.connections:
        lines.append("")
        lines.append("### Connections Within Included Subgraph")
        for connection in snapshot.connections:
            lines.append(
                f"- {connection.from_node_name} (`{connection.from_node_id}`) "
                f"`{connection.from_port}` → `{connection.to_port}` "
                f"{connection.to_node_name} (`{connection.to_node_id}`) [{connection.edge_kind}]"
            )
        if snapshot.truncated_connections:
            lines.append("- … additional connections omitted.")

    return _join_lines_with_limit(lines, max_chars=max_chars)


def format_graph_context_report(snapshot: GraphContextSnapshot | None) -> str:
    if snapshot is None:
        return "# Graph Context Snapshot\n\n_No pinned graph context._"
    payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
    summary = format_graph_context_snapshot(snapshot)
    return "\n".join(
        [
            "# Graph Context Snapshot",
            "",
            "## Structured Payload",
            "```json",
            payload,
            "```",
            "",
            "## Prompt Summary",
            summary,
        ]
    )


def _selection_label(nodes: list[Any]) -> str:
    if not nodes:
        return ""
    if len(nodes) == 1:
        node = nodes[0]
        return _node_display_name(node, _node_spec(node))
    return f"{len(nodes)} selected nodes"


def _normalize_selected_nodes(nodes: Any) -> list[Any]:
    if nodes is None:
        return []
    if _node_id(nodes):
        return [nodes]
    if not isinstance(nodes, Iterable) or isinstance(nodes, (str, bytes, bytearray)):
        return []
    unique: dict[str, Any] = {}
    for item in nodes:
        item_id = _node_id(item)
        if not item_id:
            continue
        if _node_spec(item) is None:
            continue
        unique[item_id] = item
    return list(unique.values())


def _collect_one_hop_nodes(selected_nodes: list[Any], *, exclude_ids: set[str]) -> list[Any]:
    neighbors_by_id: dict[str, Any] = {}
    for node in selected_nodes:
        for neighbor in _connected_neighbor_nodes(node):
            neighbor_id = _node_id(neighbor)
            if not neighbor_id or neighbor_id in exclude_ids:
                continue
            if _node_spec(neighbor) is None:
                continue
            neighbors_by_id[neighbor_id] = neighbor
    return sorted(neighbors_by_id.values(), key=_node_sort_key)


def _connected_neighbor_nodes(node: Any) -> list[Any]:
    neighbors_by_id: dict[str, Any] = {}
    for port in _input_ports(node) + _output_ports(node):
        for connected_port in _connected_ports(port):
            neighbor = _port_node(connected_port)
            neighbor_id = _node_id(neighbor)
            if not neighbor_id:
                continue
            neighbors_by_id[neighbor_id] = neighbor
    return list(neighbors_by_id.values())


def _collect_subgraph_connections(nodes: list[Any], *, included_node_ids: set[str]) -> list[GraphContextEdgeSummary]:
    node_by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    edges: dict[tuple[str, str, str, str, str], GraphContextEdgeSummary] = {}
    for node_id, node in node_by_id.items():
        for out_port in _output_ports(node):
            out_port_name = _port_name(out_port)
            if not out_port_name:
                continue
            for in_port in _connected_ports(out_port):
                target_node = _port_node(in_port)
                target_node_id = _node_id(target_node)
                if not target_node_id or target_node_id not in included_node_ids:
                    continue
                in_port_name = _port_name(in_port)
                edge_kind = port_kind(out_port_name) or port_kind(in_port_name) or EDGE_KIND_DATA
                key = (
                    node_id,
                    _raw_port_name(out_port_name),
                    target_node_id,
                    _raw_port_name(in_port_name),
                    str(edge_kind),
                )
                if key in edges:
                    continue
                edges[key] = GraphContextEdgeSummary(
                    edge_kind=str(edge_kind),
                    from_node_id=node_id,
                    from_node_name=_node_display_name(node, _node_spec(node)),
                    from_node_kind=_node_kind(_node_spec(node)),
                    from_port=_raw_port_name(out_port_name),
                    to_node_id=target_node_id,
                    to_node_name=_node_display_name(target_node, _node_spec(target_node)),
                    to_node_kind=_node_kind(_node_spec(target_node)),
                    to_port=_raw_port_name(in_port_name),
                )
    return sorted(
        edges.values(),
        key=lambda item: (
            item.from_node_name,
            item.from_port,
            item.to_node_name,
            item.to_port,
            item.edge_kind,
        ),
    )


def _node_summary(node: Any, *, include_current_values: bool, is_selected: bool) -> GraphContextNodeSummary:
    spec = _node_spec(node)
    if spec is None:
        raise ValueError("graph context node summary requires a valid node spec")
    state_fields = _effective_state_fields(node, spec)
    current_values: tuple[GraphContextValueSummary, ...] = ()
    if include_current_values:
        current_values = tuple(_current_value_summaries(node, state_fields))
    return GraphContextNodeSummary(
        node_id=_node_id(node),
        node_name=_node_display_name(node, spec),
        node_label=_text_or_empty(spec.label),
        node_kind=_node_kind(spec),
        service_class=_service_class(spec),
        operator_class=_operator_class(spec),
        description=_text_or_empty(spec.description),
        data_in_ports=tuple(_port_summary(port) for port in list(spec.dataInPorts or [])),
        data_out_ports=tuple(_port_summary(port) for port in list(spec.dataOutPorts or [])),
        state_fields=tuple(_state_field_summary(field) for field in state_fields),
        current_values=current_values,
        is_selected=is_selected,
    )


def _format_detailed_node_summary(node: GraphContextNodeSummary) -> list[str]:
    lines = [
        f"#### {node.node_name}",
        f"- Node ID: `{node.node_id}`",
        f"- Kind: `{node.node_kind}`",
    ]
    if node.service_class:
        lines.append(f"- Service Class: `{node.service_class}`")
    if node.operator_class:
        lines.append(f"- Operator Class: `{node.operator_class}`")
    if node.description:
        lines.append(f"- Description: {node.description}")
    if node.data_in_ports:
        lines.append("- Input Ports:")
        lines.extend(_indent_lines(_format_port_lines(node.data_in_ports)))
    if node.data_out_ports:
        lines.append("- Output Ports:")
        lines.extend(_indent_lines(_format_port_lines(node.data_out_ports)))
    if node.state_fields:
        lines.append("- State Fields:")
        for field in node.state_fields:
            field_parts = [f"access={field.access}", f"schema={field.schema_summary}"]
            if field.ui_language:
                field_parts.append(f"uiLanguage={field.ui_language}")
            label_suffix = f" label={field.label}" if field.label else ""
            desc_suffix = f" | description={field.description}" if field.description else ""
            lines.append(f"  - `{field.name}` ({', '.join(field_parts)}){label_suffix}{desc_suffix}")
    if node.current_values:
        lines.append("- Current Values:")
        for value in node.current_values:
            label_suffix = f" ({value.field_label})" if value.field_label else ""
            lines.append(f"  - `{value.field_name}`{label_suffix}: {value.summary_text}")
    return lines


def _format_compact_node_summary(node: GraphContextNodeSummary) -> list[str]:
    type_bits = [node.node_kind]
    if node.operator_class:
        type_bits.append(node.operator_class)
    elif node.service_class:
        type_bits.append(node.service_class)
    ports_bits: list[str] = []
    if node.data_in_ports:
        ports_bits.append("in=" + ", ".join(port.name for port in node.data_in_ports[:4]))
    if node.data_out_ports:
        ports_bits.append("out=" + ", ".join(port.name for port in node.data_out_ports[:4]))
    ports_suffix = f" | {'; '.join(ports_bits)}" if ports_bits else ""
    description_suffix = f" | {node.description}" if node.description else ""
    return [
        f"- {node.node_name} (`{node.node_id}`, {' / '.join(type_bits)}){ports_suffix}{description_suffix}",
    ]


def _indent_lines(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def _format_port_lines(ports: tuple[GraphContextPortSummary, ...]) -> list[str]:
    lines: list[str] = []
    for port in ports:
        required_text = "required" if port.required else "optional"
        description_suffix = f" | description={port.description}" if port.description else ""
        lines.append(f"- `{port.name}` ({required_text}, schema={port.schema_summary}){description_suffix}")
    return lines


def _join_lines_with_limit(lines: list[str], *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    out_lines: list[str] = []
    current_length = 0
    for line in lines:
        addition = len(line) + (1 if out_lines else 0)
        if current_length + addition > max_chars:
            suffix = "\n- … truncated for brevity."
            if not out_lines:
                return suffix.strip()
            joined = "\n".join(out_lines)
            if len(joined) + len(suffix) <= max_chars:
                return joined + suffix
            return joined[: max(0, max_chars - 1)].rstrip() + "…"
        out_lines.append(line)
        current_length += addition
    return "\n".join(out_lines)


def _port_summary(port: Any) -> GraphContextPortSummary:
    return GraphContextPortSummary(
        name=str(port.name or "").strip(),
        schema_summary=_schema_summary(port.valueSchema),
        description=_text_or_empty(port.description),
        required=_bool_or_default(port.required, default=True),
    )


def _state_field_summary(field: Any) -> GraphContextStateFieldSummary:
    return GraphContextStateFieldSummary(
        name=str(field.name or "").strip(),
        label=_text_or_empty(field.label),
        access=_text_or_empty(field.access),
        schema_summary=_schema_summary(field.valueSchema),
        description=_text_or_empty(field.description),
        required=_bool_or_default(field.required, default=False),
        ui_language=_text_or_empty(field.uiLanguage).lower(),
    )


def _current_value_summaries(node: Any, state_fields: list[Any]) -> list[GraphContextValueSummary]:
    candidates: list[GraphContextValueSummary] = []
    for field in state_fields:
        field_name = str(field.name or "").strip()
        if not field_name:
            continue
        try:
            raw_value = node.get_property(field_name)
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            continue
        summary = _summarize_state_value(field_name=field_name, field=field, raw_value=raw_value)
        if summary is None:
            continue
        candidates.append(summary)
    candidates.sort(key=lambda item: (item.priority, item.field_name))
    return candidates[:_MAX_CURRENT_VALUE_FIELDS]


def _summarize_state_value(*, field_name: str, field: Any, raw_value: Any) -> GraphContextValueSummary | None:
    ui_language = _text_or_empty(field.uiLanguage).lower()
    field_label = _text_or_empty(field.label)
    if isinstance(raw_value, msgspec.UnsetType):
        raw_value = None
    if raw_value is None:
        return GraphContextValueSummary(
            field_name=field_name,
            field_label=field_label,
            priority=0,
            summary_text="null",
            value_kind="null",
            truncated=False,
        )
    if isinstance(raw_value, bool):
        return GraphContextValueSummary(
            field_name=field_name,
            field_label=field_label,
            priority=0,
            summary_text="true" if raw_value else "false",
            value_kind="bool",
            truncated=False,
        )
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        return GraphContextValueSummary(
            field_name=field_name,
            field_label=field_label,
            priority=0,
            summary_text=json.dumps(raw_value, ensure_ascii=False),
            value_kind="number",
            truncated=False,
        )
    if isinstance(raw_value, (bytes, bytearray, memoryview)):
        return None
    if _looks_like_binary_payload(raw_value):
        return None
    if isinstance(raw_value, str):
        return _summarize_string_value(
            field_name=field_name,
            field_label=field_label,
            ui_language=ui_language,
            raw_value=raw_value,
        )
    if isinstance(raw_value, (dict, list, tuple)):
        return _summarize_json_like_value(
            field_name=field_name,
            field_label=field_label,
            raw_value=raw_value,
            priority=2,
        )
    dumped = _dump_json_safe(raw_value)
    if isinstance(dumped, (dict, list)):
        return _summarize_json_like_value(
            field_name=field_name,
            field_label=field_label,
            raw_value=dumped,
            priority=2,
        )
    if isinstance(dumped, str):
        return _summarize_string_value(
            field_name=field_name,
            field_label=field_label,
            ui_language=ui_language,
            raw_value=dumped,
        )
    return None


def _summarize_string_value(
    *,
    field_name: str,
    field_label: str,
    ui_language: str,
    raw_value: str,
) -> GraphContextValueSummary | None:
    text = str(raw_value or "")
    stripped = text.strip()
    if not stripped:
        return None
    lowered_field_name = field_name.strip().lower()
    if ui_language not in _SAFE_TEXT_UI_LANGUAGES:
        return None
    if lowered_field_name in _SCRIPT_FIELD_NAMES and ui_language in _SCRIPT_UI_LANGUAGES:
        return None
    if _is_probable_base64_blob(stripped):
        return None
    if _looks_like_script_body(stripped):
        return None
    if ui_language in {"json", "jsonc"} or (stripped[:1] in {"{", "["} and stripped[-1:] in {"}", "]"}):
        parsed = _try_parse_json(stripped)
        if isinstance(parsed, (dict, list)):
            return _summarize_json_like_value(
                field_name=field_name,
                field_label=field_label,
                raw_value=parsed,
                priority=2,
            )
    truncated_text, truncated = _truncate_text(stripped, max_chars=_MAX_VALUE_TEXT_LENGTH, max_lines=4)
    return GraphContextValueSummary(
        field_name=field_name,
        field_label=field_label,
        priority=1,
        summary_text=json.dumps(truncated_text, ensure_ascii=False),
        value_kind="string",
        truncated=truncated,
    )


def _summarize_json_like_value(
    *,
    field_name: str,
    field_label: str,
    raw_value: Any,
    priority: int,
) -> GraphContextValueSummary | None:
    payload = _dump_json_safe(raw_value)
    if not isinstance(payload, (dict, list)):
        return None
    if _json_item_count(payload) > 24:
        return None
    compact = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(compact) > (_MAX_VALUE_TEXT_LENGTH * 2):
        return None
    truncated_text, truncated = _truncate_text(compact, max_chars=_MAX_VALUE_TEXT_LENGTH, max_lines=4)
    return GraphContextValueSummary(
        field_name=field_name,
        field_label=field_label,
        priority=priority,
        summary_text=truncated_text,
        value_kind="json",
        truncated=truncated,
    )


def _schema_summary(schema_obj: Any) -> str:
    dumped = _dump_json_safe(schema_obj)
    if not isinstance(dumped, dict):
        return "Any"
    schema_type = str(dumped.get("type") or "any").strip().lower()
    if schema_type == "object":
        properties = dumped.get("properties")
        if isinstance(properties, dict) and properties:
            keys = ", ".join(str(key) for key in properties.keys())
            return f"object<{keys}>"
        return "object"
    if schema_type == "array":
        items = dumped.get("items")
        if isinstance(items, dict):
            item_type = str(items.get("type") or "any").strip().lower() or "any"
            return f"array<{item_type}>"
        return "array"
    return schema_type or "Any"


def _node_spec(node: Any) -> F8OperatorSpec | F8ServiceSpec | None:
    if node is None:
        return None
    try:
        spec = node.spec
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return spec
    return None


def _effective_state_fields(node: Any, spec: F8OperatorSpec | F8ServiceSpec) -> list[Any]:
    try:
        fields = list(node.effective_state_fields() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        fields = list(spec.stateFields or [])
    return fields


def _node_id(node: Any) -> str:
    if node is None:
        return ""
    try:
        return str(node.id or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _node_display_name(node: Any, spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if node is not None:
        try:
            name = str(node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            name = ""
        if name:
            return name
    if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        label = _text_or_empty(spec.label)
        if label:
            return label
    return _node_id(node) or "Unknown Node"


def _node_kind(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if isinstance(spec, F8OperatorSpec):
        return "operator"
    if isinstance(spec, F8ServiceSpec):
        return "service"
    return "unknown"


def _service_class(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return ""
    return _text_or_empty(spec.serviceClass)


def _operator_class(spec: F8OperatorSpec | F8ServiceSpec | None) -> str:
    if not isinstance(spec, F8OperatorSpec):
        return ""
    return _text_or_empty(spec.operatorClass)


def _node_sort_key(node: Any) -> tuple[str, str]:
    spec = _node_spec(node)
    return (_node_display_name(node, spec).lower(), _node_id(node))


def _input_ports(node: Any) -> list[Any]:
    try:
        return list(node.input_ports() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def _output_ports(node: Any) -> list[Any]:
    try:
        return list(node.output_ports() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def _connected_ports(port: Any) -> list[Any]:
    try:
        return list(port.connected_ports() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def _port_name(port: Any) -> str:
    try:
        return str(port.name() or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _port_node(port: Any) -> Any | None:
    try:
        return port.node()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _raw_port_name(name: str) -> str:
    raw = str(name or "").strip()
    for prefix in ("[E]", "[D]", "[S]"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    for suffix in ("[E]", "[D]", "[S]"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return raw.strip()


def _dump_json_safe(value: Any) -> Any:
    if isinstance(value, msgspec.UnsetType):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out[str(key)] = _dump_json_safe(item)
        return out
    if isinstance(value, list):
        return [_dump_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_dump_json_safe(item) for item in value]
    try:
        return _dump_json_safe(dump_json(value, mode="json"))
    except (AttributeError, TypeError, ValueError):
        return None


def _json_item_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 1


def _truncate_text(text: str, *, max_chars: int, max_lines: int) -> tuple[str, bool]:
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
        truncated = True
    if truncated:
        out = out.rstrip() + "…"
    return out, truncated


def _is_probable_base64_blob(text: str) -> bool:
    stripped = str(text or "").strip()
    if len(stripped) < 120:
        return False
    if "\n" in stripped or " " in stripped or "\t" in stripped:
        return False
    return bool(_BASE64_RE.fullmatch(stripped))


def _looks_like_script_body(text: str) -> bool:
    if len(text) < 240:
        return False
    lowered = text.lower()
    signal_count = 0
    for token in ("def ", "class ", "import ", "from ", "return ", "if __name__", "function ", "const ", "let "):
        if token in lowered:
            signal_count += 1
    return signal_count >= 2


def _try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _looks_like_binary_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    lowered_keys = {str(key).strip().lower() for key in value.keys()}
    if "mime" in lowered_keys:
        mime_value = str(value.get("mime") or "").strip().lower()
        if mime_value.startswith("image/") or mime_value.startswith("application/octet-stream"):
            return True
    if {"content", "mime"} <= lowered_keys:
        return _is_probable_base64_blob(str(value.get("content") or ""))
    return False


def _text_or_empty(value: Any) -> str:
    if isinstance(value, msgspec.UnsetType) or value is None:
        return ""
    return str(value).strip()


def _bool_or_default(value: Any, *, default: bool) -> bool:
    if isinstance(value, msgspec.UnsetType) or value is None:
        return bool(default)
    return bool(value)


__all__ = [
    "GraphContextEdgeSummary",
    "GraphContextNodeSummary",
    "GraphContextPortSummary",
    "GraphContextSnapshot",
    "GraphContextStateFieldSummary",
    "GraphContextValueSummary",
    "build_graph_context_snapshot",
    "format_graph_context_report",
    "format_graph_context_snapshot",
]
