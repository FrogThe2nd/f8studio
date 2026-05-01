from __future__ import annotations

from typing import Any, Protocol, cast

from qtpy import QtWidgets

from .service_bridge_protocol import ServiceBridge
from .service_basenode import F8StudioServiceBaseNode
from ..ui.dialogs.monitor_stream_dialog import open_monitor_stream_dialog
from ..ui.support.ui_notifications import show_warning


class _ContextNodesMenuProtocol(Protocol):
    def add_command(self, label: str, *, func: Any, node_type: str) -> object: ...


class _GraphMonitorHost(Protocol):
    _monitor_menu_node_types: set[str]
    _service_bridge: ServiceBridge | None

    def context_nodes_menu(self) -> _ContextNodesMenuProtocol | None: ...

    def tr(self, text: str) -> str: ...

    def _notification_parent(self) -> QtWidgets.QWidget | None: ...


class GraphMonitorActionsMixin:
    def install_monitor_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        host = cast(_GraphMonitorHost, self)
        nodes_menu = host.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            if not issubclass(node_cls, F8StudioServiceBaseNode):
                continue
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in host._monitor_menu_node_types:
                continue
            nodes_menu.add_command(
                host.tr("View Monitor Stream..."),
                func=self._on_view_monitor_stream_menu_action,
                node_type=node_type,
            )
            host._monitor_menu_node_types.add(node_type)

    def _on_view_monitor_stream_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        if not isinstance(node, F8StudioServiceBaseNode):
            return
        service_id = str(node.id or "").strip()
        if not service_id:
            return
        host = cast(_GraphMonitorHost, self)
        bridge = host._service_bridge
        if bridge is None:
            show_warning(
                host._notification_parent(),
                host.tr("Monitor Stream Unavailable"),
                host.tr("No service bridge is configured for this graph."),
            )
            return
        open_monitor_stream_dialog(
            parent=host._notification_parent(),
            bridge=bridge,
            service_id=service_id,
        )
