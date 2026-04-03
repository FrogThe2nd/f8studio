from __future__ import annotations

from collections.abc import Callable
from typing import Any

import shortuuid
from qtpy import QtCore, QtGui, QtWidgets
from NodeGraphQt import NodeGraph
from NodeGraphQt.base.commands import NodeMovedCmd

from .graph_connection_rules import GraphConnectionRulesMixin
from .graph_container_binding import GraphContainerBindingMixin
from .graph_duplicate_actions import GraphDuplicateActionsMixin
from .graph_factory_flow import GraphFactoryFlowMixin
from .graph_identity_actions import GraphIdentityActionsMixin
from .graph_node_docs_actions import GraphNodeDocsActionsMixin
from .graph_insert_flow import GraphInsertFlowMixin, GraphInsertRequest, InsertResult
from .graph_layering import GraphLayeringMixin
from .graph_search_actions import GraphSearchActionsMixin
from .graph_service_reclaim import GraphServiceReclaimMixin
from .graph_variant_actions import GraphVariantActionsMixin
from .insert_layout_utils import GraphBounds
from .layers import normalize_layer_defs
from .service_bridge_protocol import ServiceBridge
from .session_layout_codec import SessionLayoutCodecMixin
from .viewer import F8StudioNodeViewer
from ..ui.support.ui_notifications import show_warning
from ..ui.dialogs.node_docs_dialog import SpecTemplate

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


class F8StudioGraph(
    GraphVariantActionsMixin,
    GraphIdentityActionsMixin,
    GraphDuplicateActionsMixin,
    GraphNodeDocsActionsMixin,
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
        self._tab_search_node_type_aliases: dict[str, str] = {}
        self._tab_search_component_ids: dict[str, str] = {}
        self._variant_menu_node_types: set[str] = set()
        self._identity_menu_node_types: set[str] = set()
        self._duplicate_menu_node_types: set[str] = set()
        self._node_docs_menu_node_types: set[str] = set()
        self._graph_context_menu_commands_installed = False
        self._node_docs_dialog_opener: Callable[[SpecTemplate, str, str], None] | None = None
        self._component_insert_dialog_opener: Callable[[tuple[float, float] | None], None] | None = None

        self.property_changed.connect(self._on_property_changed)  # type: ignore[attr-defined]

        self._service_bridge: ServiceBridge | None = None
        self._global_hotkey_controller: Any | None = None
        self._reclaim_timers: dict[str, QtCore.QTimer] = {}
        self._session_layer_defs = normalize_layer_defs(())
        self._active_layer_ids = tuple(layer.id for layer in self._session_layer_defs if layer.default_visible)

        self.nodes_deleted.connect(self._on_nodes_deleted)  # type: ignore[attr-defined]
        self.nodes_deleted.connect(self.on_layering_nodes_deleted)  # type: ignore[attr-defined]
        self.port_connected.connect(self._on_port_connected)  # type: ignore[attr-defined]
        self.port_disconnected.connect(self._on_port_disconnected)  # type: ignore[attr-defined]
        self._install_graph_context_menu_commands()

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

    def set_component_insert_dialog_opener(
        self,
        opener: Callable[[tuple[float, float] | None], None] | None,
    ) -> None:
        self._component_insert_dialog_opener = opener

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

    def open_component_insert_dialog(self, *, scene_pos: tuple[float, float] | None = None) -> None:
        opener = self._component_insert_dialog_opener
        if opener is None:
            parent = self._notification_parent()
            if parent is not None:
                show_warning(parent, "Insert component unavailable", "No component insert dialog handler is configured.")
            return
        opener(scene_pos)

    def _install_graph_context_menu_commands(self) -> None:
        if self._graph_context_menu_commands_installed:
            return
        graph_menu = self.context_menu()
        if graph_menu is None:
            return
        graph_menu.add_separator()
        graph_menu.add_command("Insert Component...", func=self._on_open_component_insert_dialog_action)
        self._graph_context_menu_commands_installed = True

    def _on_open_component_insert_dialog_action(self, _graph: Any) -> None:
        viewer = self.viewer()
        if not isinstance(viewer, F8StudioNodeViewer):
            self.open_component_insert_dialog(scene_pos=None)
            return
        scene_pos_qt = viewer.scene_cursor_pos()
        self.open_component_insert_dialog(scene_pos=(float(scene_pos_qt.x()), float(scene_pos_qt.y())))

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

    def _on_property_changed(self, node: Any, name: str, value: Any) -> None:
        _ = (node, name, value)
        return

    def _on_nodes_moving(self, node_data: Any) -> None:
        _ = node_data
        return

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
