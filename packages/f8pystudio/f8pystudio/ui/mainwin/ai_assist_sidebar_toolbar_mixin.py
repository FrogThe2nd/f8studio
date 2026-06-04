from __future__ import annotations

import logging
from typing import Protocol, cast

from qtpy import QtCore, QtGui, QtWidgets

from ...agents.graph_context import GraphContextSnapshot
from ..dialogs.ai_context_inspector import AiContextInspectorDialog
from ..support.ai_context_controls import set_status_label_text, usage_pie_icon
from ..support.studio_theme import ai_context_button_qss, studio_dark_theme

logger = logging.getLogger(__name__)


class _AiBridgeToolbar(Protocol):
    def get_context_breakdown(self) -> dict[str, int]: ...

    def get_context_report(self) -> str: ...

    def get_chat_context_report(self) -> str: ...


class _AiAssistSidebarToolbarHost(Protocol):
    _selection_mode: str
    _current_selected_snapshot_preview: GraphContextSnapshot | None
    _pinned_graph_context_snapshot: GraphContextSnapshot | None
    _ctx_btn: QtWidgets.QToolButton
    _selected_node_label: QtWidgets.QLabel
    _pinned_node_label: QtWidgets.QLabel
    _pin_context_btn: QtWidgets.QToolButton
    _clear_context_btn: QtWidgets.QToolButton
    _ai_bridge: _AiBridgeToolbar


class AiAssistSidebarToolbarMixin:
    @QtCore.Slot(int, int)
    def _on_context_usage_updated(self, used: int, total: int) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        if total <= 0:
            return
        used_ratio = max(0.0, min(1.0, used / total))
        free_ratio = max(0.0, 1.0 - used_ratio)
        p = studio_dark_theme().palette
        if used_ratio < 0.5:
            color = p.info
        elif used_ratio < 0.8:
            color = p.warning
        else:
            color = p.error

        def _fmt(value: int) -> str:
            return f"{value / 1000:.0f}k" if value >= 1000 else str(value)

        free_pct = int(round(free_ratio * 100.0))
        host._ctx_btn.setIcon(usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
        host._ctx_btn.setText(f"{free_pct}% free")
        host._ctx_btn.setStyleSheet(ai_context_button_qss(text_color=color, include_background=True))
        try:
            breakdown = host._ai_bridge.get_context_breakdown()
            host._ctx_btn.setToolTip(
                f"AI Context Usage: {free_pct}% free\n"
                f"System: {_fmt(int(breakdown['system_tokens']))} | "
                f"Chat: {_fmt(int(breakdown['chat_tokens']))}\n"
                f"Used: {_fmt(int(breakdown['used_tokens']))} / {_fmt(int(breakdown['total_tokens']))} tok"
            )
        except Exception:
            logger.exception("Failed to update AI context tooltip")

    def _on_ctx_menu_requested(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(cast(QtWidgets.QWidget, self))
        inspect_act = menu.addAction("Inspect Current Context Payload...")
        inspect_act.triggered.connect(self._inspect_context)
        inspect_graph_act = menu.addAction("Inspect Graph Context Payload...")
        inspect_graph_act.triggered.connect(self._inspect_graph_context)
        host = cast(_AiAssistSidebarToolbarHost, self)
        menu.exec(host._ctx_btn.mapToGlobal(pos))

    def _inspect_context(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        dlg = AiContextInspectorDialog(host._ai_bridge.get_context_report(), cast(QtWidgets.QWidget, self))
        dlg.exec()

    def _inspect_graph_context(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        dlg = AiContextInspectorDialog(host._ai_bridge.get_chat_context_report(), cast(QtWidgets.QWidget, self))
        dlg.exec()

    def _refresh_context_toolbar(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        selection_mode = host._selection_mode
        selected_snapshot = host._current_selected_snapshot_preview
        pinned_snapshot = host._pinned_graph_context_snapshot

        if selection_mode == "active" and selected_snapshot is not None:
            selected_text = f"Sel: {selected_snapshot.selection_label}"
            selected_tooltip = (
                f"Selected nodes: {selected_snapshot.total_selected_count}\n"
                f"One-hop context nodes: {selected_snapshot.total_one_hop_count}\n"
                f"Included connections: {selected_snapshot.total_connection_count}"
            )
        else:
            selected_text = "Sel: none"
            selected_tooltip = "Select one or more nodes to preview graph subgraph context."
        set_status_label_text(host._selected_node_label, selected_text, max_width=host._selected_node_label.maximumWidth())
        host._selected_node_label.setToolTip(selected_tooltip)

        if pinned_snapshot is None:
            set_status_label_text(host._pinned_node_label, "Pin: none", max_width=host._pinned_node_label.maximumWidth())
            host._pinned_node_label.setToolTip("No graph context is currently pinned into chat.")
        else:
            set_status_label_text(
                host._pinned_node_label,
                f"Pin: {pinned_snapshot.selection_label}",
                max_width=host._pinned_node_label.maximumWidth(),
            )
            host._pinned_node_label.setToolTip(
                f"Selected nodes: {pinned_snapshot.total_selected_count}\n"
                f"One-hop context nodes: {pinned_snapshot.total_one_hop_count}\n"
                f"Included connections: {pinned_snapshot.total_connection_count}"
            )

        host._pin_context_btn.setEnabled(selected_snapshot is not None)
        host._clear_context_btn.setEnabled(pinned_snapshot is not None)
