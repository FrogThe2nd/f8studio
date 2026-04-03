from __future__ import annotations

import logging
from typing import Protocol

from qtpy import QtCore, QtWidgets

from ...ai_assist.graph_context import GraphContextSnapshot, build_graph_context_snapshot

logger = logging.getLogger(__name__)


class GraphSelectionSource(Protocol):
    node_selected: object
    node_selection_changed: object
    nodes_deleted: object
    property_changed: object
    port_connected: object
    port_disconnected: object

    def selected_nodes(self) -> list[object]: ...


def wire_graph_signals(
    *,
    graph: GraphSelectionSource | None,
    on_graph_selection_signal: object,
    on_graph_selection_changed: object,
    on_graph_nodes_deleted: object,
    on_graph_property_changed: object,
    on_graph_ports_changed: object,
) -> bool:
    if graph is None:
        return False
    graph.node_selected.connect(on_graph_selection_signal)  # type: ignore[attr-defined]
    graph.node_selection_changed.connect(on_graph_selection_changed)  # type: ignore[attr-defined]
    graph.nodes_deleted.connect(on_graph_nodes_deleted)  # type: ignore[attr-defined]
    graph.property_changed.connect(on_graph_property_changed)  # type: ignore[attr-defined]
    graph.port_connected.connect(on_graph_ports_changed)  # type: ignore[attr-defined]
    graph.port_disconnected.connect(on_graph_ports_changed)  # type: ignore[attr-defined]
    return True


def schedule_selection_refresh(
    *,
    widget: QtWidgets.QWidget,
    selection_timer: QtCore.QTimer,
    apply_graph_selection: object,
) -> None:
    if not widget.isVisible():
        apply_graph_selection()
        return
    selection_timer.start()


def apply_graph_selection(
    *,
    graph: GraphSelectionSource | None,
    set_current_selection_snapshot: object,
) -> None:
    if graph is None:
        set_current_selection_snapshot(None, mode="none")
        return
    try:
        selected_nodes = list(graph.selected_nodes() or [])
    except Exception:
        logger.exception("Failed to query AI assist graph selection")
        set_current_selection_snapshot(None, mode="none")
        return
    if not selected_nodes:
        set_current_selection_snapshot(None, mode="none")
        return
    snapshot = build_graph_context_snapshot(graph, selected_nodes)
    set_current_selection_snapshot(snapshot, mode="active" if snapshot is not None else "none")
