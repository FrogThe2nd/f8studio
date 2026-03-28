from __future__ import annotations

from typing import Any

import shortuuid
from qtpy import QtCore, QtGui, QtWidgets
from NodeGraphQt import NodeGraph

from .graph_connection_rules import GraphConnectionRulesMixin
from .graph_container_binding import GraphContainerBindingMixin
from .graph_duplicate_actions import GraphDuplicateActionsMixin
from .graph_factory_flow import GraphFactoryFlowMixin
from .graph_identity_actions import GraphIdentityActionsMixin
from .graph_node_docs_actions import GraphNodeDocsActionsMixin
from .graph_insert_flow import GraphInsertFlowMixin, GraphInsertRequest, InsertResult
from .graph_search_actions import GraphSearchActionsMixin
from .graph_service_reclaim import GraphServiceReclaimMixin
from .graph_variant_actions import GraphVariantActionsMixin
from .insert_layout_utils import GraphBounds
from .service_bridge_protocol import ServiceBridge
from .session_layout_codec import SessionLayoutCodecMixin
from .viewer import F8StudioNodeViewer
from ..ui_notifications import show_warning

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


class F8StudioGraph(
    GraphVariantActionsMixin,
    GraphIdentityActionsMixin,
    GraphDuplicateActionsMixin,
    GraphNodeDocsActionsMixin,
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
        self._variant_menu_node_types: set[str] = set()
        self._identity_menu_node_types: set[str] = set()
        self._duplicate_menu_node_types: set[str] = set()
        self._node_docs_menu_node_types: set[str] = set()

        self.property_changed.connect(self._on_property_changed)  # type: ignore[attr-defined]

        self._service_bridge: ServiceBridge | None = None
        self._global_hotkey_controller: Any | None = None
        self._reclaim_timers: dict[str, QtCore.QTimer] = {}

        self.nodes_deleted.connect(self._on_nodes_deleted)  # type: ignore[attr-defined]
        self.port_connected.connect(self._on_port_connected)  # type: ignore[attr-defined]
        self.port_disconnected.connect(self._on_port_disconnected)  # type: ignore[attr-defined]

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


__all__ = [
    "F8StudioGraph",
    "GraphBounds",
    "GraphInsertRequest",
    "InsertResult",
    "MISSING_SERVICE_NODE_TYPE",
    "MISSING_OPERATOR_NODE_TYPE",
]
