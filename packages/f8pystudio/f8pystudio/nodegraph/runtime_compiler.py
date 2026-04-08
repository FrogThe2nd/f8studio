from __future__ import annotations

from f8pysdk.msgspec_codec import copy_model, dump_json
import enum
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

import msgspec

from f8pysdk.specs import (
    F8DataPortSpec,
    F8Edge,
    F8EdgeDirection,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8OperatorSpec,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
    F8RuntimeGraph,
    F8RuntimeNode,
    F8RuntimeService,
)
from f8pysdk.builtin_state_fields import (
    operator_state_fields_with_builtins,
    service_state_fields_with_builtins,
)
from f8pysdk.command_state import (
    command_input_state_field,
    command_output_state_field,
    hidden_command_state_specs,
    parse_command_port_name,
)
from f8pysdk.rungraph_validation import (
    validate_data_edges_or_raise,
    validate_exec_edges_or_raise,
    validate_state_edge_targets_writable_or_raise,
    validate_state_edges_or_raise,
)
from f8pysdk.specs import boolean_schema
from f8pysdk.specs import integer_schema
from f8pysdk.specs import any_schema
from f8pysdk.nats_naming import ensure_token

from f8pystudio.studio_specs.registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio.studio_specs.registry import STUDIO_SERVICE_ID
from ..operators.patch_hub import OPERATOR_CLASS as PATCH_HUB_OPERATOR_CLASS


logger = logging.getLogger(__name__)
PYENGINE_SERVICE_CLASS = "f8.pyengine"
AUTO_PULL_OPERATOR_CLASS = "f8.pull"


def _port_kind(name: str) -> F8EdgeKindEnum | None:
    n = str(name or "")
    if n.startswith("[E]") or n.endswith("[E]"):
        return F8EdgeKindEnum.exec
    if n.startswith("[D]") or n.endswith("[D]"):
        return F8EdgeKindEnum.data
    if n.startswith("[S]") or n.endswith("[S]"):
        return F8EdgeKindEnum.state
    if n.startswith("[C]") or n.endswith("[C]"):
        return F8EdgeKindEnum.state
    return None


def _raw_port_name(name: str) -> str:
    n = str(name or "")
    parsed_command = parse_command_port_name(n)
    if parsed_command is not None:
        is_in, command_name = parsed_command
        if is_in:
            return command_input_state_field(command_name)
        return command_output_state_field(command_name)
    for prefix in ("[E]", "[D]", "[S]"):
        if n.startswith(prefix):
            n = n[len(prefix) :]
    for suffix in ("[E]", "[D]", "[S]"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip()

def _port_name(port: Any) -> str:
    """
    NodeGraphQt `Port` exposes `name()` (method), not `.name` (attribute).
    """
    try:
        return str(port.name() or "")
    except Exception:
        return ""


def _node_name(node: Any) -> str:
    """
    NodeGraphQt `BaseNode` exposes `name()` (method), not `.name` (attribute).
    """
    try:
        return str(node.name() or "")
    except Exception:
        return ""


def _runtime_node_id(node: Any) -> str:
    return ensure_token(str(node.id), label="node_id")


def _runtime_service_id(node: Any) -> str:
    try:
        spec = node.spec
    except Exception:
        spec = None
    # Containers represent service instances themselves: their id is the serviceId.
    if isinstance(spec, F8ServiceSpec):
        return ensure_token(str(node.id), label="service_id")
    # Studio operators belong to a fixed local service id.
    if isinstance(spec, F8OperatorSpec) and str(spec.serviceClass or "") == STUDIO_SERVICE_CLASS:
        return STUDIO_SERVICE_ID
    # Operators are bound to a container: svcId points at the container id.
    return ensure_token(str(node.svcId), label="service_id")


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return ensure_token(f"{prefix}_{digest}", label="node_id")


def _as_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        out = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        out = int(default)
    if out < minimum:
        out = minimum
    if out > maximum:
        out = maximum
    return out


def _coerce_state_payload_value(value: Any) -> tuple[bool, Any]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True, value
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        for item in value:
            ok, normalized = _coerce_state_payload_value(item)
            if not ok:
                return False, None
            out.append(normalized)
        return True, out
    if isinstance(value, dict):
        out_obj: dict[str, Any] = {}
        for key, item in value.items():
            ok, normalized = _coerce_state_payload_value(item)
            if not ok:
                return False, None
            out_obj[str(key)] = normalized
        return True, out_obj
    if isinstance(value, enum.Enum):
        return _coerce_state_payload_value(value.value)
    try:
        dumped = dump_json(value, mode="json")
    except (AttributeError, TypeError, ValueError):
        return False, None
    if dumped is value:
        return False, None
    return _coerce_state_payload_value(dumped)


def _inject_studio_auto_pull_triggers(graph: F8RuntimeGraph) -> tuple[F8RuntimeGraph, list[str]]:
    """
    Inject hidden pull trigger nodes for eligible Studio auto-sampling consumers.

    Trigger-only mode:
    - Keep original source->studio cross edges unchanged.
    - Add source->f8.pull data edges inside the source pyengine service.
    """
    warnings: list[str] = []
    services_by_id: dict[str, F8RuntimeService] = {str(s.serviceId): s for s in list(graph.services or [])}
    nodes_by_id: dict[str, F8RuntimeNode] = {str(n.nodeId): n for n in list(graph.nodes or [])}

    by_source: dict[tuple[str, str, str], dict[str, Any]] = {}

    for edge in list(graph.edges or []):
        if edge.kind != F8EdgeKindEnum.data:
            continue
        from_service_id = str(edge.fromServiceId)
        to_service_id = str(edge.toServiceId)
        if from_service_id == to_service_id:
            continue
        if to_service_id != STUDIO_SERVICE_ID:
            continue
        if edge.fromOperatorId is None or edge.toOperatorId is None:
            continue
        src_node_id = str(edge.fromOperatorId)
        src_node = nodes_by_id.get(src_node_id)
        if src_node is not None and str(src_node.operatorClass or "") == AUTO_PULL_OPERATOR_CLASS:
            continue

        dst_node_id = str(edge.toOperatorId)
        dst_node = nodes_by_id.get(dst_node_id)
        if dst_node is None:
            continue

        raw_state_values = dict(dst_node.stateValues or {})
        state_values: dict[str, Any] = {}
        for key, item in raw_state_values.items():
            ok, normalized = _coerce_state_payload_value(item)
            if ok:
                state_values[str(key)] = normalized
        mode = str(state_values.get("upstreamSamplingMode", "passive") or "").strip().lower()
        if mode != "auto":
            continue

        interval_ms = _as_int(
            state_values.get("upstreamSampleIntervalMs", 100),
            default=100,
            minimum=8,
            maximum=5000,
        )
        src_key = (from_service_id, str(edge.fromOperatorId), str(edge.fromPort))

        group = by_source.get(src_key)
        if group is None:
            group = {
                "sample_interval_ms": interval_ms,
                "consumers": set(),
            }
            by_source[src_key] = group
        group["consumers"].add(dst_node_id)
        group["sample_interval_ms"] = min(int(group["sample_interval_ms"]), int(interval_ms))

    if not by_source:
        return graph, warnings

    new_nodes = list(graph.nodes or [])
    new_edges = list(graph.edges or [])

    injected_edges: list[F8Edge] = []

    for src_key, group in by_source.items():
        from_service_id, from_node_id, from_port = src_key
        service = services_by_id.get(from_service_id)
        service_class = str(service.serviceClass) if service is not None else ""
        if service_class != PYENGINE_SERVICE_CLASS:
            consumers_sorted = ", ".join(sorted(str(x) for x in group["consumers"]))
            warnings.append(
                f"auto sampling skipped for {from_service_id}.{from_node_id}.{from_port}: "
                f"source serviceClass={service_class or 'unknown'} (consumers={consumers_sorted})"
            )
            continue

        pull_node_id = _stable_id("auto_pull", from_service_id, from_node_id, from_port)
        sample_interval_ms = int(group["sample_interval_ms"])
        trigger_port = "value"

        if pull_node_id not in nodes_by_id:
            pull_node = F8RuntimeNode(
                nodeId=pull_node_id,
                serviceId=from_service_id,
                serviceClass=PYENGINE_SERVICE_CLASS,
                operatorClass=AUTO_PULL_OPERATOR_CLASS,
                dataInPorts=[
                    F8DataPortSpec(
                        name=trigger_port,
                        description="auto trigger pull input",
                        valueSchema=any_schema(),
                        required=False,
                    ),
                ],
                dataOutPorts=[],
                execInPorts=[],
                execOutPorts=[],
                stateFields=[
                    F8StateSpec(
                        name="autoTriggerEnabled",
                        label="Auto Trigger",
                        description="Periodically pull all data inputs without exec.",
                        valueSchema=boolean_schema(default=False),
                        access=F8StateAccess.rw,
                        showOnNode=False,
                    ),
                    F8StateSpec(
                        name="autoTriggerIntervalMs",
                        label="Auto Trigger Interval (ms)",
                        description="Periodic pull interval in milliseconds.",
                        valueSchema=integer_schema(default=100, minimum=8, maximum=5000),
                        access=F8StateAccess.rw,
                        showOnNode=False,
                    ),
                ],
                stateValues={
                    "autoTriggerEnabled": True,
                    "autoTriggerIntervalMs": sample_interval_ms,
                },
            )
            new_nodes.append(pull_node)
            nodes_by_id[pull_node_id] = pull_node

        source_to_pull_edge_id = _stable_id("edge_auto_pull_src", from_service_id, from_node_id, from_port)
        injected_edges.append(
            F8Edge(
                edgeId=source_to_pull_edge_id,
                fromServiceId=from_service_id,
                fromOperatorId=from_node_id,
                fromPort=from_port,
                toServiceId=from_service_id,
                toOperatorId=pull_node_id,
                toPort=trigger_port,
                kind=F8EdgeKindEnum.data,
                strategy=F8EdgeStrategyEnum.latest,
                timeoutMs=msgspec.UNSET,
                direction=msgspec.UNSET,
            )
        )

    if not injected_edges and not warnings:
        return graph, warnings

    # Deduplicate by edgeId to make reinjection idempotent.
    dedup_edges: dict[str, F8Edge] = {}
    for edge in list(new_edges) + injected_edges:
        dedup_edges[str(edge.edgeId)] = edge

    patched = copy_model(graph, update={"nodes": new_nodes, "edges": list(dedup_edges.values())})
    return patched, warnings


def _is_patch_hub_runtime_node(node: F8RuntimeNode | None) -> bool:
    if node is None:
        return False
    return str(node.operatorClass or "").strip() == PATCH_HUB_OPERATOR_CLASS


def _patch_hub_terminal_key(*, hub_node_id: str, kind: F8EdgeKindEnum, terminal_name: str) -> tuple[str, str, str]:
    return (str(hub_node_id), str(kind.value), str(terminal_name or "").strip())


def _patch_hub_terminal_text(key: tuple[str, str, str]) -> str:
    return f"{key[0]}.{key[1]}.{key[2]}"


def _lower_patch_hubs(graph: F8RuntimeGraph) -> tuple[F8RuntimeGraph, list[str]]:
    warnings: list[str] = []
    nodes_by_id: dict[str, F8RuntimeNode] = {str(node.nodeId): node for node in list(graph.nodes or [])}
    patch_hub_ids = {
        str(node.nodeId)
        for node in list(graph.nodes or [])
        if _is_patch_hub_runtime_node(node)
    }
    if not patch_hub_ids:
        return graph, warnings

    kept_edges: list[F8Edge] = []
    inbound_by_terminal: dict[tuple[str, str, str], list[F8Edge]] = {}
    outbound_by_terminal: dict[tuple[str, str, str], list[F8Edge]] = {}

    for edge in list(graph.edges or []):
        from_node_id = str(edge.fromOperatorId or "").strip()
        to_node_id = str(edge.toOperatorId or "").strip()
        from_is_hub = from_node_id in patch_hub_ids
        to_is_hub = to_node_id in patch_hub_ids

        if not from_is_hub and not to_is_hub:
            kept_edges.append(edge)
            continue

        if edge.kind not in (F8EdgeKindEnum.data, F8EdgeKindEnum.state):
            raise ValueError(
                "patch hub only supports data/state edges: "
                f"{from_node_id or '$service'}.{str(edge.fromPort or '')} -> "
                f"{to_node_id or '$service'}.{str(edge.toPort or '')}"
            )

        if from_is_hub:
            outbound_key = _patch_hub_terminal_key(
                hub_node_id=from_node_id,
                kind=edge.kind,
                terminal_name=str(edge.fromPort or ""),
            )
            outbound_by_terminal.setdefault(outbound_key, []).append(edge)
        if to_is_hub:
            inbound_key = _patch_hub_terminal_key(
                hub_node_id=to_node_id,
                kind=edge.kind,
                terminal_name=str(edge.toPort or ""),
            )
            inbound_by_terminal.setdefault(inbound_key, []).append(edge)

    for terminal_key, inbound_edges in list(inbound_by_terminal.items()):
        if len(inbound_edges) <= 1:
            continue
        upstreams = ", ".join(
            f"{str(edge.fromServiceId)}.{str(edge.fromOperatorId or '$service')}.{str(edge.fromPort or '')}"
            for edge in inbound_edges
        )
        raise ValueError(
            f"patch hub terminal has multiple upstreams: {_patch_hub_terminal_text(terminal_key)} <- {upstreams}"
        )

    def resolve_source_terminal(
        terminal_key: tuple[str, str, str],
        *,
        stack: tuple[tuple[str, str, str], ...],
    ) -> F8Edge | None:
        if terminal_key in stack:
            cycle = " -> ".join(_patch_hub_terminal_text(key) for key in (*stack, terminal_key))
            raise ValueError(f"patch hub cycle detected: {cycle}")

        inbound_edges = inbound_by_terminal.get(terminal_key)
        if not inbound_edges:
            return None

        inbound_edge = inbound_edges[0]
        upstream_node_id = str(inbound_edge.fromOperatorId or "").strip()
        if upstream_node_id in patch_hub_ids:
            upstream_key = _patch_hub_terminal_key(
                hub_node_id=upstream_node_id,
                kind=inbound_edge.kind,
                terminal_name=str(inbound_edge.fromPort or ""),
            )
            return resolve_source_terminal(upstream_key, stack=(*stack, terminal_key))
        return inbound_edge

    lowered_edges: list[F8Edge] = []
    terminal_keys = set(inbound_by_terminal.keys()) | set(outbound_by_terminal.keys())
    for terminal_key in terminal_keys:
        inbound_edges = inbound_by_terminal.get(terminal_key) or []
        outbound_edges = outbound_by_terminal.get(terminal_key) or []
        if inbound_edges and not outbound_edges:
            warnings.append(f"patch hub terminal has no downstream consumers: {_patch_hub_terminal_text(terminal_key)}")
            continue
        if outbound_edges and not inbound_edges:
            warnings.append(f"patch hub terminal has no upstream source: {_patch_hub_terminal_text(terminal_key)}")
            continue
        if not inbound_edges or not outbound_edges:
            continue

        source_edge = resolve_source_terminal(terminal_key, stack=())
        if source_edge is None:
            warnings.append(f"patch hub terminal has no resolvable upstream source: {_patch_hub_terminal_text(terminal_key)}")
            continue

        for outbound_edge in outbound_edges:
            target_node_id = str(outbound_edge.toOperatorId or "").strip()
            if target_node_id in patch_hub_ids:
                continue
            lowered_edges.append(
                F8Edge(
                    edgeId=_stable_id(
                        "patch_hub_edge",
                        str(source_edge.kind.value),
                        str(source_edge.fromServiceId),
                        str(source_edge.fromOperatorId or "$service"),
                        str(source_edge.fromPort or ""),
                        str(outbound_edge.toServiceId),
                        str(outbound_edge.toOperatorId or "$service"),
                        str(outbound_edge.toPort or ""),
                    ),
                    fromServiceId=source_edge.fromServiceId,
                    fromOperatorId=source_edge.fromOperatorId,
                    fromPort=source_edge.fromPort,
                    toServiceId=outbound_edge.toServiceId,
                    toOperatorId=outbound_edge.toOperatorId,
                    toPort=outbound_edge.toPort,
                    kind=outbound_edge.kind,
                    strategy=outbound_edge.strategy,
                    timeoutMs=outbound_edge.timeoutMs,
                    direction=msgspec.UNSET,
                )
            )

    dedup_edges: dict[tuple[str, str, str, str, str, str, str], F8Edge] = {}
    for edge in [*kept_edges, *lowered_edges]:
        dedup_edges[
            (
                str(edge.kind.value),
                str(edge.fromServiceId),
                str(edge.fromOperatorId or ""),
                str(edge.fromPort or ""),
                str(edge.toServiceId),
                str(edge.toOperatorId or ""),
                str(edge.toPort or ""),
            )
        ] = edge

    filtered_nodes = [node for node in list(graph.nodes or []) if str(node.nodeId) not in patch_hub_ids]
    return copy_model(graph, update={"nodes": filtered_nodes, "edges": list(dedup_edges.values())}), warnings


@dataclass(frozen=True)
class CompiledRuntimeGraphs:
    global_graph: F8RuntimeGraph
    per_service: dict[str, F8RuntimeGraph]
    warnings: tuple[str, ...] = ()


def compile_global_runtime_graph(
    *,
    services: Iterable[Any],
    operators: Iterable[Any],
    service_nodes: Iterable[Any] | None = None,
    graph_id: str | None = None,
    revision: str = "1",
    compile_warnings: list[str] | None = None,
) -> F8RuntimeGraph:
    """
    Compile studio nodes into a single global runtime graph.

    - `services` are container nodes (service instances).
    - `operators` are operator nodes (executable nodes bound to a container).
    """
    gid = ensure_token(graph_id or uuid4().hex, label="graph_id")
    rev = ensure_token(str(revision), label="revision")

    # Service instances (containers + standalone single-node services).
    runtime_services: dict[str, F8RuntimeService] = {}

    def add_runtime_service(node: Any) -> None:
        service_id = _runtime_service_id(node)
        try:
            spec = node.spec
        except Exception:
            spec = None
        if not isinstance(spec, F8ServiceSpec):
            return
        meta: dict[str, Any] = {}
        instance_name = _node_name(node).strip()
        if instance_name:
            meta["name"] = instance_name
        runtime_services[service_id] = F8RuntimeService(
            serviceId=service_id,
            serviceClass=str(spec.serviceClass),
            label=str(spec.label or "") or msgspec.UNSET,
            meta=meta,
        )

    for c in services:
        add_runtime_service(c)
    for s in list(service_nodes or []):
        add_runtime_service(s)

    # If the canvas contains studio operators, ensure the studio service instance exists.
    try:
        has_studio_ops = any(
            isinstance(n.spec, F8OperatorSpec) and str(n.spec.serviceClass or "") == STUDIO_SERVICE_CLASS
            for n in operators
        )
    except Exception:
        has_studio_ops = False
    if has_studio_ops and STUDIO_SERVICE_ID not in runtime_services:
        runtime_services[STUDIO_SERVICE_ID] = F8RuntimeService(
            serviceId=STUDIO_SERVICE_ID,
            serviceClass=STUDIO_SERVICE_CLASS,
            label="PyStudio",
            meta={"name": "PyStudio"},
        )

    runtime_nodes: list[F8RuntimeNode] = []
    # Include containers too: containers are service instances and should be present as runtime nodes
    # so they can later own telemetry/state/data ports.
    port_nodes: list[Any] = [n for n in operators] + list(service_nodes or []) + [n for n in services]

    id_map: dict[Any, str] = {}
    svc_map: dict[Any, str] = {}
    kind_map: dict[Any, str] = {}
    for n in port_nodes:
        try:
            spec = n.spec
        except Exception:
            spec = None
        if isinstance(spec, F8OperatorSpec):
            kind_map[n] = "operator"
        elif isinstance(spec, F8ServiceSpec):
            kind_map[n] = "service"
        else:
            continue

        node_id = _runtime_node_id(n)
        service_id = _runtime_service_id(n)
        id_map[n] = node_id
        svc_map[n] = service_id

        state_values: dict[str, Any] = {}
        for f in list(spec.stateFields or []):
            name = str(f.name or "").strip()
            if not name:
                continue
            # Do not include read-only state values in the rungraph snapshot.
            # These are runtime-owned and may be updated internally (eg. telemetry).
            if f.access == F8StateAccess.ro:
                continue
            try:
                if name not in n.model.properties and name not in n.model.custom_properties:
                    continue
                raw_value = n.model.get_property(name)
            except (AttributeError, KeyError, RuntimeError, TypeError):
                continue
            ok, normalized = _coerce_state_payload_value(raw_value)
            if ok:
                state_values[name] = normalized
                continue
            warning = f"skip non-serializable state value: {service_id}.{node_id}.{name}"
            if compile_warnings is not None:
                compile_warnings.append(warning)
            logger.warning("%s", warning)
        # NOTE: values for upstream-driven state fields (bound via state edges)
        # are filtered out after compiling edges, so state propagation always
        # takes precedence over this snapshot on repeated deploys.

        if isinstance(spec, F8ServiceSpec):
            state_fields = service_state_fields_with_builtins(
                [*list(spec.stateFields or []), *hidden_command_state_specs(list(spec.commands or []))]
            )
        else:
            state_fields = operator_state_fields_with_builtins(
                [*list(spec.stateFields or []), *hidden_command_state_specs(list(spec.commands or []))]
            )

        runtime_nodes.append(
            F8RuntimeNode(
                nodeId=node_id,
                serviceId=service_id,
                serviceClass=str(spec.serviceClass),
                operatorClass=(str(spec.operatorClass) if isinstance(spec, F8OperatorSpec) else None),
                execInPorts=([str(p) for p in list(spec.execInPorts or [])] if isinstance(spec, F8OperatorSpec) else []),
                execOutPorts=([str(p) for p in list(spec.execOutPorts or [])] if isinstance(spec, F8OperatorSpec) else []),
                dataInPorts=list(spec.dataInPorts or []),
                dataOutPorts=list(spec.dataOutPorts or []),
                stateFields=state_fields,
                stateValues=state_values or msgspec.UNSET,
            )
        )

    edges: list[F8Edge] = []
    for src_node in port_nodes:
        if src_node not in id_map:
            continue
        for out_port in list(src_node.output_ports() or []):
            out_name = _port_name(out_port)
            edge_kind = _port_kind(out_name)
            if edge_kind is None:
                continue
            for in_port in list(out_port.connected_ports() or []):
                dst_node = in_port.node()
                if dst_node not in id_map:
                    continue
                in_name = _port_name(in_port)

                edges.append(
                    F8Edge(
                        edgeId=uuid4().hex,
                        fromServiceId=svc_map[src_node],
                        fromOperatorId=(
                            id_map[src_node] if kind_map.get(src_node) != "container" else msgspec.UNSET
                        ),
                        fromPort=_raw_port_name(out_name),
                        toServiceId=svc_map[dst_node],
                        toOperatorId=(id_map[dst_node] if kind_map.get(dst_node) != "container" else msgspec.UNSET),
                        toPort=_raw_port_name(in_name),
                        kind=edge_kind,
                        strategy=F8EdgeStrategyEnum.latest,
                        timeoutMs=msgspec.UNSET,
                        direction=msgspec.UNSET,
                    )
                )

    # If a state field is upstream-driven (connected via state edge), do not
    # include its current state value in the rungraph snapshot. This avoids
    # "deploy races" where the snapshot temporarily overrides the edge-driven
    # value when redeploying a running graph.
    upstream_state_by_node: dict[str, set[str]] = {}
    for e in edges:
        if e.kind != F8EdgeKindEnum.state:
            continue
        if e.toOperatorId is None:
            continue
        node_id = str(e.toOperatorId)
        field = str(e.toPort or "").strip()
        if not field:
            continue
        upstream_state_by_node.setdefault(node_id, set()).add(field)

    if upstream_state_by_node:
        filtered_nodes: list[F8RuntimeNode] = []
        for rn in runtime_nodes:
            bound = upstream_state_by_node.get(str(rn.nodeId))
            if not bound or not rn.stateValues:
                filtered_nodes.append(rn)
                continue
            new_values = {k: v for k, v in dict(rn.stateValues).items() if str(k) not in bound}
            if new_values == rn.stateValues:
                filtered_nodes.append(rn)
                continue
            filtered_nodes.append(copy_model(rn, update={"stateValues": new_values or msgspec.UNSET}))
        runtime_nodes = filtered_nodes

    graph = F8RuntimeGraph(
        graphId=gid,
        revision=rev,
        services=list(runtime_services.values()),
        nodes=runtime_nodes,
        edges=edges,
    )
    graph, patch_hub_warnings = _lower_patch_hubs(graph)
    if patch_hub_warnings:
        if compile_warnings is not None:
            compile_warnings.extend(patch_hub_warnings)
        for warning in patch_hub_warnings:
            logger.warning("%s", warning)
    graph, warnings = _inject_studio_auto_pull_triggers(graph)
    if warnings:
        if compile_warnings is not None:
            compile_warnings.extend(warnings)
        for warning in warnings:
            logger.warning("%s", warning)
    # Studio-level validation: reject invalid wiring early.
    validate_exec_edges_or_raise(graph)
    validate_data_edges_or_raise(graph)
    validate_state_edges_or_raise(graph, forbid_cycles=True, forbid_multi_upstream=True)
    validate_state_edge_targets_writable_or_raise(graph)
    return graph


def split_runtime_graph_by_service(graph: F8RuntimeGraph) -> dict[str, F8RuntimeGraph]:
    """
    Produce per-service runtime graphs.

    Cross edges are included, but since the peer service's nodes are absent in
    the per-service node list, they naturally act as "half edges".
    """

    def _with_direction(edge: F8Edge, direction: F8EdgeDirection) -> F8Edge:
        # Avoid mutating shared edge instances across per-service graphs.
        return copy_model(edge, update={"direction": direction})

    by_service_nodes: dict[str, list[F8RuntimeNode]] = {}
    for n in graph.nodes:
        by_service_nodes.setdefault(str(n.serviceId), []).append(n)

    by_service_edges: dict[str, list[F8Edge]] = {}
    for e in graph.edges:
        from_sid = str(e.fromServiceId)
        to_sid = str(e.toServiceId)
        if to_sid == from_sid:
            by_service_edges.setdefault(from_sid, []).append(e)
            continue

        # Cross-service edges become half-edges in per-service graphs.
        by_service_edges.setdefault(from_sid, []).append(_with_direction(e, F8EdgeDirection.out))
        by_service_edges.setdefault(to_sid, []).append(_with_direction(e, F8EdgeDirection.in_))

    out: dict[str, F8RuntimeGraph] = {}
    for svc in graph.services:
        sid = str(svc.serviceId)
        out[sid] = F8RuntimeGraph(
            graphId=graph.graphId,
            revision=graph.revision,
            services=[svc],
            nodes=by_service_nodes.get(sid, []),
            edges=by_service_edges.get(sid, []),
        )
    return out


def compile_runtime_graphs_from_studio(studio_graph: Any) -> CompiledRuntimeGraphs:
    """
    Convenience wrapper that extracts container/operator nodes from an
    `F8StudioGraph`.
    """
    def _is_disabled(n: Any) -> bool:
        try:
            return bool(n.view.disabled)
        except Exception:
            return False

    all_graph_nodes = list(studio_graph.all_nodes() or [])
    missing_locked_nodes: list[tuple[str, str]] = []
    for node in all_graph_nodes:
        node_id = ""
        missing_type = ""
        try:
            node_id = str(node.id or "").strip()
        except Exception:
            node_id = ""
        try:
            model = node.model
            f8_sys = model.f8_sys if isinstance(model.f8_sys, dict) else {}
            if bool(f8_sys.get("missingLocked")):
                missing_type = str(f8_sys.get("missingType") or "").strip()
                missing_locked_nodes.append((node_id, missing_type))
        except Exception:
            continue
    if missing_locked_nodes:
        formatted = ", ".join(
            f"{node_id or '<unknown>'}({missing_type or 'unknown'})" for node_id, missing_type in missing_locked_nodes
        )
        raise ValueError(f"compile blocked: missing dependency node(s): {formatted}")

    all_nodes = [n for n in all_graph_nodes if not _is_disabled(n)]
    try:
        is_container_node = studio_graph._is_container_node
        is_operator_node = studio_graph._is_operator_node
    except Exception as exc:
        raise TypeError("studio_graph must be an F8StudioGraph (missing type predicates).") from exc
    try:
        repaired = int(studio_graph.repair_stale_port_connection_refs())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        repaired = 0
    if repaired:
        logger.warning("compile_runtime_graphs_from_studio: repaired %s stale port ref(s) before compile.", repaired)

    services = [n for n in all_nodes if is_container_node(n)]
    operators = [n for n in all_nodes if is_operator_node(n)]

    # Standalone single-node services (non-container F8ServiceSpec nodes).
    service_nodes: list[Any] = []
    for n in all_nodes:
        try:
            spec = n.spec
        except (AttributeError, RuntimeError, TypeError):
            continue
        if not isinstance(spec, F8ServiceSpec):
            continue
        if is_container_node(n):
            continue
        service_nodes.append(n)

    compile_warnings: list[str] = []
    global_graph = compile_global_runtime_graph(
        services=services,
        operators=operators,
        service_nodes=service_nodes,
        compile_warnings=compile_warnings,
    )
    return CompiledRuntimeGraphs(
        global_graph=global_graph,
        per_service=split_runtime_graph_by_service(global_graph),
        warnings=tuple(compile_warnings),
    )
