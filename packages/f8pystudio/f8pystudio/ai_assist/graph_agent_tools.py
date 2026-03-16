from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from typing import Any, Literal

import msgspec

from f8pysdk import F8OperatorSpec, F8ServiceSpec
from f8pysdk.msgspec_codec import dump_json

from .graph_context import dump_graph_value_json

logger = logging.getLogger(__name__)

AgentToolName = Literal[
    "resolve_nodes",
    "get_node_overview",
    "get_node_spec",
    "get_state_field_details",
    "get_connections",
]

_MAX_RESOLVE_LIMIT = 8
_DEFAULT_MAX_VALUE_CHARS = 1000
_MAX_PREVIEW_LINES = 12
_MAX_RESOLVE_QUERY_LENGTH = 96


@dataclass(frozen=True)
class AgentToolCall:
    tool_name: AgentToolName
    arguments: dict[str, Any]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentToolResult:
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    payload: dict[str, Any]
    summary: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GraphAgentToolExecutor:
    def __init__(self, studio_graph: Any | None) -> None:
        self._studio_graph = studio_graph

    def execute_tool_call(self, call: AgentToolCall) -> AgentToolResult:
        try:
            if call.tool_name == "resolve_nodes":
                return self._resolve_nodes(call)
            if call.tool_name == "get_node_overview":
                return self._get_node_overview(call)
            if call.tool_name == "get_node_spec":
                return self._get_node_spec(call)
            if call.tool_name == "get_state_field_details":
                return self._get_state_field_details(call)
            if call.tool_name == "get_connections":
                return self._get_connections(call)
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Unknown tool",
                error=f"Unknown tool: {call.tool_name}",
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.exception("Graph agent tool failed: %s", call.tool_name)
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary=f"{call.tool_name} failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _resolve_nodes(self, call: AgentToolCall) -> AgentToolResult:
        query = str(call.arguments.get("query") or "").strip()
        limit_raw = call.arguments.get("limit")
        limit = _MAX_RESOLVE_LIMIT
        if isinstance(limit_raw, int):
            limit = max(1, min(_MAX_RESOLVE_LIMIT, limit_raw))
        if not query:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node query missing",
                error="`query` is required",
            )
        if _looks_like_task_instruction(query):
            suggested_next_call = self._suggested_next_call_for_resolve_query(query)
            payload = {
                "query": query,
                "hint": (
                    "`resolve_nodes` expects a short node identifier, node name, label, serviceClass, "
                    "or operatorClass. Do not pass the whole user instruction or graph-summary task text."
                ),
            }
            if suggested_next_call is not None:
                payload["suggested_next_call"] = suggested_next_call
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload=payload,
                summary="Rejected non-node query for resolve_nodes",
                error="`resolve_nodes.query` must be a node identifier or node-like search string, not a full task sentence.",
            )

        node_records = [self._node_record(node) for node in self._all_nodes()]
        query_lower = query.lower()
        exact_id = [record for record in node_records if record["node_id"].lower() == query_lower]
        exact_name = [record for record in node_records if record["node_name"].lower() == query_lower]
        exact_label = [record for record in node_records if record["label"].lower() == query_lower]
        exact_class = [
            record
            for record in node_records
            if record["operator_class"].lower() == query_lower or record["service_class"].lower() == query_lower
        ]
        query_contains_exact_id = [
            record for record in node_records if record["node_id"] and record["node_id"].lower() in query_lower
        ]
        query_contains_exact_name = [
            record for record in node_records if record["node_name"] and record["node_name"].lower() in query_lower
        ]
        query_contains_exact_label = [
            record for record in node_records if record["label"] and record["label"].lower() in query_lower
        ]
        fuzzy = [
            record
            for record in node_records
            if query_lower in record["node_id"].lower()
            or query_lower in record["node_name"].lower()
            or query_lower in record["label"].lower()
            or query_lower in record["operator_class"].lower()
            or query_lower in record["service_class"].lower()
        ]
        token_overlap = [
            record for record in node_records if _node_record_matches_query_tokens(record=record, query=query_lower)
        ]
        matches: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for items, match_reason in (
            (exact_id, "exact_node_id"),
            (exact_name, "exact_node_name"),
            (exact_label, "exact_label"),
            (exact_class, "exact_class"),
            (query_contains_exact_id, "query_contains_exact_node_id"),
            (query_contains_exact_name, "query_contains_exact_node_name"),
            (query_contains_exact_label, "query_contains_exact_label"),
            (fuzzy, "fuzzy"),
            (token_overlap, "token_overlap"),
        ):
            for item in items:
                node_id = str(item["node_id"])
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
                match = dict(item)
                match["match_reason"] = match_reason
                matches.append(match)
                if len(matches) >= limit:
                    break
            if len(matches) >= limit:
                break

        payload = {
            "query": query,
            "matches": matches,
            "returned_count": len(matches),
        }
        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=True,
            payload=payload,
            summary=f"Resolved {len(matches)} nodes for {query!r}",
        )

    def _get_node_overview(self, call: AgentToolCall) -> AgentToolResult:
        node_ids = _as_string_list(call.arguments.get("node_ids"))
        if not node_ids:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node ids missing",
                error="`node_ids` is required",
            )
        node_map = self._nodes_by_id()
        overviews: list[dict[str, Any]] = []
        missing_node_ids: list[str] = []
        for node_id in node_ids:
            node = node_map.get(node_id)
            if node is None:
                missing_node_ids.append(node_id)
                continue
            spec = _node_spec(node)
            if spec is None:
                missing_node_ids.append(node_id)
                continue
            input_neighbors = self._neighbor_count(node, direction="in")
            output_neighbors = self._neighbor_count(node, direction="out")
            overviews.append(
                {
                    "node_id": node_id,
                    "node_name": _node_display_name(node, spec),
                    "node_kind": _node_kind(spec),
                    "service_class": _service_class(spec),
                    "operator_class": _operator_class(spec),
                    "description": _text_or_empty(spec.description),
                    "instance_purpose": _node_instance_purpose(node),
                    "data_in_port_names": [_raw_port_name(_port_name(port)) for port in list(spec.dataInPorts or [])],
                    "data_out_port_names": [_raw_port_name(_port_name(port)) for port in list(spec.dataOutPorts or [])],
                    "state_field_names": [str(field.name or "").strip() for field in _effective_state_fields(node, spec)],
                    "incoming_neighbor_count": input_neighbors,
                    "outgoing_neighbor_count": output_neighbors,
                }
            )
        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=bool(overviews),
            payload={"nodes": overviews, "missing_node_ids": missing_node_ids},
            summary=f"Loaded overview for {len(overviews)} nodes",
            error="" if overviews else "No requested nodes were found",
        )

    def _get_node_spec(self, call: AgentToolCall) -> AgentToolResult:
        node_id = str(call.arguments.get("node_id") or "").strip()
        sections = _as_string_list(call.arguments.get("sections"))
        valid_sections = {"data_in_ports", "data_out_ports", "state_fields", "commands"}
        if not node_id:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node id missing",
                error="`node_id` is required",
            )
        if not sections:
            sections = ["data_in_ports", "data_out_ports", "state_fields", "commands"]
        unknown_sections = [section for section in sections if section not in valid_sections]
        if unknown_sections:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Unknown spec section requested",
                error=f"Unknown sections: {', '.join(unknown_sections)}",
            )
        node = self._nodes_by_id().get(node_id)
        if node is None:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node not found",
                error=f"Unknown node_id: {node_id}",
            )
        spec = _node_spec(node)
        if spec is None:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node spec unavailable",
                error=f"Node {node_id!r} has no readable spec",
            )

        payload: dict[str, Any] = {
            "node_id": node_id,
            "node_name": _node_display_name(node, spec),
            "node_kind": _node_kind(spec),
            "service_class": _service_class(spec),
            "operator_class": _operator_class(spec),
            "sections": sections,
        }
        if "data_in_ports" in sections:
            payload["data_in_ports"] = [self._port_spec_payload(port) for port in list(spec.dataInPorts or [])]
        if "data_out_ports" in sections:
            payload["data_out_ports"] = [self._port_spec_payload(port) for port in list(spec.dataOutPorts or [])]
        if "state_fields" in sections:
            payload["state_fields"] = [self._state_field_spec_payload(field) for field in _effective_state_fields(node, spec)]
        if "commands" in sections:
            payload["commands"] = [self._command_payload(command) for command in list(spec.commands or [])]

        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=True,
            payload=payload,
            summary=f"Loaded spec sections for {payload['node_name']}",
        )

    def _get_state_field_details(self, call: AgentToolCall) -> AgentToolResult:
        node_id = str(call.arguments.get("node_id") or "").strip()
        field_names = _as_string_list(call.arguments.get("field_names"))
        include_values_raw = call.arguments.get("include_values")
        max_value_chars_raw = call.arguments.get("max_value_chars")
        include_values = True if include_values_raw is None else bool(include_values_raw)
        max_value_chars = _DEFAULT_MAX_VALUE_CHARS
        if isinstance(max_value_chars_raw, int):
            max_value_chars = max(80, min(8000, max_value_chars_raw))
        if not node_id:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node id missing",
                error="`node_id` is required",
            )
        if not field_names:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Field names missing",
                error="`field_names` is required",
            )
        node = self._nodes_by_id().get(node_id)
        if node is None:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node not found",
                error=f"Unknown node_id: {node_id}",
            )
        spec = _node_spec(node)
        if spec is None:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node spec unavailable",
                error=f"Node {node_id!r} has no readable spec",
            )
        state_fields = _effective_state_fields(node, spec)
        field_by_name = {str(field.name or "").strip(): field for field in state_fields if str(field.name or "").strip()}
        details: list[dict[str, Any]] = []
        missing_field_names: list[str] = []
        for field_name in field_names:
            field = field_by_name.get(field_name)
            if field is None:
                missing_field_names.append(field_name)
                continue
            detail = self._state_field_spec_payload(field)
            if include_values:
                detail["current_value"] = self._value_preview(node=node, field_name=field_name, max_value_chars=max_value_chars)
            details.append(detail)
        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=bool(details),
            payload={"node_id": node_id, "fields": details, "missing_field_names": missing_field_names},
            summary=f"Loaded {len(details)} state fields",
            error="" if details else "No requested state fields were found",
        )

    def _get_connections(self, call: AgentToolCall) -> AgentToolResult:
        node_ids = _as_string_list(call.arguments.get("node_ids"))
        direction = str(call.arguments.get("direction") or "both").strip().lower()
        if not node_ids:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Node ids missing",
                error="`node_ids` is required",
            )
        if direction not in {"in", "out", "both"}:
            return AgentToolResult(
                tool_name=call.tool_name,
                arguments=call.arguments,
                success=False,
                payload={},
                summary="Invalid connection direction",
                error=f"Unsupported direction: {direction}",
            )
        node_map = self._nodes_by_id()
        connections: list[dict[str, Any]] = []
        neighbors: dict[str, dict[str, Any]] = {}
        seen_edges: set[tuple[str, str, str, str]] = set()
        missing_node_ids: list[str] = []
        for node_id in node_ids:
            node = node_map.get(node_id)
            if node is None:
                missing_node_ids.append(node_id)
                continue
            if direction in {"in", "both"}:
                self._append_connections(
                    node=node,
                    source_ports=_input_ports(node),
                    seen_edges=seen_edges,
                    connections=connections,
                    neighbors=neighbors,
                    flip_direction=True,
                )
            if direction in {"out", "both"}:
                self._append_connections(
                    node=node,
                    source_ports=_output_ports(node),
                    seen_edges=seen_edges,
                    connections=connections,
                    neighbors=neighbors,
                    flip_direction=False,
                )
        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=bool(connections),
            payload={
                "node_ids": node_ids,
                "direction": direction,
                "connections": connections,
                "neighbors": list(neighbors.values()),
                "missing_node_ids": missing_node_ids,
            },
            summary=f"Loaded {len(connections)} connections",
            error="" if connections else "No connections found for the requested nodes",
        )

    def _append_connections(
        self,
        *,
        node: Any,
        source_ports: list[Any],
        seen_edges: set[tuple[str, str, str, str]],
        connections: list[dict[str, Any]],
        neighbors: dict[str, dict[str, Any]],
        flip_direction: bool,
    ) -> None:
        for source_port in source_ports:
            source_port_name = _raw_port_name(_port_name(source_port))
            for connected_port in _connected_ports(source_port):
                source_node = _port_node(source_port)
                target_node = _port_node(connected_port)
                if source_node is None or target_node is None:
                    continue
                from_node = target_node if flip_direction else source_node
                to_node = source_node if flip_direction else target_node
                from_port = _raw_port_name(_port_name(connected_port)) if flip_direction else source_port_name
                to_port = source_port_name if flip_direction else _raw_port_name(_port_name(connected_port))
                from_node_id = _node_id(from_node)
                to_node_id = _node_id(to_node)
                if not from_node_id or not to_node_id:
                    continue
                edge_key = (from_node_id, from_port, to_node_id, to_port)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                from_spec = _node_spec(from_node)
                to_spec = _node_spec(to_node)
                connections.append(
                    {
                        "from_node_id": from_node_id,
                        "from_node_name": _node_display_name(from_node, from_spec),
                        "from_node_kind": _node_kind(from_spec),
                        "from_port": from_port,
                        "to_node_id": to_node_id,
                        "to_node_name": _node_display_name(to_node, to_spec),
                        "to_node_kind": _node_kind(to_spec),
                        "to_port": to_port,
                    }
                )
                neighbor = target_node if _node_id(target_node) != _node_id(node) else source_node
                neighbor_spec = _node_spec(neighbor)
                neighbor_id = _node_id(neighbor)
                if neighbor_id and neighbor_id not in neighbors:
                    neighbors[neighbor_id] = {
                        "node_id": neighbor_id,
                        "node_name": _node_display_name(neighbor, neighbor_spec),
                        "node_kind": _node_kind(neighbor_spec),
                        "service_class": _service_class(neighbor_spec),
                        "operator_class": _operator_class(neighbor_spec),
                    }

    def _node_record(self, node: Any) -> dict[str, Any]:
        spec = _node_spec(node)
        return {
            "node_id": _node_id(node),
            "node_name": _node_display_name(node, spec),
            "label": _text_or_empty(spec.label) if spec is not None else "",
            "node_kind": _node_kind(spec),
            "service_class": _service_class(spec),
            "operator_class": _operator_class(spec),
            "description": _text_or_empty(spec.description) if spec is not None else "",
            "instance_purpose": _node_instance_purpose(node),
        }

    def _suggested_next_call_for_resolve_query(self, query: str) -> dict[str, Any] | None:
        query_lower = str(query or "").strip().lower()
        if not query_lower:
            return None
        node_records = [self._node_record(node) for node in self._all_nodes()]
        matched_record: dict[str, Any] | None = None
        for record in node_records:
            node_id = str(record["node_id"]).lower()
            node_name = str(record["node_name"]).lower()
            label = str(record["label"]).lower()
            if node_id and node_id in query_lower:
                matched_record = record
                break
            if node_name and node_name in query_lower:
                matched_record = record
                break
            if label and label in query_lower:
                matched_record = record
                break
        if matched_record is None:
            return None
        node_id = str(matched_record["node_id"])
        if _looks_like_port_schema_question(query_lower):
            sections = ["data_out_ports"] if _looks_like_output_question(query_lower) else ["data_in_ports", "data_out_ports"]
            return {
                "tool_name": "get_node_spec",
                "arguments": {
                    "node_id": node_id,
                    "sections": sections,
                },
                "reason": "Known node_id already present in query; inspect port schema directly instead of resolving nodes again.",
            }
        return {
            "tool_name": "get_node_overview",
            "arguments": {"node_ids": [node_id]},
            "reason": "Known node_id already present in query; inspect the node directly instead of resolving nodes again.",
        }

    def _nodes_by_id(self) -> dict[str, Any]:
        node_map: dict[str, Any] = {}
        for node in self._all_nodes():
            node_id = _node_id(node)
            if node_id:
                node_map[node_id] = node
        return node_map

    def _all_nodes(self) -> list[Any]:
        graph = self._studio_graph
        if graph is None:
            raise RuntimeError("Studio graph is not available")
        try:
            nodes = list(graph.all_nodes() or [])
        except AttributeError as exc:
            raise RuntimeError("Studio graph does not expose all_nodes()") from exc
        except (RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Failed to enumerate graph nodes: {exc}") from exc
        nodes.sort(key=lambda item: (_node_display_name(item, _node_spec(item)).lower(), _node_id(item)))
        return nodes

    @staticmethod
    def _port_spec_payload(port: Any) -> dict[str, Any]:
        return {
            "name": str(port.name or "").strip(),
            "required": bool(port.required) if port.required is not None else True,
            "description": _text_or_empty(port.description),
            "value_schema": dump_graph_value_json(port.valueSchema),
        }

    @staticmethod
    def _state_field_spec_payload(field: Any) -> dict[str, Any]:
        return {
            "name": str(field.name or "").strip(),
            "label": _text_or_empty(field.label),
            "access": _text_or_empty(field.access),
            "required": bool(field.required) if field.required is not None else False,
            "show_on_node": bool(field.showOnNode) if field.showOnNode is not None else False,
            "description": _text_or_empty(field.description),
            "ui_language": _text_or_empty(field.uiLanguage).lower(),
            "value_schema": dump_graph_value_json(field.valueSchema),
        }

    @staticmethod
    def _command_payload(command: Any) -> dict[str, Any]:
        payload = dump_graph_value_json(command)
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(json.dumps(dump_json(command, mode="json"), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Failed to serialize command payload: {exc}") from exc

    @staticmethod
    def _value_preview(*, node: Any, field_name: str, max_value_chars: int) -> dict[str, Any]:
        try:
            raw_value = node.get_property(field_name)
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "value_kind": "error",
                "preview_text": "",
                "truncated": False,
                "omitted": True,
                "omitted_reason": f"Failed to read current value: {type(exc).__name__}: {exc}",
            }
        if isinstance(raw_value, msgspec.UnsetType):
            raw_value = None
        if raw_value is None or isinstance(raw_value, bool):
            return {
                "value_kind": "scalar",
                "preview_text": json.dumps(raw_value, ensure_ascii=False),
                "truncated": False,
                "omitted": False,
                "omitted_reason": "",
            }
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return {
                "value_kind": "scalar",
                "preview_text": json.dumps(raw_value, ensure_ascii=False),
                "truncated": False,
                "omitted": False,
                "omitted_reason": "",
            }
        if isinstance(raw_value, (bytes, bytearray, memoryview)):
            return {
                "value_kind": "binary",
                "preview_text": "",
                "truncated": False,
                "omitted": True,
                "omitted_reason": "Binary payload omitted",
            }
        if _looks_like_binary_payload(raw_value):
            return {
                "value_kind": "binary",
                "preview_text": "",
                "truncated": False,
                "omitted": True,
                "omitted_reason": "Binary/blob payload omitted",
            }
        if isinstance(raw_value, str) and _is_probable_base64_blob(raw_value):
            return {
                "value_kind": "base64",
                "preview_text": "",
                "truncated": False,
                "omitted": True,
                "omitted_reason": "Base64 blob omitted",
            }

        payload = dump_graph_value_json(raw_value)
        if isinstance(payload, (dict, list)):
            preview_source = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            value_kind = "json"
        elif isinstance(payload, str):
            preview_source = payload
            value_kind = "string"
        else:
            preview_source = json.dumps(payload, ensure_ascii=False, default=str)
            value_kind = "scalar"
        preview_text, truncated = _truncate_text(preview_source, max_chars=max_value_chars, max_lines=_MAX_PREVIEW_LINES)
        return {
            "value_kind": value_kind,
            "preview_text": preview_text,
            "truncated": truncated,
            "omitted": False,
            "omitted_reason": "",
        }

    @staticmethod
    def _neighbor_count(node: Any, *, direction: str) -> int:
        neighbors: set[str] = set()
        source_ports = _input_ports(node) if direction == "in" else _output_ports(node)
        for source_port in source_ports:
            for connected_port in _connected_ports(source_port):
                neighbor = _port_node(connected_port)
                neighbor_id = _node_id(neighbor)
                if neighbor_id:
                    neighbors.add(neighbor_id)
        return len(neighbors)


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _looks_like_task_instruction(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if len(text) > _MAX_RESOLVE_QUERY_LENGTH:
        return True
    lowered = text.lower()
    instruction_signals = (
        "summarize",
        "summary",
        "describe",
        "explain",
        "provided",
        "current graph",
        "node graph",
        "dataflow",
        "inputs to outputs",
        "what does",
        "please",
    )
    if any(signal in lowered for signal in instruction_signals):
        return True
    word_count = len(text.split())
    if word_count >= 8:
        return True
    return False


def _node_record_matches_query_tokens(*, record: dict[str, Any], query: str) -> bool:
    query_tokens = _query_tokens(query)
    if len(query_tokens) < 2:
        return False
    candidate_texts = (
        str(record.get("node_name") or "").lower(),
        str(record.get("label") or "").lower(),
        str(record.get("service_class") or "").lower(),
        str(record.get("operator_class") or "").lower(),
    )
    for candidate_text in candidate_texts:
        if not candidate_text:
            continue
        candidate_tokens = _query_tokens(candidate_text)
        if candidate_tokens and candidate_tokens.issubset(query_tokens):
            return True
    return False


def _query_tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    tokens: set[str] = set()
    token_chars: list[str] = []
    for char in raw:
        if char.isalnum():
            token_chars.append(char)
            continue
        if token_chars:
            token = "".join(token_chars)
            if len(token) >= 2:
                tokens.add(token)
            token_chars = []
    if token_chars:
        token = "".join(token_chars)
        if len(token) >= 2:
            tokens.add(token)
    return tokens


def _looks_like_port_schema_question(query: str) -> bool:
    lowered = str(query or "").lower()
    signals = ("port", "ports", "output", "outputs", "input", "inputs", "schema", "type", "datatype", "data type")
    return any(signal in lowered for signal in signals)


def _looks_like_output_question(query: str) -> bool:
    lowered = str(query or "").lower()
    signals = ("output", "outputs", "detections", "result", "out")
    return any(signal in lowered for signal in signals)


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


def _node_instance_purpose(node: Any) -> str:
    try:
        return str(node.nodePurpose or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


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


def _truncate_text(text: str, *, max_chars: int, max_lines: int) -> tuple[str, bool]:
    lines = str(text or "").splitlines()
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
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"
    return all(char in allowed for char in stripped)


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


__all__ = [
    "AgentToolCall",
    "AgentToolName",
    "AgentToolResult",
    "GraphAgentToolExecutor",
]
