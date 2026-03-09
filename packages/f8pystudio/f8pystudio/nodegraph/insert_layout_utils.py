from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return float(self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return float(self.max_y - self.min_y)

    def shifted(self, *, dx: float, dy: float) -> "GraphBounds":
        return GraphBounds(
            min_x=float(self.min_x + dx),
            min_y=float(self.min_y + dy),
            max_x=float(self.max_x + dx),
            max_y=float(self.max_y + dy),
        )


@dataclass(frozen=True)
class IdRemapPlan:
    mapping: dict[str, str] = field(default_factory=dict)


def coerce_layout_pos(node_data: dict[str, Any]) -> tuple[float, float] | None:
    pos_obj = node_data.get("pos")
    if not (isinstance(pos_obj, (list, tuple)) and len(pos_obj) >= 2):
        return None
    try:
        return float(pos_obj[0]), float(pos_obj[1])
    except (TypeError, ValueError):
        return None


def compute_layout_bbox(layout_data: dict[str, Any]) -> GraphBounds:
    nodes = layout_data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return GraphBounds(0.0, 0.0, 0.0, 0.0)
    min_x: float | None = None
    min_y: float | None = None
    max_x: float | None = None
    max_y: float | None = None
    for node_data in list(nodes.values()):
        if not isinstance(node_data, dict):
            continue
        pos = coerce_layout_pos(node_data)
        if pos is None:
            continue
        px, py = pos
        min_x = px if min_x is None else min(min_x, px)
        min_y = py if min_y is None else min(min_y, py)
        max_x = px if max_x is None else max(max_x, px)
        max_y = py if max_y is None else max(max_y, py)
    if min_x is None or min_y is None or max_x is None or max_y is None:
        return GraphBounds(0.0, 0.0, 0.0, 0.0)
    return GraphBounds(float(min_x), float(min_y), float(max_x), float(max_y))


def next_unique_suffix_id(base_id: str, used_ids: set[str]) -> str:
    suffix = 2
    while True:
        candidate = f"{base_id}_{suffix}"
        if candidate not in used_ids:
            return candidate
        suffix += 1


def build_insert_id_remap(import_node_ids: list[str], *, existing_node_ids: set[str]) -> IdRemapPlan:
    used_ids = set(existing_node_ids)
    mapping: dict[str, str] = {}
    for old_id in list(import_node_ids):
        src = str(old_id or "").strip()
        base_id = src or "node"
        if base_id not in used_ids:
            dst = base_id
        else:
            dst = next_unique_suffix_id(base_id, used_ids)
        mapping[src] = dst
        used_ids.add(dst)
    return IdRemapPlan(mapping=mapping)


def remap_identity_fields_on_node(node_data: dict[str, Any], remap: dict[str, str]) -> None:
    custom_obj = node_data.get("custom")
    if isinstance(custom_obj, dict):
        svc_id = custom_obj.get("svcId")
        if isinstance(svc_id, str) and svc_id in remap:
            custom_obj["svcId"] = remap[svc_id]
        operator_id = custom_obj.get("operatorId")
        if isinstance(operator_id, str) and operator_id in remap:
            custom_obj["operatorId"] = remap[operator_id]
    f8_sys_obj = node_data.get("f8_sys")
    if isinstance(f8_sys_obj, dict):
        svc_id = f8_sys_obj.get("svcId")
        if isinstance(svc_id, str) and svc_id in remap:
            f8_sys_obj["svcId"] = remap[svc_id]


def remap_insert_layout(layout_data: dict[str, Any], remap_plan: IdRemapPlan) -> dict[str, Any]:
    remap = dict(remap_plan.mapping)
    rewritten = deepcopy(layout_data)
    nodes_obj = rewritten.get("nodes")
    if isinstance(nodes_obj, dict):
        rewritten_nodes: dict[str, Any] = {}
        for old_id, node_data in list(nodes_obj.items()):
            if not isinstance(node_data, dict):
                continue
            src_id = str(old_id or "").strip()
            dst_id = remap.get(src_id, src_id)
            next_node = deepcopy(node_data)
            next_node["id"] = dst_id
            remap_identity_fields_on_node(next_node, remap)
            rewritten_nodes[dst_id] = next_node
        rewritten["nodes"] = rewritten_nodes

    connections_obj = rewritten.get("connections")
    if isinstance(connections_obj, list):
        rewritten_connections: list[dict[str, Any]] = []
        for raw_conn in list(connections_obj):
            if not isinstance(raw_conn, dict):
                continue
            next_conn = deepcopy(raw_conn)
            out_obj = next_conn.get("out")
            if isinstance(out_obj, list) and len(out_obj) >= 2:
                src_out_node_id = str(out_obj[0] or "").strip()
                out_obj[0] = remap.get(src_out_node_id, src_out_node_id)
            in_obj = next_conn.get("in")
            if isinstance(in_obj, list) and len(in_obj) >= 2:
                src_in_node_id = str(in_obj[0] or "").strip()
                in_obj[0] = remap.get(src_in_node_id, src_in_node_id)
            rewritten_connections.append(next_conn)
        rewritten["connections"] = rewritten_connections
    return rewritten


def shift_insert_layout_nodes(layout_data: dict[str, Any], *, dx: float, dy: float) -> None:
    nodes_obj = layout_data.get("nodes")
    if not isinstance(nodes_obj, dict):
        return
    for node_data in list(nodes_obj.values()):
        if not isinstance(node_data, dict):
            continue
        pos = coerce_layout_pos(node_data)
        if pos is None:
            continue
        node_data["pos"] = [float(pos[0] + dx), float(pos[1] + dy)]
