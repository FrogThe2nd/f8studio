from __future__ import annotations

import logging
from typing import Any, Protocol, cast

from qtpy import QtCore

from ...agents.graph_context import GraphContextSnapshot, build_graph_context_snapshot

logger = logging.getLogger(__name__)


class GraphSelectionSource(Protocol):
    node_selected: object
    node_selection_changed: object
    nodes_deleted: object
    property_changed: object
    port_connected: object
    port_disconnected: object

    def selected_nodes(self) -> list[object]: ...


class _AiBridgeGraphContext(Protocol):
    def set_chat_context_snapshot(self, snapshot: GraphContextSnapshot) -> None: ...

    def set_auto_chat_context_snapshot(self, snapshot: GraphContextSnapshot | None) -> None: ...

    def clear_chat_context_snapshot(self) -> None: ...


class _AiAssistSidebarGraphContextHost(Protocol):
    _studio_graph: GraphSelectionSource | None
    _selection_timer: QtCore.QTimer
    _selection_mode: str
    _current_selection_label: str
    _current_selected_snapshot_preview: GraphContextSnapshot | None
    _pinned_graph_context_snapshot: GraphContextSnapshot | None
    _ai_bridge: _AiBridgeGraphContext

    def isVisible(self) -> bool: ...

    def _refresh_context_toolbar(self) -> None: ...


class AiAssistSidebarGraphContextMixin:
    def _wire_graph_signals(self) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        graph = host._studio_graph
        if graph is None:
            return
        graph.node_selected.connect(self._on_graph_selection_signal)  # type: ignore[attr-defined]
        graph.node_selection_changed.connect(self._on_graph_selection_changed)  # type: ignore[attr-defined]
        graph.nodes_deleted.connect(self._on_graph_nodes_deleted)  # type: ignore[attr-defined]
        graph.property_changed.connect(self._on_graph_property_changed)  # type: ignore[attr-defined]
        graph.port_connected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]
        graph.port_disconnected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]
        self._schedule_selection_refresh()

    def _schedule_selection_refresh(self) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        if not host.isVisible():
            self._apply_graph_selection()
            return
        host._selection_timer.start()

    def _set_current_selection_snapshot(self, snapshot: GraphContextSnapshot | None, *, mode: str) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        host._selection_mode = mode
        if snapshot is None:
            host._current_selection_label = ""
            host._current_selected_snapshot_preview = None
        else:
            host._current_selected_snapshot_preview = snapshot
            host._current_selection_label = snapshot.selection_label
        host._ai_bridge.set_auto_chat_context_snapshot(snapshot)
        host._refresh_context_toolbar()

    def _pin_selected_context(self) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        snapshot = host._current_selected_snapshot_preview
        if snapshot is None:
            return
        host._pinned_graph_context_snapshot = snapshot
        host._ai_bridge.set_chat_context_snapshot(snapshot)
        host._refresh_context_toolbar()

    def _clear_pinned_context(self) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        host._pinned_graph_context_snapshot = None
        host._ai_bridge.clear_chat_context_snapshot()
        host._refresh_context_toolbar()

    @QtCore.Slot(object)
    def _on_graph_selection_signal(self, _node: object) -> None:
        self._schedule_selection_refresh()

    @QtCore.Slot(list, list)
    def _on_graph_selection_changed(self, _selected: list[object], _deselected: list[object]) -> None:
        self._schedule_selection_refresh()

    @QtCore.Slot(list)
    def _on_graph_nodes_deleted(self, _node_ids: list[str]) -> None:
        self._schedule_selection_refresh()

    @QtCore.Slot(object, str, object)
    def _on_graph_property_changed(self, _node: object, _name: str, _value: object) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        if host._selection_mode != "none":
            self._schedule_selection_refresh()

    @QtCore.Slot(object, object)
    def _on_graph_ports_changed(self, _port_a: object, _port_b: object) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        if host._selection_mode != "none":
            self._schedule_selection_refresh()

    def _apply_graph_selection(self) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        graph = host._studio_graph
        if graph is None:
            self._set_current_selection_snapshot(None, mode="none")
            return
        try:
            selected_nodes = list(graph.selected_nodes() or [])
        except Exception:
            logger.exception("Failed to query AI assist graph selection")
            self._set_current_selection_snapshot(None, mode="none")
            return
        if not selected_nodes:
            self._set_current_selection_snapshot(None, mode="none")
            return
        snapshot = build_graph_context_snapshot(graph, selected_nodes)
        self._set_current_selection_snapshot(snapshot, mode="active" if snapshot is not None else "none")

    @QtCore.Slot(bool, str)
    def _on_bridge_chat_context_changed(self, has_context: bool, _node_name: str) -> None:
        host = cast(_AiAssistSidebarGraphContextHost, self)
        if not has_context:
            host._pinned_graph_context_snapshot = None
        host._refresh_context_toolbar()
