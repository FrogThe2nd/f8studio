from __future__ import annotations

import json
from typing import Any

from f8pysdk import F8OperatorSpec, F8ServiceSpec
from f8pysdk.msgspec_codec import validate_as

from ..session_migration import extract_layout, wrap_layout_for_save
from .graph_doc import (
    GraphDoc,
    GraphDocParseResult,
    GraphEdge,
    GraphEdgeEndpoint,
    GraphNode,
    GraphNodeUi,
)
from .json_sanitize import sanitize_legacy_nulls


F8STUDIO_SESSION_SCHEMA_VERSION: str = "f8studio-session/1"


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    return None


def _port_kind(name: str) -> str | None:
    n = str(name or "")
    if n.startswith("[E]") or n.endswith("[E]"):
        return "exec"
    if n.startswith("[D]") or n.endswith("[D]"):
        return "data"
    if n.startswith("[S]") or n.endswith("[S]"):
        return "state"
    return None


def _raw_port_name(name: str) -> str:
    n = str(name or "")
    for prefix in ("[E]", "[D]", "[S]"):
        if n.startswith(prefix):
            n = n[len(prefix) :]
    for suffix in ("[E]", "[D]", "[S]"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip()


def _encode_output_port(kind: str, raw: str) -> str:
    raw_name = str(raw or "").strip()
    if kind == "exec":
        return f"{raw_name}[E]"
    if kind == "data":
        return f"{raw_name}[D]"
    if kind == "state":
        return f"{raw_name}[S]"
    return raw_name


def _encode_input_port(kind: str, raw: str) -> str:
    raw_name = str(raw or "").strip()
    if kind == "exec":
        return f"[E]{raw_name}"
    if kind == "data":
        return f"[D]{raw_name}"
    if kind == "state":
        return f"[S]{raw_name}"
    return raw_name


def _state_field_names(spec: dict[str, Any]) -> set[str]:
    cleaned = sanitize_legacy_nulls(spec)
    if not isinstance(cleaned, dict):
        cleaned = dict(spec)
    try:
        if "operatorClass" in cleaned:
            typed = validate_as(F8OperatorSpec, cleaned)
        else:
            typed = validate_as(F8ServiceSpec, cleaned)
    except Exception:
        return set()
    out: set[str] = set()
    for f in list(typed.stateFields or []):
        name = str(f.name or "").strip()
        if name:
            out.add(name)
    return out


def import_nodegraphqt_session(payload: Any) -> GraphDocParseResult:
    """
    Import `f8studio-session/1` (NodeGraphQt session envelope) into `f8studio-graph/1`.
    """
    if not isinstance(payload, dict):
        raise ValueError("session payload must be a JSON object")
    schema_version = str(payload.get("schemaVersion") or "").strip()
    if schema_version != F8STUDIO_SESSION_SCHEMA_VERSION:
        raise ValueError(f"unsupported session schemaVersion: {schema_version!r}")

    layout = extract_layout(payload)
    warnings: list[str] = []

    nodes_raw = _as_dict(layout.get("nodes")) or {}
    conns_raw = _as_list(layout.get("connections")) or []

    # Preserve top-level NodeGraphQt graph settings for roundtrip.
    compat_top: dict[str, Any] = {
        "nodegraphqt": {
            "graph": _as_dict(layout.get("graph")) or {},
        }
    }

    nodes_out: list[GraphNode] = []
    node_ids_seen: set[str] = set()
    for node_id, node_data_any in nodes_raw.items():
        node_id_s = str(node_id)
        if node_id_s in node_ids_seen:
            continue
        node_ids_seen.add(node_id_s)

        node_data = _as_dict(node_data_any) or {}
        node_type = str(node_data.get("type_") or "").strip()
        if not node_type:
            warnings.append(f"skip node with empty type_: nodeId={node_id_s}")
            continue

        spec = _as_dict(node_data.get("f8_spec")) or {}
        ui_overrides = _as_dict(node_data.get("f8_ui")) or {}
        sys = _as_dict(node_data.get("f8_sys")) or {}
        custom_raw = _as_dict(node_data.get("custom")) or {}

        pos = node_data.get("pos")
        ui_pos = (0.0, 0.0)
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                ui_pos = (float(pos[0]), float(pos[1]))
            except (TypeError, ValueError):
                ui_pos = (0.0, 0.0)

        ui_size: tuple[float, float] | None = None
        try:
            w = node_data.get("width")
            h = node_data.get("height")
            if w is not None and h is not None:
                ui_size = (float(w), float(h))
        except (TypeError, ValueError):
            ui_size = None

        state_field_names = _state_field_names(spec)
        state: dict[str, Any] = {}
        custom: dict[str, Any] = {}
        for k, v in custom_raw.items():
            key = str(k)
            if key in state_field_names:
                state[key] = v
            else:
                custom[key] = v

        # Preserve any NodeGraphQt/UI-only keys not represented by the canonical doc.
        preserved: dict[str, Any] = {}
        for k, v in node_data.items():
            if k in {
                "type_",
                "pos",
                "width",
                "height",
                "custom",
                "f8_spec",
                "f8_ui",
                "f8_sys",
                # Session load strips these to avoid port rebuild.
                "input_ports",
                "output_ports",
                "port_deletion_allowed",
            }:
                continue
            preserved[str(k)] = v

        nodes_out.append(
            GraphNode(
                id=node_id_s,
                nodeType=node_type,
                spec=spec,
                state=state,
                custom=custom,
                sys={str(k): v for k, v in sys.items()},
                uiOverrides={str(k): v for k, v in ui_overrides.items()},
                ui=GraphNodeUi(pos=ui_pos, size=ui_size, collapsed=False),
                compat={"nodegraphqt": preserved},
            )
        )

    edges_out: list[GraphEdge] = []
    edge_counts: dict[str, int] = {}
    for item in conns_raw:
        conn = _as_dict(item)
        if conn is None:
            continue
        out_raw = conn.get("out")
        in_raw = conn.get("in")
        if (
            not isinstance(out_raw, list)
            or len(out_raw) != 2
            or not isinstance(in_raw, list)
            or len(in_raw) != 2
        ):
            continue
        from_node = str(out_raw[0])
        from_port_raw = str(out_raw[1])
        to_node = str(in_raw[0])
        to_port_raw = str(in_raw[1])
        if from_node not in nodes_raw or to_node not in nodes_raw:
            warnings.append(
                f"drop connection with missing node(s): out={from_node!r} in={to_node!r}"
            )
            continue
        out_kind = _port_kind(from_port_raw)
        in_kind = _port_kind(to_port_raw)
        if out_kind is None or in_kind is None:
            warnings.append(
                f"drop connection with unknown port kind: out={from_port_raw!r} in={to_port_raw!r}"
            )
            continue
        if out_kind != in_kind:
            warnings.append(
                f"drop mixed-kind connection: out={from_port_raw!r} in={to_port_raw!r}"
            )
            continue
        from_port = _raw_port_name(from_port_raw)
        to_port = _raw_port_name(to_port_raw)
        if not from_port or not to_port:
            continue
        base_id = f"{out_kind}:{from_node}:{from_port}->{to_node}:{to_port}"
        edge_counts[base_id] = int(edge_counts.get(base_id, 0)) + 1
        idx = int(edge_counts[base_id])
        edge_id = base_id if idx == 1 else f"{base_id}#{idx}"
        edges_out.append(
            GraphEdge(
                id=edge_id,
                kind=out_kind,  # type: ignore[arg-type]
                from_=GraphEdgeEndpoint(nodeId=from_node, port=from_port),
                to=GraphEdgeEndpoint(nodeId=to_node, port=to_port),
            )
        )

    graph_id = "studio"
    revision = "1"
    try:
        graph_id = str(layout.get("graphId") or "studio")
    except Exception:
        graph_id = "studio"
    try:
        revision = str(layout.get("revision") or "1")
    except Exception:
        revision = "1"

    return GraphDocParseResult(
        doc=GraphDoc(
            graphId=graph_id,
            revision=revision,
            nodes=nodes_out,
            edges=edges_out,
            compat=compat_top,
        ),
        warnings=tuple(warnings),
    )


def export_nodegraphqt_session(doc: GraphDoc) -> dict[str, Any]:
    """
    Export `f8studio-graph/1` to `f8studio-session/1` envelope.
    """
    node_map: dict[str, GraphNode] = {str(n.id): n for n in list(doc.nodes or [])}
    nodes_out: dict[str, dict[str, Any]] = {}

    top_ng = doc.compat.get("nodegraphqt") if isinstance(doc.compat, dict) else None
    top_ng_obj = top_ng if isinstance(top_ng, dict) else {}
    graph_settings = top_ng_obj.get("graph") if isinstance(top_ng_obj.get("graph"), dict) else {}

    for node_id, n in node_map.items():
        # Rehydrate preserved NodeGraphQt keys.
        preserved = n.compat.get("nodegraphqt") if isinstance(n.compat, dict) else None
        preserved_obj: dict[str, Any] = dict(preserved) if isinstance(preserved, dict) else {}

        # Canonical -> NodeGraphQt session keys.
        preserved_obj["type_"] = str(n.nodeType)
        preserved_obj["pos"] = [float(n.ui.pos[0]), float(n.ui.pos[1])]
        if n.ui.size is not None:
            preserved_obj["width"] = float(n.ui.size[0])
            preserved_obj["height"] = float(n.ui.size[1])

        preserved_obj["f8_spec"] = dict(n.spec)
        preserved_obj["f8_ui"] = dict(n.uiOverrides or {})
        preserved_obj["f8_sys"] = dict(n.sys or {})

        # NodeGraphQt uses `custom` as the persisted custom-properties bag.
        merged_custom: dict[str, Any] = {}
        merged_custom.update({str(k): v for k, v in dict(n.custom or {}).items()})
        merged_custom.update({str(k): v for k, v in dict(n.state or {}).items()})
        preserved_obj["custom"] = merged_custom

        # Provide a reasonable default name if none is preserved.
        if "name" not in preserved_obj:
            label = ""
            try:
                label = str(n.spec.get("label") or "").strip()
            except Exception:
                label = ""
            preserved_obj["name"] = label or str(n.nodeType).split(".")[-1]

        nodes_out[str(node_id)] = preserved_obj

    connections_out: list[dict[str, Any]] = []
    for e in list(doc.edges or []):
        kind = str(e.kind)
        from_node = str(e.from_.nodeId)
        to_node = str(e.to.nodeId)
        if from_node not in nodes_out or to_node not in nodes_out:
            continue
        connections_out.append(
            {
                "out": [from_node, _encode_output_port(kind, str(e.from_.port))],
                "in": [to_node, _encode_input_port(kind, str(e.to.port))],
            }
        )

    layout: dict[str, Any] = {
        "graph": dict(graph_settings),
        "nodes": nodes_out,
        "connections": connections_out,
    }
    return wrap_layout_for_save(layout)


def export_nodegraphqt_session_text(doc: GraphDoc, *, indent: int = 2) -> str:
    return json.dumps(export_nodegraphqt_session(doc), ensure_ascii=False, indent=int(indent))
