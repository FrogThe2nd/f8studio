from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import msgspec

from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8RuntimeGraph
from f8pysdk.msgspec_codec import dump_json, validate_as
from f8pysdk.nats_naming import ensure_token

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs, compile_global_runtime_graph, split_runtime_graph_by_service
from ..pystudio_node_registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from ..constants import STUDIO_SERVICE_ID
from .graph_doc import GraphDoc, GraphEdge, GraphNode
from .json_sanitize import sanitize_legacy_nulls


@dataclass(frozen=True)
class _ModelAdapter:
    name: str
    properties: dict[str, Any]
    custom_properties: dict[str, Any]

    def get_property(self, name: str) -> Any:
        key = str(name or "")
        if key in self.properties:
            return self.properties[key]
        if key in self.custom_properties:
            return self.custom_properties[key]
        raise KeyError(key)


class _PortAdapter:
    def __init__(self, *, node: "_NodeAdapter", port_name: str) -> None:
        self._node = node
        self._port_name = str(port_name or "")
        self._connected: list["_PortAdapter"] = []

    def name(self) -> str:
        return self._port_name

    def node(self) -> "_NodeAdapter":
        return self._node

    def connected_ports(self) -> list["_PortAdapter"]:
        return list(self._connected)

    def _connect_to(self, in_port: "_PortAdapter") -> None:
        self._connected.append(in_port)


class _NodeAdapter:
    """
    Minimal adapter surface required by `compile_global_runtime_graph`.
    """

    def __init__(
        self,
        *,
        node_id: str,
        spec: F8OperatorSpec | F8ServiceSpec,
        svc_id: str,
        model: _ModelAdapter,
    ) -> None:
        self.id = ensure_token(str(node_id), label="node_id")
        self.spec = spec
        self.svcId = str(svc_id)
        self.model = model
        self._out_ports: dict[str, _PortAdapter] = {}
        self._in_ports: dict[str, _PortAdapter] = {}

    def name(self) -> str:
        return str(self.model.name or "")

    def output_ports(self) -> list[_PortAdapter]:
        return list(self._out_ports.values())

    def _get_or_create_out(self, port_name: str) -> _PortAdapter:
        key = str(port_name)
        existing = self._out_ports.get(key)
        if existing is not None:
            return existing
        p = _PortAdapter(node=self, port_name=key)
        self._out_ports[key] = p
        return p

    def _get_or_create_in(self, port_name: str) -> _PortAdapter:
        key = str(port_name)
        existing = self._in_ports.get(key)
        if existing is not None:
            return existing
        p = _PortAdapter(node=self, port_name=key)
        self._in_ports[key] = p
        return p


def _encode_out_port(kind: str, raw: str) -> str:
    base = str(raw or "").strip()
    if kind == "exec":
        return f"{base}[E]"
    if kind == "data":
        return f"{base}[D]"
    if kind == "state":
        return f"{base}[S]"
    return base


def _encode_in_port(kind: str, raw: str) -> str:
    base = str(raw or "").strip()
    if kind == "exec":
        return f"[E]{base}"
    if kind == "data":
        return f"[D]{base}"
    if kind == "state":
        return f"[S]{base}"
    return base


def _coerce_spec(spec_json: dict[str, Any]) -> F8OperatorSpec | F8ServiceSpec:
    # Legacy sessions often contain optional schema metadata serialized as null
    # (eg. valueSchema.title=null), which msgspec expects to be omitted.
    cleaned = sanitize_legacy_nulls(spec_json)
    if not isinstance(cleaned, dict):
        cleaned = dict(spec_json)
    if "operatorClass" in cleaned:
        return validate_as(F8OperatorSpec, cleaned)
    return validate_as(F8ServiceSpec, cleaned)


def _node_display_name(node: GraphNode) -> str:
    ng = node.compat.get("nodegraphqt") if isinstance(node.compat, dict) else None
    if isinstance(ng, dict):
        raw = ng.get("name")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    try:
        label = str(node.spec.get("label") or "").strip()
    except Exception:
        label = ""
    return label or str(node.nodeType).split(".")[-1]


def compile_runtime_graphs_from_doc(doc: GraphDoc) -> CompiledRuntimeGraphs:
    """
    Compile `f8studio-graph/1` into runtime graphs without NodeGraphQt.

    The implementation intentionally routes through the existing studio compiler
    (`compile_global_runtime_graph`) by providing tiny adapter objects.
    """
    node_by_id: dict[str, _NodeAdapter] = {}
    services: list[_NodeAdapter] = []
    operators: list[_NodeAdapter] = []

    for n in list(doc.nodes or []):
        node_id = str(n.id or "").strip()
        if not node_id:
            continue
        spec_obj = _coerce_spec(dict(n.spec))

        state = dict(n.state or {})
        custom = dict(n.custom or {})

        # Service binding for operators.
        if isinstance(spec_obj, F8OperatorSpec) and str(spec_obj.serviceClass or "") != STUDIO_SERVICE_CLASS:
            svc_id = ""
            if "svcId" in state:
                svc_id = str(state.get("svcId") or "").strip()
            elif "svcId" in custom:
                svc_id = str(custom.get("svcId") or "").strip()
            if not svc_id:
                raise ValueError(f"operator missing svcId binding: nodeId={node_id} operatorClass={spec_obj.operatorClass}")
        elif isinstance(spec_obj, F8OperatorSpec):
            svc_id = STUDIO_SERVICE_ID
        else:
            svc_id = node_id

        adapter = _NodeAdapter(
            node_id=node_id,
            spec=spec_obj,
            svc_id=svc_id,
            model=_ModelAdapter(
                name=_node_display_name(n),
                properties={str(k): v for k, v in state.items()},
                custom_properties={str(k): v for k, v in custom.items()},
            ),
        )
        node_by_id[node_id] = adapter
        if isinstance(spec_obj, F8ServiceSpec):
            services.append(adapter)
        else:
            operators.append(adapter)

    # Bind doc edges into port connections that the existing compiler understands.
    for e in list(doc.edges or []):
        _bind_edge(node_by_id=node_by_id, edge=e)

    compile_warnings: list[str] = []
    global_graph = compile_global_runtime_graph(
        services=services,
        operators=operators,
        service_nodes=[],
        graph_id=str(doc.graphId or "studio"),
        revision=str(doc.revision or "1"),
        compile_warnings=compile_warnings,
    )
    return CompiledRuntimeGraphs(
        global_graph=global_graph,
        per_service=split_runtime_graph_by_service(global_graph),
        warnings=tuple(compile_warnings),
    )


def _bind_edge(*, node_by_id: dict[str, _NodeAdapter], edge: GraphEdge) -> None:
    kind = str(edge.kind or "")
    src_id = str(edge.from_.nodeId or "")
    dst_id = str(edge.to.nodeId or "")
    if not src_id or not dst_id:
        return
    src = node_by_id.get(src_id)
    dst = node_by_id.get(dst_id)
    if src is None or dst is None:
        return
    out_port_name = _encode_out_port(kind, str(edge.from_.port or ""))
    in_port_name = _encode_in_port(kind, str(edge.to.port or ""))
    if not out_port_name.strip() or not in_port_name.strip():
        return
    out_port = src._get_or_create_out(out_port_name)
    in_port = dst._get_or_create_in(in_port_name)
    out_port._connect_to(in_port)


def compiled_runtime_graphs_to_json(compiled: CompiledRuntimeGraphs) -> dict[str, Any]:
    """
    JSON-friendly dump for API responses.
    """
    global_graph = dump_json(compiled.global_graph, mode="json", by_alias=True)
    per_service: dict[str, Any] = {}
    for service_id, sub in dict(compiled.per_service or {}).items():
        per_service[str(service_id)] = dump_json(sub, mode="json", by_alias=True)
    return {
        "global_graph": global_graph,
        "per_service": per_service,
        "warnings": list(compiled.warnings or ()),
    }


def runtime_graph_to_json(graph: F8RuntimeGraph) -> dict[str, Any]:
    return dump_json(graph, mode="json", by_alias=True)
