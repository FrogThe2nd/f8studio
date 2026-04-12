from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from .session_schema import extract_layout, wrap_layout_for_save


class _SelectedNodeProtocol(Protocol):
    id: object


def component_selected_node_id(node: object) -> str:
    try:
        return str(cast(_SelectedNodeProtocol, node).id or "").strip()
    except (AttributeError, RuntimeError, TypeError):
        return ""


def collect_component_selected_node_ids(selected_nodes: Iterable[object]) -> set[str]:
    selected_node_ids: set[str] = set()
    for node in selected_nodes:
        node_id = component_selected_node_id(node)
        if node_id:
            selected_node_ids.add(node_id)
    return selected_node_ids


def _connection_endpoint_node_id(endpoint: object) -> str:
    if not isinstance(endpoint, list) or not endpoint:
        return ""
    return str(endpoint[0] or "").strip()


def trim_component_publish_payload_to_selected_nodes(
    *,
    payload: dict[str, object],
    selected_node_ids: set[str],
) -> dict[str, object]:
    layout = extract_layout(payload)
    raw_nodes = layout.get("nodes")
    raw_connections = layout.get("connections")

    kept_nodes: dict[str, object] = {}
    if isinstance(raw_nodes, dict):
        for node_id, node_data in raw_nodes.items():
            normalized_node_id = str(node_id or "").strip()
            if normalized_node_id in selected_node_ids:
                kept_nodes[normalized_node_id] = node_data

    kept_connections: list[object] = []
    if isinstance(raw_connections, list):
        for connection in raw_connections:
            if not isinstance(connection, dict):
                continue
            source_node_id = _connection_endpoint_node_id(connection.get("out"))
            target_node_id = _connection_endpoint_node_id(connection.get("in"))
            if source_node_id in selected_node_ids and target_node_id in selected_node_ids:
                kept_connections.append(connection)

    return wrap_layout_for_save(
        {
            **layout,
            "nodes": kept_nodes,
            "connections": kept_connections,
        }
    )
