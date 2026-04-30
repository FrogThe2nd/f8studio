from __future__ import annotations

from collections.abc import Callable
from typing import Any

import shortuuid
from qtpy import QtCore, QtGui, QtWidgets
from NodeGraphQt import NodeGraph
from NodeGraphQt.base.commands import NodeMovedCmd

from .graph_connection_rules import GraphConnectionRulesMixin
from .container_basenode import F8StudioContainerBaseNode
from .graph_container_binding import GraphContainerBindingMixin
from .graph_component_actions import GraphComponentActionsMixin
from .graph_backdrop_actions import GraphBackdropActionsMixin
from .graph_duplicate_actions import GraphDuplicateActionsMixin
from .graph_factory_flow import GraphFactoryFlowMixin
from .graph_identity_actions import GraphIdentityActionsMixin
from .graph_node_docs_actions import GraphNodeDocsActionsMixin
from .graph_insert_flow import GraphInsertFlowMixin, GraphInsertRequest, InsertResult
from .graph_layering import GraphLayeringMixin
from .graph_search_actions import GraphSearchActionsMixin
from .graph_service_reclaim import GraphServiceReclaimMixin
from .graph_node_state_actions import GraphNodeStateActionsMixin
from .graph_variant_actions import GraphVariantActionsMixin
from .insert_layout_utils import GraphBounds
from .layers import normalize_layer_defs
from .service_bridge_protocol import ServiceBridge
from .session_layout_codec import SessionLayoutCodecMixin
from .viewer import F8StudioNodeViewer
from ..assets.common import JsonObject
from ..assets.common.asset_cache_events import subscribe_asset_cache_changed
from ..render_nodes.backdrop import BackdropRenderNode
from ..ui.dialogs.node_docs_dialog import SpecTemplate
from ..ui.support.ui_notifications import show_warning
from f8pysdk.specs import F8VariantRecord

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


class F8StudioGraph(
    GraphComponentActionsMixin,
    GraphBackdropActionsMixin,
    GraphVariantActionsMixin,
    GraphIdentityActionsMixin,
    GraphDuplicateActionsMixin,
    GraphNodeDocsActionsMixin,
    GraphNodeStateActionsMixin,
    GraphLayeringMixin,
    GraphConnectionRulesMixin,
    GraphInsertFlowMixin,
    SessionLayoutCodecMixin,
    GraphFactoryFlowMixin,
    GraphContainerBindingMixin,
    GraphServiceReclaimMixin,
    GraphSearchActionsMixin,
    NodeGraph,
):
    """Main F8PyStudio controller class."""

    node_placement_changed = QtCore.Signal(bool, str)
    layers_changed = QtCore.Signal()
    active_layers_changed = QtCore.Signal(tuple)

    def __init__(self, parent=None, **kwargs):
        """
        Args:
            parent (object): object parent.
            **kwargs (dict): Used for overriding internal objects at init time.
        """
        asset_cache_auto_refresh = bool(kwargs.pop("asset_cache_auto_refresh", True))
        undo_stack = kwargs.get("undo_stack") or QtGui.QUndoStack(parent)
        viewer = kwargs.get("viewer") or F8StudioNodeViewer(undo_stack=undo_stack)

        kwargs["undo_stack"] = undo_stack
        kwargs["viewer"] = viewer
        super().__init__(parent, **kwargs)
        viewer.set_graph(self)
        viewer.node_placement_changed.connect(self._on_viewer_node_placement_changed)  # type: ignore[attr-defined]

        self.uuid_length = kwargs.get("uuid_length", 4)
        self.uuid_generator = shortuuid.ShortUUID()
        self._loading_session = False
        self._skip_post_load_viewer_refresh = False
        self._tab_search_node_type_aliases: dict[str, str] = {}
        self._tab_search_component_ids: dict[str, str] = {}
        self._component_menu_node_types: set[str] = set()
        self._backdrop_create_menu_node_types: set[str] = set()
        self._backdrop_wrap_menu_node_types: set[str] = set()
        self._backdrop_registered_node_type: str | None = None
        self._variant_menu_node_types: set[str] = set()
        self._identity_menu_node_types: set[str] = set()
        self._duplicate_menu_node_types: set[str] = set()
        self._node_docs_menu_node_types: set[str] = set()
        self._node_state_menu_node_types: set[str] = set()
        self._graph_context_menu_commands_installed = False
        self._node_docs_dialog_opener: Callable[[SpecTemplate, str, str], None] | None = None
        self._unsubscribe_asset_cache_changed = None
        if asset_cache_auto_refresh:
            self._unsubscribe_asset_cache_changed = subscribe_asset_cache_changed(self._on_asset_cache_changed)
        self._global_hotkey_controller: Any | None = None
        self._global_hotkey_node_id_snapshot: frozenset[str] = frozenset()
        self.property_changed.connect(self._on_property_changed)  # type: ignore[attr-defined]
        self.node_created.connect(self._on_global_hotkey_node_created)  # type: ignore[attr-defined]
        self.nodes_deleted.connect(self._on_global_hotkey_nodes_deleted)  # type: ignore[attr-defined]
        self._undo_stack.indexChanged.connect(self._on_global_hotkey_undo_index_changed)  # type: ignore[attr-defined]
        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]

        self._service_bridge: ServiceBridge | None = None
        self._reclaim_timers: dict[str, QtCore.QTimer] = {}
        self._session_layer_defs = normalize_layer_defs(())
        self._active_layer_ids = tuple(layer.id for layer in self._session_layer_defs if layer.default_visible)

        self.nodes_deleted.connect(self._on_nodes_deleted)  # type: ignore[attr-defined]
        self.nodes_deleted.connect(self.on_layering_nodes_deleted)  # type: ignore[attr-defined]
        self.nodes_deleted.connect(self._refresh_view_after_nodes_deleted)  # type: ignore[attr-defined]
        self.port_connected.connect(self._on_port_connected)  # type: ignore[attr-defined]
        self.port_disconnected.connect(self._on_port_disconnected)  # type: ignore[attr-defined]
        self._install_graph_context_menu_commands()

    def rebuild_asset_search_sources(self) -> None:
        self.refresh_tab_search_if_visible()

    def _on_asset_cache_changed(self) -> None:
        self.rebuild_asset_search_sources()

    def _clear_asset_cache_changed_subscription(self) -> None:
        unsubscribe = self._unsubscribe_asset_cache_changed
        self._unsubscribe_asset_cache_changed = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_destroyed(self, _obj: object) -> None:
        self._clear_asset_cache_changed_subscription()

    def _on_viewer_node_placement_changed(self, active: bool, label: str) -> None:
        self.node_placement_changed.emit(bool(active), str(label or ""))

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        viewer = self.viewer()
        if viewer is None:
            return None
        window = viewer.window()
        if isinstance(window, QtWidgets.QWidget):
            return window
        return viewer

    def set_skip_post_load_viewer_refresh(self, skip: bool) -> None:
        self._skip_post_load_viewer_refresh = bool(skip)

    def clear_undo_history(self) -> None:
        self._undo_stack.clear()

    def create_node_for_session_load(
        self,
        node_type: str,
        *,
        name: str | None = None,
        pos: tuple[float, float] | None = None,
        selected: bool = False,
        push_undo: bool = False,
    ) -> object | None:
        previous_loading = bool(self._loading_session)
        self._loading_session = True
        try:
            return self.create_node(
                node_type,
                name=name,
                pos=pos,
                selected=selected,
                push_undo=push_undo,
            )
        finally:
            self._loading_session = previous_loading

    def apply_variant_record_to_node(
        self,
        *,
        node: object,
        variant_record: F8VariantRecord,
        variant_spec_json: JsonObject,
    ) -> None:
        self._apply_variant_to_node(
            node=node,
            variant_record=variant_record,
            variant_spec_json=variant_spec_json,
        )

    def begin_node_placement(self, node_type: str, node_label: str) -> None:
        viewer = self._viewer
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.begin_node_placement(node_type=node_type, node_label=node_label)

    def cancel_node_placement(self) -> None:
        viewer = self._viewer
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.cancel_node_placement()

    def pending_node_placement_type(self) -> str | None:
        viewer = self._viewer
        if not isinstance(viewer, F8StudioNodeViewer):
            return None
        return viewer.pending_node_type()

    def begin_graph_placement(self, request: GraphInsertRequest, *, label: str = "") -> None:
        viewer = self._viewer
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.begin_graph_placement(request=request, label=label)

    def set_node_docs_dialog_opener(
        self,
        opener: Callable[[SpecTemplate, str, str], None] | None,
    ) -> None:
        self._node_docs_dialog_opener = opener

    def open_node_docs_dialog(self, *, spec: SpecTemplate, node_id: str, node_name: str) -> None:
        opener = self._node_docs_dialog_opener
        if opener is None:
            parent = self._notification_parent()
            if parent is not None:
                show_warning(parent, "Node docs unavailable", "No node docs dialog handler is configured.")
            return
        opener(spec, node_id, node_name)

    def _install_graph_context_menu_commands(self) -> None:
        if self._graph_context_menu_commands_installed:
            return
        graph_menu = self.context_menu()
        if graph_menu is None:
            return
        self._graph_context_menu_commands_installed = True

    def cancel_graph_placement(self) -> None:
        viewer = self._viewer
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.cancel_graph_placement()

    def set_service_bridge(self, bridge: ServiceBridge | None) -> None:
        self._service_bridge = bridge

    @property
    def service_bridge(self) -> ServiceBridge | None:
        return self._service_bridge

    def set_global_hotkey_controller(self, controller: Any | None) -> None:
        self._global_hotkey_controller = controller

    @property
    def global_hotkey_controller(self) -> Any | None:
        return self._global_hotkey_controller

    def schedule_global_hotkey_refresh(self) -> None:
        controller = self._global_hotkey_controller
        if controller is None:
            return
        try:
            controller.schedule_refresh()
        except Exception:
            logger.exception("Failed to schedule global hotkey refresh")

    def _current_global_hotkey_node_id_snapshot(self) -> frozenset[str]:
        node_ids: set[str] = set()
        for node in list(self.all_nodes() or []):
            try:
                node_id = str(node.id or "").strip()
            except (AttributeError, RuntimeError, TypeError):
                continue
            if node_id:
                node_ids.add(node_id)
        return frozenset(node_ids)

    def _schedule_global_hotkey_refresh_after_node_set_changed(self) -> None:
        self._global_hotkey_node_id_snapshot = self._current_global_hotkey_node_id_snapshot()
        self.schedule_global_hotkey_refresh()

    def _on_property_changed(self, node: Any, name: str, value: Any) -> None:
        controller = self._global_hotkey_controller
        if controller is None:
            return
        try:
            controller.on_graph_property_changed(node, name, value)
        except Exception:
            logger.exception("Failed to handle global hotkey property change")

    def _on_global_hotkey_node_created(self, _node: object) -> None:
        self._schedule_global_hotkey_refresh_after_node_set_changed()

    def _on_global_hotkey_nodes_deleted(self, _node_ids: list[str]) -> None:
        self._schedule_global_hotkey_refresh_after_node_set_changed()

    def _on_global_hotkey_undo_index_changed(self, _index: int) -> None:
        current_snapshot = self._current_global_hotkey_node_id_snapshot()
        if current_snapshot == self._global_hotkey_node_id_snapshot:
            return
        self._global_hotkey_node_id_snapshot = current_snapshot
        self.schedule_global_hotkey_refresh()

    def _on_node_backdrop_updated(self, node_id: str, update_property: str, value: Any) -> None:
        node = self.get_node_by_id(str(node_id or ""))
        if isinstance(node, (BackdropRenderNode, F8StudioContainerBaseNode)):
            node.on_backdrop_updated(str(update_property or ""), value)
            return
        super()._on_node_backdrop_updated(node_id, update_property, value)

    def _on_nodes_moving(self, node_data: Any) -> None:
        _ = node_data
        return

    def _refresh_view_after_nodes_deleted(self, _node_ids: list[str]) -> None:
        viewer = self.viewer()
        if viewer is None:
            return
        scene = viewer.scene()
        if scene is not None:
            scene.invalidate(scene.sceneRect(), QtWidgets.QGraphicsScene.AllLayers)
            scene.update()
        viewer.resetCachedContent()
        viewport = viewer.viewport()
        if viewport is not None:
            viewport.update()

    @staticmethod
    def _container_parent_view(node_view: Any) -> Any | None:
        try:
            container_view = node_view._container_item
        except (AttributeError, RuntimeError, TypeError):
            return None
        return container_view

    @classmethod
    def _has_moved_container_ancestor(cls, node_view: Any, moved_views: set[Any]) -> bool:
        container_view = cls._container_parent_view(node_view)
        while container_view is not None:
            if container_view in moved_views:
                return True
            container_view = cls._container_parent_view(container_view)
        return False

    @classmethod
    def _filter_redundant_container_child_moves(cls, node_data: dict[Any, Any]) -> dict[Any, Any]:
        if len(node_data) < 2:
            return dict(node_data)
        moved_views = set(node_data.keys())
        filtered: dict[Any, Any] = {}
        for node_view, prev_pos in node_data.items():
            if cls._has_moved_container_ancestor(node_view, moved_views):
                continue
            filtered[node_view] = prev_pos
        return filtered

    def _on_nodes_moved(self, node_data: dict[Any, Any]) -> None:
        filtered_node_data = self._filter_redundant_container_child_moves(node_data)
        if not filtered_node_data:
            return
        self._undo_stack.beginMacro("move nodes")
        for node_view, prev_pos in filtered_node_data.items():
            node = self._model.nodes[node_view.id]
            self._undo_stack.push(NodeMovedCmd(node, node.pos(), prev_pos))
        self._undo_stack.endMacro()


__all__ = [
    "F8StudioGraph",
    "GraphBounds",
    "GraphInsertRequest",
    "InsertResult",
    "MISSING_SERVICE_NODE_TYPE",
    "MISSING_OPERATOR_NODE_TYPE",
]
