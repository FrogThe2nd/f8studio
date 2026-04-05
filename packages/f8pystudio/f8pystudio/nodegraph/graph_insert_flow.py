# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol, cast

from qtpy import QtWidgets

from ..assets.common import JsonObject, json_object_from_value
from f8pystudio.nodegraph.session_schema import extract_layout as _extract_session_layout
from ..ui.support.ui_notifications import show_warning
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
    F8LayerDef,
    augment_layer_defs_for_layout_nodes,
    extract_node_layer_ids_from_ui_state,
    layout_layer_defs_from_layout,
    remap_layout_node_layer_ids,
)
from .viewer import F8StudioNodeViewer


@dataclass(frozen=True)
class GraphInsertRequest:
    source_path: str
    layout_data: JsonObject
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


class _InsertNodeViewProtocol(Protocol):
    def draw_node(self) -> None: ...

    def sync_proxy_mode(self, *, force: bool = False) -> None: ...


class _InsertNodeProtocol(Protocol):
    id: object
    view: object

    def set_property(self, name: str, value: object, *, push_undo: bool = True) -> None: ...


class _InsertViewerProtocol(Protocol):
    def refresh_auto_proxy_mode(self, *, force: bool = False) -> None: ...


class _GraphInsertHost(Protocol):
    _loading_session: bool | None

    def _inject_node_ids(self, layout_data: JsonObject) -> None: ...

    def _restore_missing_session_nodes(self, layout_data: JsonObject) -> JsonObject: ...

    def _coerce_missing_session_nodes(self, layout_data: JsonObject) -> JsonObject: ...

    def _merge_session_specs(self, layout_data: JsonObject) -> JsonObject: ...

    def _strip_port_restore_data(self, layout_data: JsonObject) -> JsonObject: ...

    def _strip_unknown_session_custom_properties(self, layout_data: JsonObject) -> JsonObject: ...

    def _strip_invalid_connections(self, layout_data: JsonObject) -> JsonObject: ...

    def all_nodes(self) -> list[_InsertNodeProtocol]: ...

    def viewer(self) -> object | None: ...

    def merge_imported_layer_defs(self, imported_defs: tuple[F8LayerDef, ...]) -> tuple[tuple[F8LayerDef, ...], dict[str, str]]: ...

    def active_layer_ids(self) -> tuple[str, ...]: ...

    def deserialize_session(self, layout_data: JsonObject, *, clear_session: bool, clear_undo_stack: bool) -> None: ...

    def set_session_layer_defs(
        self,
        layer_defs: tuple[F8LayerDef, ...],
        *,
        preserve_active: bool,
        activate_layer_ids: tuple[str, ...] | None = None,
    ) -> None: ...

    def _rebind_container_children(self) -> None: ...

    def _refresh_all_inline_state_read_only(self) -> None: ...

    def _notification_parent(self) -> QtWidgets.QWidget | None: ...


class GraphInsertFlowMixin:
    _loading_session: bool | None = None

    @staticmethod
    def _json_object_or_none(value: object) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        return json_object_from_value(cast(object, value))

    @classmethod
    def _json_object_field(cls, payload: JsonObject, key: str) -> JsonObject | None:
        return cls._json_object_or_none(payload.get(key))

    @staticmethod
    def _json_list_length(payload: JsonObject, key: str) -> int:
        raw_list = payload.get(key)
        if not isinstance(raw_list, list):
            return 0
        return len(cast(list[object], raw_list))

    @classmethod
    def _layout_nodes(cls, payload: JsonObject) -> dict[str, JsonObject]:
        raw_nodes = cls._json_object_field(payload, "nodes")
        if raw_nodes is None:
            return {}
        out: dict[str, JsonObject] = {}
        for node_id, raw_node in raw_nodes.items():
            node_payload = cls._json_object_or_none(raw_node)
            if node_payload is None:
                continue
            out[str(node_id)] = node_payload
        return out

    @staticmethod
    def _coerce_layout_pos(node_data: JsonObject) -> tuple[float, float] | None:
        return coerce_layout_pos(node_data)

    @classmethod
    def _compute_layout_bbox(cls, layout_data: JsonObject) -> GraphBounds:
        _ = cls
        return compute_layout_bbox(layout_data)

    def _normalize_insert_layout(self, raw_layout_data: JsonObject) -> tuple[JsonObject, int]:
        host = cast(_GraphInsertHost, cast(object, self))
        layout_data = deepcopy(raw_layout_data)
        host._inject_node_ids(layout_data)
        layout_data = host._restore_missing_session_nodes(layout_data)
        layout_data = host._coerce_missing_session_nodes(layout_data)
        layout_data = host._merge_session_specs(layout_data)
        layout_data = host._strip_port_restore_data(layout_data)
        layout_data = host._strip_unknown_session_custom_properties(layout_data)
        before_connections = self._json_list_length(layout_data, "connections")
        layout_data = host._strip_invalid_connections(layout_data)
        after_connections = self._json_list_length(layout_data, "connections")
        dropped = max(0, before_connections - after_connections)
        return layout_data, dropped

    def prepare_insert_graph_from_file(self, path: str) -> GraphInsertRequest:
        file_path = str(path or "").strip()
        if not file_path:
            raise ValueError("insert graph path is empty")
        if not os.path.isfile(file_path):
            raise IOError(f"file does not exist: {file_path}")
        with open(file_path, encoding="utf-8-sig") as data_file:
            payload = json_object_from_value(cast(object, json.load(data_file)))
        return self.prepare_insert_graph_from_payload(payload, source_path=file_path)

    def prepare_insert_graph_from_payload(self, payload: JsonObject, *, source_path: str = "") -> GraphInsertRequest:
        raw_layout_data = json_object_from_value(_extract_session_layout(payload))
        layout_data, dropped_invalid_connections = self._normalize_insert_layout(raw_layout_data)
        node_count = len(self._layout_nodes(layout_data))
        connection_count = self._json_list_length(layout_data, "connections")
        source_bbox = self._compute_layout_bbox(layout_data)
        return GraphInsertRequest(
            source_path=str(source_path or "<payload>"),
            layout_data=layout_data,
            source_bbox=source_bbox,
            node_count=node_count,
            connection_count=connection_count,
            dropped_invalid_connections=int(dropped_invalid_connections),
        )

    def prepare_insert_graph_from_component(self, component_payload: JsonObject, *, component_name: str) -> GraphInsertRequest:
        return self.prepare_insert_graph_from_payload(component_payload, source_path=f"component:{component_name}")

    def _existing_node_ids(self) -> set[str]:
        host = cast(_GraphInsertHost, cast(object, self))
        existing: set[str] = set()
        for node in list(host.all_nodes() or []):
            node_id = str(node.id or "").strip()
            if node_id:
                existing.add(node_id)
        return existing

    @staticmethod
    def _next_unique_suffix_id(base_id: str, used_ids: set[str]) -> str:
        return next_unique_suffix_id(base_id, used_ids)

    def _build_insert_id_remap(self, import_node_ids: list[str]) -> IdRemapPlan:
        return build_insert_id_remap(import_node_ids, existing_node_ids=self._existing_node_ids())

    @staticmethod
    def _remap_identity_fields_on_node(node_data: JsonObject, remap: dict[str, str]) -> None:
        remap_identity_fields_on_node(node_data, remap)

    @classmethod
    def _remap_insert_layout(cls, layout_data: JsonObject, remap_plan: IdRemapPlan) -> JsonObject:
        _ = cls
        return cast(JsonObject, remap_insert_layout(layout_data, remap_plan))

    @classmethod
    def _shift_insert_layout_nodes(cls, layout_data: JsonObject, *, dx: float, dy: float) -> None:
        _ = cls
        shift_insert_layout_nodes(layout_data, dx=dx, dy=dy)

    def _refresh_inserted_node_views(self, inserted_node_ids: list[str]) -> None:
        host = cast(_GraphInsertHost, cast(object, self))
        inserted_ids = {
            str(node_id or "").strip()
            for node_id in list(inserted_node_ids or [])
            if str(node_id or "").strip()
        }
        if not inserted_ids:
            return
        for node in list(host.all_nodes() or []):
            node_id = str(node.id or "").strip()
            if node_id not in inserted_ids:
                continue
            view = cast(_InsertNodeViewProtocol, node.view)
            view.draw_node()
            try:
                view.sync_proxy_mode(force=True)
            except AttributeError:
                pass

    def _refresh_viewer_after_insert(self) -> None:
        host = cast(_GraphInsertHost, cast(object, self))
        viewer = host.viewer()
        if not isinstance(viewer, F8StudioNodeViewer):
            return
        typed_viewer = cast(_InsertViewerProtocol, viewer)
        typed_viewer.refresh_auto_proxy_mode(force=True)

    def apply_insert_graph(self, request: GraphInsertRequest, *, anchor_x: float, anchor_y: float) -> InsertResult:
        host = cast(_GraphInsertHost, cast(object, self))
        source_layout = deepcopy(request.layout_data)
        nodes_obj = self._layout_nodes(source_layout)
        if not nodes_obj:
            raise ValueError("insert graph contains no nodes")

        import_node_ids = [str(node_id or "").strip() for node_id in list(nodes_obj.keys())]
        remap_plan = self._build_insert_id_remap(import_node_ids)
        remapped_layout = self._remap_insert_layout(source_layout, remap_plan)
        imported_layer_defs = augment_layer_defs_for_layout_nodes(
            layout_layer_defs_from_layout(remapped_layout),
            remapped_layout.get("nodes"),
        )
        merged_layer_defs, layer_id_remap = host.merge_imported_layer_defs(imported_layer_defs)
        remap_layout_node_layer_ids(remapped_layout.get("nodes"), layer_id_remap)

        dx = float(anchor_x) - float(request.source_bbox.min_x)
        dy = float(anchor_y) - float(request.source_bbox.min_y)
        self._shift_insert_layout_nodes(remapped_layout, dx=dx, dy=dy)
        inserted_bbox = self._compute_layout_bbox(remapped_layout)

        before_connections = self._json_list_length(remapped_layout, "connections")
        remapped_layout = host._strip_invalid_connections(remapped_layout)
        after_connections = self._json_list_length(remapped_layout, "connections")
        dropped_invalid_connections = max(0, before_connections - after_connections)

        activate_layer_ids: set[str] = set(host.active_layer_ids())
        for node_data in self._layout_nodes(remapped_layout).values():
            for layer_id in extract_node_layer_ids_from_ui_state(node_data.get("f8_ui_state")):
                activate_layer_ids.add(layer_id)

        prev_loading = bool(host._loading_session)
        host._loading_session = True
        try:
            deserialize_layout = dict(remapped_layout)
            _ = deserialize_layout.pop("f8_layers", None)
            host.deserialize_session(deserialize_layout, clear_session=False, clear_undo_stack=False)
        finally:
            host._loading_session = prev_loading
        host.set_session_layer_defs(
            merged_layer_defs,
            preserve_active=True,
            activate_layer_ids=tuple(sorted(activate_layer_ids)),
        )
        inserted_node_ids = [remap_plan.mapping.get(src, src) for src in import_node_ids]
        host._rebind_container_children()
        host._refresh_all_inline_state_read_only()
        self._refresh_inserted_node_views(inserted_node_ids)
        all_nodes = list(host.all_nodes() or [])
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
                host._notification_parent(),
                "Insert Graph",
                f"Dropped {total_dropped} invalid connection(s) while inserting graph.",
            )

        return InsertResult(
            inserted_node_ids=inserted_node_ids,
            inserted_connection_count=after_connections,
            inserted_bbox=inserted_bbox,
            id_remap_plan=remap_plan,
            dropped_invalid_connections=total_dropped,
        )
