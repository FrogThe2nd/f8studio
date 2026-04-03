from __future__ import annotations

import logging

from qtpy import QtCore, QtGui, QtWidgets

from ...ai_assist.graph_context import GraphContextSnapshot
from ..support.ai_context_controls import set_status_label_text, usage_pie_icon

logger = logging.getLogger(__name__)


def update_context_usage(
    *,
    context_button: QtWidgets.QToolButton,
    used: int,
    total: int,
    get_context_breakdown: object,
) -> None:
    if total <= 0:
        return
    used_ratio = max(0.0, min(1.0, used / total))
    free_ratio = max(0.0, 1.0 - used_ratio)
    if used_ratio < 0.5:
        color = "#4fc3f7"
    elif used_ratio < 0.8:
        color = "#ffd54f"
    else:
        color = "#ef9a9a"

    def _fmt(value: int) -> str:
        return f"{value / 1000:.0f}k" if value >= 1000 else str(value)

    free_pct = int(round(free_ratio * 100.0))
    context_button.setIcon(usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
    context_button.setText(f"{free_pct}% free")
    context_button.setStyleSheet(
        f"QToolButton {{ color: {color}; border: none; padding: 0 4px; background: transparent; font-size: 10pt; }}"
        "QToolButton:hover { color: white; }"
    )
    try:
        breakdown = get_context_breakdown()
        tip = (
            f"AI Context Usage: {free_pct}% free\n"
            f"System: {_fmt(int(breakdown['system_tokens']))} | "
            f"Chat: {_fmt(int(breakdown['chat_tokens']))}\n"
            f"Used: {_fmt(int(breakdown['used_tokens']))} / {_fmt(int(breakdown['total_tokens']))} tok"
        )
        context_button.setToolTip(tip)
    except Exception:
        logger.exception("Failed to update AI context tooltip")


def refresh_context_toolbar(
    *,
    selection_mode: str,
    selected_snapshot: GraphContextSnapshot | None,
    pinned_snapshot: GraphContextSnapshot | None,
    selected_label: QtWidgets.QLabel,
    pinned_label: QtWidgets.QLabel,
    pin_context_button: QtWidgets.QToolButton,
    clear_context_button: QtWidgets.QToolButton,
) -> None:
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
    set_status_label_text(selected_label, selected_text, max_width=selected_label.maximumWidth())
    selected_label.setToolTip(selected_tooltip)

    if pinned_snapshot is None:
        set_status_label_text(pinned_label, "Pin: none", max_width=pinned_label.maximumWidth())
        pinned_label.setToolTip("No graph context is currently pinned into chat.")
    else:
        set_status_label_text(
            pinned_label,
            f"Pin: {pinned_snapshot.selection_label}",
            max_width=pinned_label.maximumWidth(),
        )
        pinned_label.setToolTip(
            f"Selected nodes: {pinned_snapshot.total_selected_count}\n"
            f"One-hop context nodes: {pinned_snapshot.total_one_hop_count}\n"
            f"Included connections: {pinned_snapshot.total_connection_count}"
        )

    pin_context_button.setEnabled(selected_snapshot is not None)
    clear_context_button.setEnabled(pinned_snapshot is not None)
