from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, TypeAlias

import msgspec


F8STUDIO_GRAPH_SCHEMA_VERSION: str = "f8studio-graph/1"

EdgeKind: TypeAlias = Literal["exec", "data", "state"]

JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)


class GraphNodeUi(msgspec.Struct, kw_only=True):
    pos: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] | None = None
    collapsed: bool = False


class GraphEdgeEndpoint(msgspec.Struct, kw_only=True):
    nodeId: str
    port: str


class GraphEdge(msgspec.Struct, kw_only=True):
    id: str
    kind: EdgeKind
    from_: GraphEdgeEndpoint = msgspec.field(name="from")
    to: GraphEdgeEndpoint


class GraphNode(msgspec.Struct, kw_only=True):
    id: str
    nodeType: str
    spec: dict[str, Any]
    state: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    ui: GraphNodeUi = msgspec.field(default_factory=GraphNodeUi)
    sys: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    uiOverrides: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    custom: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    compat: dict[str, JsonValue] = msgspec.field(default_factory=dict)


class GraphDoc(msgspec.Struct, kw_only=True):
    schemaVersion: str = F8STUDIO_GRAPH_SCHEMA_VERSION
    graphId: str
    revision: str
    nodes: list[GraphNode] = msgspec.field(default_factory=list)
    edges: list[GraphEdge] = msgspec.field(default_factory=list)
    settings: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    compat: dict[str, JsonValue] = msgspec.field(default_factory=dict)


@dataclass(frozen=True)
class GraphDocParseResult:
    doc: GraphDoc
    warnings: tuple[str, ...] = ()


def _as_float_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _ensure_json_obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    return value


def load_graph_doc(payload: Any) -> GraphDocParseResult:
    """
    Best-effort parse for `f8studio-graph/1`.

    This keeps schema enforcement explicit (field names are fixed and grep-able)
    while still allowing forward-compatible `compat` blobs.
    """
    obj = _ensure_json_obj(payload)
    schema_version = str(obj.get("schemaVersion") or "").strip()
    if schema_version != F8STUDIO_GRAPH_SCHEMA_VERSION:
        raise ValueError(f"unsupported graph schemaVersion: {schema_version!r}")

    warnings: list[str] = []
    nodes_out: list[GraphNode] = []
    for raw_node in list(obj.get("nodes") or []):
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id") or "").strip()
        node_type = str(raw_node.get("nodeType") or "").strip()
        spec = raw_node.get("spec")
        if not node_id or not node_type or not isinstance(spec, dict):
            continue
        state = raw_node.get("state")
        if not isinstance(state, dict):
            state = {}
        custom = raw_node.get("custom")
        if not isinstance(custom, dict):
            custom = {}
        sys = raw_node.get("sys")
        if not isinstance(sys, dict):
            sys = {}
        ui_overrides = raw_node.get("uiOverrides")
        if not isinstance(ui_overrides, dict):
            ui_overrides = {}
        compat = raw_node.get("compat")
        if not isinstance(compat, dict):
            compat = {}

        ui_raw = raw_node.get("ui")
        ui_pos = (0.0, 0.0)
        ui_size: tuple[float, float] | None = None
        ui_collapsed = False
        if isinstance(ui_raw, dict):
            pos = _as_float_pair(ui_raw.get("pos"))
            if pos is not None:
                ui_pos = pos
            size = _as_float_pair(ui_raw.get("size"))
            if size is not None:
                ui_size = size
            ui_collapsed = bool(ui_raw.get("collapsed"))

        nodes_out.append(
            GraphNode(
                id=node_id,
                nodeType=node_type,
                spec=spec,
                state={str(k): v for k, v in state.items()},
                custom={str(k): v for k, v in custom.items()},
                sys={str(k): v for k, v in sys.items()},
                uiOverrides={str(k): v for k, v in ui_overrides.items()},
                ui=GraphNodeUi(pos=ui_pos, size=ui_size, collapsed=ui_collapsed),
                compat={str(k): v for k, v in compat.items()},
            )
        )

    edges_out: list[GraphEdge] = []
    for raw_edge in list(obj.get("edges") or []):
        if not isinstance(raw_edge, dict):
            continue
        edge_id = str(raw_edge.get("id") or "").strip()
        kind = str(raw_edge.get("kind") or "").strip().lower()
        if kind not in ("exec", "data", "state"):
            continue
        from_raw = raw_edge.get("from")
        to_raw = raw_edge.get("to")
        if not isinstance(from_raw, dict) or not isinstance(to_raw, dict):
            continue
        from_node = str(from_raw.get("nodeId") or "").strip()
        from_port = str(from_raw.get("port") or "").strip()
        to_node = str(to_raw.get("nodeId") or "").strip()
        to_port = str(to_raw.get("port") or "").strip()
        if not edge_id or not from_node or not to_node or not from_port or not to_port:
            continue
        edges_out.append(
            GraphEdge(
                id=edge_id,
                kind=kind,  # type: ignore[arg-type]
                from_=GraphEdgeEndpoint(nodeId=from_node, port=from_port),
                to=GraphEdgeEndpoint(nodeId=to_node, port=to_port),
            )
        )

    graph_id = str(obj.get("graphId") or "").strip() or "studio"
    revision = str(obj.get("revision") or "").strip() or "1"
    settings = obj.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    compat = obj.get("compat")
    if not isinstance(compat, dict):
        compat = {}

    return GraphDocParseResult(
        doc=GraphDoc(
            graphId=graph_id,
            revision=revision,
            nodes=nodes_out,
            edges=edges_out,
            settings={str(k): v for k, v in settings.items()},
            compat={str(k): v for k, v in compat.items()},
        ),
        warnings=tuple(warnings),
    )


def dump_graph_doc(doc: GraphDoc) -> dict[str, Any]:
    payload = msgspec.to_builtins(doc)
    if not isinstance(payload, dict):
        raise TypeError("GraphDoc must serialize to a JSON object")
    return payload


def dump_graph_doc_text(doc: GraphDoc, *, indent: int = 2) -> str:
    return json.dumps(dump_graph_doc(doc), ensure_ascii=False, indent=int(indent))


def normalize_graph_doc(doc: GraphDoc) -> GraphDocParseResult:
    """
    Normalize a `f8studio-graph/1` document.

    Guarantees:
    - node ids unique (keeps first occurrence)
    - edges only reference existing nodes
    - edge ids unique (auto-suffix `#N` when needed)
    """
    warnings: list[str] = []

    nodes_out: list[GraphNode] = []
    node_ids: set[str] = set()
    for n in list(doc.nodes or []):
        node_id = str(n.id or "").strip()
        if not node_id:
            warnings.append("drop node with empty id")
            continue
        if node_id in node_ids:
            warnings.append(f"drop duplicate node id: {node_id}")
            continue
        node_ids.add(node_id)
        nodes_out.append(n)

    edges_out: list[GraphEdge] = []
    edge_ids_seen: set[str] = set()
    edge_counts: dict[str, int] = {}
    for e in list(doc.edges or []):
        src_id = str(e.from_.nodeId or "").strip()
        dst_id = str(e.to.nodeId or "").strip()
        if not src_id or not dst_id:
            warnings.append("drop edge with empty endpoint nodeId")
            continue
        if src_id not in node_ids or dst_id not in node_ids:
            warnings.append(f"drop edge referencing missing node: from={src_id} to={dst_id}")
            continue

        kind = str(e.kind or "").strip().lower()
        if kind not in ("exec", "data", "state"):
            warnings.append(f"drop edge with invalid kind: {kind!r}")
            continue
        from_port = str(e.from_.port or "").strip()
        to_port = str(e.to.port or "").strip()
        if not from_port or not to_port:
            warnings.append("drop edge with empty port name")
            continue

        base_id = str(e.id or "").strip()
        if not base_id:
            base_id = f"{kind}:{src_id}:{from_port}->{dst_id}:{to_port}"
        edge_counts[base_id] = int(edge_counts.get(base_id, 0)) + 1
        idx = int(edge_counts[base_id])
        edge_id = base_id if idx == 1 else f"{base_id}#{idx}"
        if edge_id in edge_ids_seen:
            # Extremely unlikely unless input already had the same suffixes.
            bump = 2
            while f"{edge_id}#{bump}" in edge_ids_seen:
                bump += 1
            edge_id = f"{edge_id}#{bump}"
        edge_ids_seen.add(edge_id)

        edges_out.append(
            GraphEdge(
                id=edge_id,
                kind=kind,  # type: ignore[arg-type]
                from_=GraphEdgeEndpoint(nodeId=src_id, port=from_port),
                to=GraphEdgeEndpoint(nodeId=dst_id, port=to_port),
            )
        )

    out = GraphDoc(
        schemaVersion=F8STUDIO_GRAPH_SCHEMA_VERSION,
        graphId=str(doc.graphId or "studio"),
        revision=str(doc.revision or "1"),
        nodes=nodes_out,
        edges=edges_out,
        settings=dict(doc.settings or {}),
        compat=dict(doc.compat or {}),
    )
    return GraphDocParseResult(doc=out, warnings=tuple(warnings))
