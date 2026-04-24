from __future__ import annotations

import json
from typing import Any

from f8pysdk.codec import dump_json

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs


def _normalize_spec_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(str(k) for k in payload.keys()):
            normalized[key] = _normalize_spec_payload(payload[key])
        return normalized
    if isinstance(payload, list):
        return [_normalize_spec_payload(item) for item in payload]
    return payload


def _normalized_named_spec_sort_key(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    name = str(payload.get("name") or "")
    payload_kind = str(payload.get("type") or payload.get("access") or "")
    return (name, payload_kind)


def _normalized_service_sort_key(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("", "")
    return (str(payload.get("serviceId") or ""), str(payload.get("serviceClass") or ""))


def _normalized_node_sort_key(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return ("", "", "")
    return (
        str(payload.get("serviceId") or ""),
        str(payload.get("nodeId") or ""),
        str(payload.get("operatorClass") or ""),
    )


def _normalized_edge_sort_key(payload: Any) -> tuple[str, str, str, str, str, str]:
    if not isinstance(payload, dict):
        return ("", "", "", "", "", "")
    return (
        str(payload.get("kind") or ""),
        str(payload.get("fromServiceId") or ""),
        str(payload.get("fromOperatorId") or ""),
        str(payload.get("fromPort") or ""),
        str(payload.get("toServiceId") or ""),
        str(payload.get("toPort") or ""),
    )


def _normalize_service_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key in sorted(str(k) for k in payload.keys()):
        normalized[key] = _normalize_spec_payload(payload[key])
    return normalized


def _normalize_node_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key in sorted(str(k) for k in payload.keys()):
        if key == "stateValues":
            continue
        value = payload[key]
        if key in {"execInPorts", "execOutPorts"} and isinstance(value, list):
            normalized[key] = sorted(str(item) for item in value)
            continue
        if key in {"dataInPorts", "dataOutPorts", "stateFields"} and isinstance(value, list):
            normalized[key] = sorted(
                (_normalize_spec_payload(item) for item in value),
                key=_normalized_named_spec_sort_key,
            )
            continue
        normalized[key] = _normalize_spec_payload(value)
    return normalized


def _normalize_edge_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key in sorted(str(k) for k in payload.keys()):
        if key == "edgeId":
            continue
        normalized[key] = _normalize_spec_payload(payload[key])
    return normalized


def build_compiled_deploy_snapshot(compiled: CompiledRuntimeGraphs) -> dict[str, Any]:
    graph_payload = dump_json(compiled.global_graph, mode="json", by_alias=True)
    if not isinstance(graph_payload, dict):
        return {"services": [], "nodes": [], "edges": []}

    raw_services = graph_payload.get("services")
    raw_nodes = graph_payload.get("nodes")
    raw_edges = graph_payload.get("edges")

    services = []
    if isinstance(raw_services, list):
        services = sorted(
            (_normalize_service_payload(item) for item in raw_services),
            key=_normalized_service_sort_key,
        )

    nodes = []
    if isinstance(raw_nodes, list):
        nodes = sorted(
            (_normalize_node_payload(item) for item in raw_nodes),
            key=_normalized_node_sort_key,
        )

    edges = []
    if isinstance(raw_edges, list):
        edges = sorted(
            (_normalize_edge_payload(item) for item in raw_edges),
            key=_normalized_edge_sort_key,
        )

    return {
        "services": services,
        "nodes": nodes,
        "edges": edges,
    }


def build_compiled_deploy_fingerprint(compiled: CompiledRuntimeGraphs) -> str:
    snapshot = build_compiled_deploy_snapshot(compiled)
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


__all__ = [
    "build_compiled_deploy_fingerprint",
    "build_compiled_deploy_snapshot",
]
