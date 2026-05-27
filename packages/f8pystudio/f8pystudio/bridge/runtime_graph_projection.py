from __future__ import annotations

import logging
from typing import Callable

import msgspec

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeGraphMeta, F8RuntimeNode, F8StateSpec
from f8pysdk.command import is_hidden_command_state_field
from f8pysdk.f8_naming import ensure_token

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from f8pystudio.bridge.remote_state_watcher import WatchTarget

logger = logging.getLogger(__name__)
_GRAPH_PROJECTION_ACCESS_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


def dedupe_fields(fields: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for field in fields:
        if field in seen:
            continue
        seen.add(field)
        ordered.append(field)
    return tuple(ordered)


def _coerce_runtime_nodes(value: object, *, context: str) -> list[object]:
    if value is None or isinstance(value, msgspec.UnsetType):
        return []
    try:
        return list(value)
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to read runtime nodes context=%s", context, exc_info=True)
        return []


def _coerce_runtime_edges(value: object, *, context: str) -> list[object]:
    if value is None or isinstance(value, msgspec.UnsetType):
        return []
    try:
        return list(value)
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to read runtime edges context=%s", context, exc_info=True)
        return []


def _runtime_graph_text(value: object, *, default: str, context: str) -> str:
    try:
        text = str(value or "").strip()
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to read runtime graph text context=%s", context, exc_info=True)
        return default
    return text or default


def _runtime_node_state_fields(node: object, *, context: str) -> list[object]:
    if isinstance(node, F8RuntimeNode):
        return list(node.stateFields or [])
    try:
        state_fields = node.stateFields  # type: ignore[attr-defined]
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to read runtime node state fields context=%s", context, exc_info=True)
        return []
    if state_fields is None or isinstance(state_fields, msgspec.UnsetType):
        return []
    try:
        return list(state_fields)
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to coerce runtime node state fields context=%s", context, exc_info=True)
        return []


def _state_field_name(field_spec: object) -> str:
    if isinstance(field_spec, F8StateSpec):
        return str(field_spec.name or "").strip()
    try:
        return str(field_spec.name or "").strip()  # type: ignore[attr-defined]
    except _GRAPH_PROJECTION_ACCESS_ERRORS:
        logger.debug("failed to read runtime state field name", exc_info=True)
        return ""


def build_studio_runtime_graph(compiled: CompiledRuntimeGraphs, *, studio_service_id: str) -> F8RuntimeGraph:
    """
    Build studio runtime graph without monitor node injection.
    """
    studio_sub = compiled.per_service.get(str(studio_service_id))
    if studio_sub is None:
        base_nodes = []
        base_edges = []
    else:
        base_nodes = _coerce_runtime_nodes(studio_sub.nodes, context="studio-subgraph")
        base_edges = _coerce_runtime_edges(studio_sub.edges, context="studio-subgraph")
    graph_id = _runtime_graph_text(compiled.global_graph.graphId, default="studio", context="global-graph-id")
    revision = _runtime_graph_text(compiled.global_graph.revision, default="1", context="global-revision")
    return F8RuntimeGraph(
        graphId=graph_id,
        revision=revision,
        meta=F8RuntimeGraphMeta(source="studio"),
        services=[],
        nodes=[*base_nodes],
        edges=[*base_edges],
    )


def build_remote_watch_targets(
    compiled: CompiledRuntimeGraphs,
    *,
    on_invalid_target: Callable[[str], None] | None = None,
) -> tuple[WatchTarget, ...]:
    targets: list[WatchTarget] = []
    nodes = _coerce_runtime_nodes(compiled.global_graph.nodes, context="global-graph")
    for node in nodes:
        try:
            service_id = ensure_token(str(node.serviceId or ""), label="service_id")
            node_id = ensure_token(str(node.nodeId or ""), label="node_id")
        except ValueError as exc:
            if on_invalid_target is not None:
                on_invalid_target(f"skip invalid remote watch target: {type(exc).__name__}: {exc}")
            continue

        candidates: list[str] = []
        state_fields = _runtime_node_state_fields(node, context=f"remote-watch:{service_id}:{node_id}")
        for field_spec in state_fields:
            name = _state_field_name(field_spec)
            if name:
                if is_hidden_command_state_field(name):
                    continue
                candidates.append(name)

        if "svcId" not in candidates:
            candidates.append("svcId")
        operator_class = str(node.operatorClass or "").strip()
        if operator_class and "operatorId" not in candidates:
            candidates.append("operatorId")

        targets.append(
            WatchTarget(
                service_id=service_id,
                node_id=node_id,
                fields=dedupe_fields(candidates),
            )
        )
    return tuple(sorted(targets, key=lambda target: (target.service_id, target.node_id, target.fields)))


def build_local_state_field_index(
    compiled: CompiledRuntimeGraphs,
    *,
    studio_service_id: str,
) -> dict[str, tuple[str, ...]]:
    studio_graph = compiled.per_service.get(str(studio_service_id))
    if studio_graph is None:
        return {}
    output: dict[str, tuple[str, ...]] = {}
    for node in _coerce_runtime_nodes(studio_graph.nodes, context="studio-local-state-index"):
        node_id = str(node.nodeId or "").strip()
        if not node_id:
            continue
        field_names: list[str] = []
        for field_spec in _runtime_node_state_fields(node, context=f"local-state-index:{node_id}"):
            name = _state_field_name(field_spec)
            if name and not is_hidden_command_state_field(name):
                field_names.append(name)
        output[node_id] = dedupe_fields(field_names)
    return output
