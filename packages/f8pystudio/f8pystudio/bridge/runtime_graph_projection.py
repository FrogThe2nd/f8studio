from __future__ import annotations

from typing import Callable

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeGraphMeta
from f8pysdk.command import is_hidden_command_state_field
from f8pysdk.nats_naming import ensure_token

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from f8pystudio.bridge.remote_state_watcher import WatchTarget


def dedupe_fields(fields: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for field in fields:
        if field in seen:
            continue
        seen.add(field)
        ordered.append(field)
    return tuple(ordered)


def build_studio_runtime_graph(compiled: CompiledRuntimeGraphs, *, studio_service_id: str) -> F8RuntimeGraph:
    """
    Build studio runtime graph without monitor node injection.
    """
    studio_sub = compiled.per_service.get(str(studio_service_id))
    if studio_sub is None:
        base_nodes = []
        base_edges = []
    else:
        try:
            base_nodes = list(studio_sub.nodes or [])
        except Exception:
            base_nodes = []
        try:
            base_edges = list(studio_sub.edges or [])
        except Exception:
            base_edges = []
    try:
        graph_id = str(compiled.global_graph.graphId or "studio")
    except Exception:
        graph_id = "studio"
    try:
        revision = str(compiled.global_graph.revision or "1")
    except Exception:
        revision = "1"
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
    try:
        nodes = list(compiled.global_graph.nodes or [])
    except Exception:
        nodes = []
    for node in nodes:
        try:
            service_id = ensure_token(str(node.serviceId or ""), label="service_id")
            node_id = ensure_token(str(node.nodeId or ""), label="node_id")
        except ValueError as exc:
            if on_invalid_target is not None:
                on_invalid_target(f"skip invalid remote watch target: {type(exc).__name__}: {exc}")
            continue

        candidates: list[str] = []
        try:
            state_fields = list(node.stateFields or [])
        except Exception:
            state_fields = []
        for field_spec in state_fields:
            try:
                name = str(field_spec.name or "").strip()
            except Exception:
                name = ""
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
    for node in list(studio_graph.nodes or []):
        node_id = str(node.nodeId or "").strip()
        if not node_id:
            continue
        field_names: list[str] = []
        for field_spec in list(node.stateFields or []):
            name = str(field_spec.name or "").strip()
            if name and not is_hidden_command_state_field(name):
                field_names.append(name)
        output[node_id] = dedupe_fields(field_names)
    return output
