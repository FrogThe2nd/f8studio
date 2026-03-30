from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .insert_layout_utils import (
    GraphBounds,
    IdRemapPlan,
    build_insert_id_remap,
    coerce_layout_pos,
    compute_layout_bbox,
    next_unique_suffix_id,
    remap_identity_fields_on_node,
    remap_insert_layout,
    shift_insert_layout_nodes,
)
from .layers import (
    augment_layer_defs_for_layout_nodes,
    extract_node_layer_ids_from_ui_state,
    layout_layer_defs_from_layout,
    remap_layout_node_layer_ids,
)
from .viewer import F8StudioNodeViewer
from ..session_migration import extract_layout as _extract_session_layout
from ..ui_notifications import show_warning


@dataclass(frozen=True)
class GraphInsertRequest:
    source_path: str
    layout_data: dict[str, Any]
    source_bbox: GraphBounds
    node_count: int
    connection_count: int
    dropped_invalid_connections: int = 0


@dataclass(frozen=True)
class InsertResult:
    inserted_node_ids: list[str] = field(default_factory=list)
    inserted_connection_count: int = 0
    inserted_bbox: GraphBounds = field(default_factory=lambda: GraphBounds(0.0, 0.0, 0.0, 0.0))
    id_remap_plan: IdRemapPlan = field(default_factory=IdRemapPlan)
    dropped_invalid_connections: int = 0


class GraphInsertFlowMixin:
    @staticmethod
    def _coerce_layout_pos(node_data: dict[str, Any]) -> tuple[float, float] | None:
        return coerce_layout_pos(node_data)

    @classmethod
    def _compute_layout_bbox(cls, layout_data: dict[str, Any]) -> GraphBounds:
        _ = cls
        return compute_layout_bbox(layout_data)

    def _normalize_insert_layout(self, raw_layout_data: dict[str, Any]) -> tuple[dict[str, Any], int]:
        layout_data = deepcopy(raw_layout_data)
        self._inject_node_ids(layout_data)
        layout_data = self._restore_missing_session_nodes(layout_data)
        layout_data = self._coerce_missing_session_nodes(layout_data)
        layout_data = self._merge_session_specs(layout_data)
        layout_data = self._strip_port_restore_data(layout_data)
        layout_data = self._strip_unknown_session_custom_properties(layout_data)
        before_connections = 0
        connections_obj = layout_data.get("connections")
        if isinstance(connections_obj, list):
            before_connections = len(connections_obj)
        layout_data = self._strip_invalid_connections(layout_data)
        after_connections = 0
        connections_obj = layout_data.get("connections")
        if isinstance(connections_obj, list):
            after_connections = len(connections_obj)
        dropped = max(0, int(before_connections - after_connections))
        return layout_data, dropped

    def prepare_insert_graph_from_file(self, path: str) -> GraphInsertRequest:
        file_path = str(path or "").strip()
        if not file_path:
            raise ValueError("insert graph path is empty")
        if not os.path.isfile(file_path):
            raise IOError(f"file does not exist: {file_path}")
        with open(file_path, encoding="utf-8-sig") as data_file:
            payload = json.load(data_file)
        raw_layout_data = _extract_session_layout(payload)
        layout_data, dropped_invalid_connections = self._normalize_insert_layout(raw_layout_data)

        node_count = 0
        nodes_obj = layout_data.get("nodes")
        if isinstance(nodes_obj, dict):
            node_count = len(nodes_obj)

        connection_count = 0
        connections_obj = layout_data.get("connections")
        if isinstance(connections_obj, list):
            connection_count = len(connections_obj)

        source_bbox = self._compute_layout_bbox(layout_data)
        return GraphInsertRequest(
            source_path=file_path,
            layout_data=layout_data,
            source_bbox=source_bbox,
            node_count=int(node_count),
            connection_count=int(connection_count),
            dropped_invalid_connections=int(dropped_invalid_connections),
        )

    def _existing_node_ids(self) -> set[str]:
        existing: set[str] = set()
        for node in list(self.all_nodes() or []):
            try:
                node_id = str(node.id or "").strip()
            except (AttributeError, RuntimeError, TypeError):
                node_id = ""
            if node_id:
                existing.add(node_id)
        return existing

    @staticmethod
    def _next_unique_suffix_id(base_id: str, used_ids: set[str]) -> str:
        return next_unique_suffix_id(base_id, used_ids)

    def _build_insert_id_remap(self, import_node_ids: list[str]) -> IdRemapPlan:
        return build_insert_id_remap(import_node_ids, existing_node_ids=self._existing_node_ids())

    @staticmethod
    def _remap_identity_fields_on_node(node_data: dict[str, Any], remap: dict[str, str]) -> None:
        remap_identity_fields_on_node(node_data, remap)

    @classmethod
    def _remap_insert_layout(cls, layout_data: dict[str, Any], remap_plan: IdRemapPlan) -> dict[str, Any]:
        _ = cls
        return remap_insert_layout(layout_data, remap_plan)

    @classmethod
    def _shift_insert_layout_nodes(cls, layout_data: dict[str, Any], *, dx: float, dy: float) -> None:
        _ = cls
        shift_insert_layout_nodes(layout_data, dx=dx, dy=dy)

    def _refresh_inserted_node_views(self, inserted_node_ids: list[str]) -> None:
        inserted_ids = {
            str(node_id or "").strip()
            for node_id in list(inserted_node_ids or [])
            if str(node_id or "").strip()
        }
        if not inserted_ids:
            return
        for node in list(self.all_nodes() or []):
            node_id = str(node.id or "").strip()
            if node_id not in inserted_ids:
                continue
            view = node.view
            view.draw_node()
            try:
                view.sync_proxy_mode(force=True)
            except AttributeError:
                pass

    def _refresh_viewer_after_insert(self) -> None:
        viewer = self.viewer()
        if not isinstance(viewer, F8StudioNodeViewer):
            return
        viewer.refresh_auto_proxy_mode(force=True)

    def apply_insert_graph(self, request: GraphInsertRequest, *, anchor_x: float, anchor_y: float) -> InsertResult:
        source_layout = deepcopy(request.layout_data)
        nodes_obj = source_layout.get("nodes")
        if not isinstance(nodes_obj, dict) or not nodes_obj:
            raise ValueError("insert graph contains no nodes")

        import_node_ids = [str(node_id or "").strip() for node_id in list(nodes_obj.keys())]
        remap_plan = self._build_insert_id_remap(import_node_ids)
        remapped_layout = self._remap_insert_layout(source_layout, remap_plan)
        imported_layer_defs = augment_layer_defs_for_layout_nodes(
            layout_layer_defs_from_layout(remapped_layout),
            remapped_layout.get("nodes"),
        )
        merged_layer_defs, layer_id_remap = self.merge_imported_layer_defs(imported_layer_defs)
        remap_layout_node_layer_ids(remapped_layout.get("nodes"), layer_id_remap)

        dx = float(anchor_x) - float(request.source_bbox.min_x)
        dy = float(anchor_y) - float(request.source_bbox.min_y)
        self._shift_insert_layout_nodes(remapped_layout, dx=dx, dy=dy)
        inserted_bbox = self._compute_layout_bbox(remapped_layout)

        before_connections = 0
        connections_obj = remapped_layout.get("connections")
        if isinstance(connections_obj, list):
            before_connections = len(connections_obj)
        remapped_layout = self._strip_invalid_connections(remapped_layout)
        after_connections = 0
        connections_obj = remapped_layout.get("connections")
        if isinstance(connections_obj, list):
            after_connections = len(connections_obj)
        dropped_invalid_connections = max(0, int(before_connections - after_connections))

        activate_layer_ids: set[str] = set(self.active_layer_ids())
        nodes_after_layer_merge = remapped_layout.get("nodes")
        if isinstance(nodes_after_layer_merge, dict):
            for node_data in list(nodes_after_layer_merge.values()):
                if not isinstance(node_data, dict):
                    continue
                for layer_id in extract_node_layer_ids_from_ui_state(node_data.get("f8_ui_state")):
                    activate_layer_ids.add(layer_id)

        prev_loading = bool(self._loading_session)
        self._loading_session = True
        try:
            deserialize_layout = dict(remapped_layout)
            deserialize_layout.pop("f8_layers", None)
            super().deserialize_session(deserialize_layout, clear_session=False, clear_undo_stack=False)
        finally:
            self._loading_session = prev_loading
        self.set_session_layer_defs(
            merged_layer_defs,
            preserve_active=True,
            activate_layer_ids=tuple(sorted(activate_layer_ids)),
        )
        inserted_node_ids = [remap_plan.mapping.get(src, src) for src in import_node_ids]
        self._rebind_container_children()
        self._refresh_all_inline_state_read_only()
        self._refresh_inserted_node_views(inserted_node_ids)
        all_nodes = list(self.all_nodes() or [])
        selected_ids = set(inserted_node_ids)
        for node in all_nodes:
            try:
                node.set_property("selected", bool(str(node.id or "") in selected_ids), push_undo=False)
            except (AttributeError, RuntimeError, TypeError):
                continue
        self._refresh_viewer_after_insert()

        total_dropped = int(request.dropped_invalid_connections) + int(dropped_invalid_connections)
        if total_dropped > 0:
            show_warning(
                self._notification_parent(),
                "Insert Graph",
                f"Dropped {total_dropped} invalid connection(s) while inserting graph.",
            )

        return InsertResult(
            inserted_node_ids=inserted_node_ids,
            inserted_connection_count=int(after_connections),
            inserted_bbox=inserted_bbox,
            id_remap_plan=remap_plan,
            dropped_invalid_connections=int(total_dropped),
        )
