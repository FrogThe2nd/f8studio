from __future__ import annotations

import logging
from qtpy import QtCore, QtGui, QtWidgets

from ..ai_assist.graph_context import GraphContextSnapshot, build_graph_context_snapshot
from ..ai_assist.llm_bridge import AiLlmBridge
from ..ai_assist.store import AiProviderStore
from ..nodegraph import F8StudioGraph
from ..qt_font_utils import normalize_font_point_size
from ..ui_icons import StudioIcon, icon_for
from .ai_context_inspector import AiContextInspectorDialog
from .ai_quick_panel import AiQuickPanel
from .ai_assist_page import build_ai_assist_html

def _usage_pie_icon(*, used_ratio: float, color: QtGui.QColor, size: int = 14) -> QtGui.QIcon:
    ratio = max(0.0, min(1.0, float(used_ratio)))
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

    outer = QtCore.QRectF(1.0, 1.0, float(size - 2), float(size - 2))
    center = QtCore.QPointF(outer.center())

    base = QtGui.QColor("#4a4f57")
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(base)
    painter.drawEllipse(outer)

    if ratio > 0.0:
        painter.setBrush(color)
        start_angle = 90 * 16
        span_angle = int(-360 * 16 * ratio)
        painter.drawPie(outer, start_angle, span_angle)

    inner_diameter = max(2.0, outer.width() * 0.46)
    inner = QtCore.QRectF(
        center.x() - inner_diameter / 2.0,
        center.y() - inner_diameter / 2.0,
        inner_diameter,
        inner_diameter,
    )
    painter.setBrush(QtGui.QColor("#1f2328"))
    painter.drawEllipse(inner)

    painter.setPen(QtGui.QPen(QtGui.QColor("#6c7380"), 1.0))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(outer)
    painter.end()
    return QtGui.QIcon(pix)

def _set_tool_button_point_size(button: QtWidgets.QToolButton, point_size: int) -> None:
    font = normalize_font_point_size(button.font(), fallback_point_size=point_size)
    font.setPointSize(max(1, int(point_size)))
    button.setFont(font)


def _configure_icon_tool_button(
    button: QtWidgets.QToolButton,
    *,
    icon: QtGui.QIcon,
    tooltip: str,
    accent_color: str,
) -> None:
    button.setIcon(icon)
    button.setIconSize(QtCore.QSize(14, 14))
    button.setAutoRaise(True)
    button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(24, 24)
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton {"
        f" color: {accent_color};"
        " border: none;"
        " border-radius: 6px;"
        " padding: 0;"
        " background: transparent;"
        "}"
        "QToolButton:hover:enabled { background: #313244; }"
        "QToolButton:pressed:enabled { background: #45475a; }"
        "QToolButton:checked { background: #313244; }"
        "QToolButton:disabled { color: #6c7086; }"
    )


def _set_status_label_text(label: QtWidgets.QLabel, text: str, *, max_width: int) -> None:
    metrics = label.fontMetrics()
    elided = metrics.elidedText(str(text or ""), QtCore.Qt.TextElideMode.ElideRight, max_width)
    label.setText(elided)
    label.setToolTip(str(text or ""))

logger = logging.getLogger(__name__)

class AiAssistSidebarWidget(QtWidgets.QWidget):
    """
    Standalone AI Assist sidebar widget for the main studio window.
    """

    def __init__(
        self,
        studio_graph: F8StudioGraph | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._selection_mode = "none"
        self._current_selection_label = ""
        self._current_selected_snapshot_preview: GraphContextSnapshot | None = None
        self._pinned_graph_context_snapshot: GraphContextSnapshot | None = None
        
        # 1. Setup AI components
        self._ai_store = AiProviderStore()
        self._ai_bridge = AiLlmBridge(self._ai_store, self)
        
        # 2. UI Components
        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]
        
        self._view = QtWebEngineWidgets.QWebEngineView(self)
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        
        self._web_channel = QtWebChannel.QWebChannel(self._view.page())
        self._web_channel.registerObject("aiAssist", self._ai_bridge)
        self._view.page().setWebChannel(self._web_channel)

        # Context usage indicator
        self._ctx_btn = QtWidgets.QToolButton()
        self._ctx_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._ctx_btn.setIconSize(QtCore.QSize(14, 14))
        self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=0.0, color=QtGui.QColor("#4fc3f7")))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        _set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(
            "QToolButton { color: #9aa4b2; border: none; padding: 0 4px; background: transparent; font-size: 10pt; }"
            "QToolButton:hover { color: #d7deea; }"
        )
        self._ctx_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._ctx_btn.customContextMenuRequested.connect(self._on_ctx_menu_requested)
        self._ai_bridge.context_usage_updated.connect(self._on_context_usage_updated)
        self._ai_bridge.chat_context_snapshot_changed.connect(self._on_bridge_chat_context_changed)

        self._selected_node_label = QtWidgets.QLabel("Sel: none")
        self._selected_node_label.setStyleSheet(
            "QLabel { color: #7f849c; font-size: 9pt; background: #181825; border: 1px solid #313244; border-radius: 5px; padding: 1px 6px; }"
        )
        self._selected_node_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self._selected_node_label.setMaximumWidth(150)
        self._selected_node_label.setToolTip("Current graph selection subgraph preview.")

        self._pinned_node_label = QtWidgets.QLabel("Pin: none")
        self._pinned_node_label.setStyleSheet(
            "QLabel { color: #89b4fa; font-size: 9pt; background: #181825; border: 1px solid #313244; border-radius: 5px; padding: 1px 6px; }"
        )
        self._pinned_node_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self._pinned_node_label.setMaximumWidth(150)
        self._pinned_node_label.setToolTip("Pinned graph context injected into AI chat.")

        self._pin_context_btn = QtWidgets.QToolButton()
        self._pin_context_btn.setEnabled(False)
        self._pin_context_btn.clicked.connect(self._pin_selected_context)
        _configure_icon_tool_button(
            self._pin_context_btn,
            icon=icon_for(self._pin_context_btn, StudioIcon.PLUS),
            tooltip="Use selected subgraph context",
            accent_color="#cdd6f4",
        )

        self._clear_context_btn = QtWidgets.QToolButton()
        self._clear_context_btn.setEnabled(False)
        self._clear_context_btn.clicked.connect(self._clear_pinned_context)
        _configure_icon_tool_button(
            self._clear_context_btn,
            icon=icon_for(self._clear_context_btn, StudioIcon.X),
            tooltip="Clear pinned graph context",
            accent_color="#f2cdcd",
        )

        self._inspect_graph_context_btn = QtWidgets.QToolButton()
        self._inspect_graph_context_btn.clicked.connect(self._inspect_graph_context)
        _configure_icon_tool_button(
            self._inspect_graph_context_btn,
            icon=icon_for(self._inspect_graph_context_btn, StudioIcon.ARTICLE),
            tooltip="Inspect pinned graph context payload",
            accent_color="#a6e3a1",
        )
        
        # AI settings toggle button
        self._ai_settings_btn = QtWidgets.QToolButton()
        self._ai_settings_btn.setCheckable(True)
        _configure_icon_tool_button(
            self._ai_settings_btn,
            icon=icon_for(self._ai_settings_btn, StudioIcon.ROBOT_FACE),
            tooltip="Toggle AI settings",
            accent_color="#cba6f7",
        )
        self._ai_settings_btn.toggled.connect(self._on_ai_settings_toggle)
        
        # Toolbar Container
        self._toolbar_container = QtWidgets.QWidget()
        toolbar_layout = QtWidgets.QVBoxLayout(self._toolbar_container)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(4)

        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(4)
        toolbar_row.addWidget(self._ctx_btn)
        toolbar_row.addStretch()
        toolbar_row.addWidget(self._pin_context_btn)
        toolbar_row.addWidget(self._clear_context_btn)
        toolbar_row.addWidget(self._inspect_graph_context_btn)
        toolbar_row.addWidget(self._ai_settings_btn)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        status_row.addWidget(self._selected_node_label, 1)
        status_row.addWidget(self._pinned_node_label, 1)

        toolbar_layout.addLayout(toolbar_row)
        toolbar_layout.addLayout(status_row)

        # AI Quick Panel (floating overlay)
        self._ai_quick_panel = AiQuickPanel(self._ai_store, self._ai_bridge, self)
        self._ai_quick_panel.setVisible(False)
        self._ai_quick_panel.open_full_config_requested.connect(self._open_full_ai_config)
        
        # Layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar_container, 0)
        layout.addWidget(self._view, 1)
        
        # Load HTML
        self._view.setHtml(build_ai_assist_html())
        
        # Timer to reposition buttons/panels since they are overlays
        self._reposition_timer = QtCore.QTimer(self)
        self._reposition_timer.setSingleShot(True)
        self._reposition_timer.setInterval(50)
        self._reposition_timer.timeout.connect(self._reposition_overlays)
        self._selection_timer = QtCore.QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(0)
        self._selection_timer.timeout.connect(self._apply_graph_selection)
        self._wire_graph_signals()
        self._refresh_context_toolbar()

    @QtCore.Slot(int, int)
    def _on_context_usage_updated(self, used: int, total: int) -> None:
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
        self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
        self._ctx_btn.setText(f"{free_pct}% free")
        # Explicitly set font-size in QSS to avoid Qt querying the internal system font which might return -1
        self._ctx_btn.setStyleSheet(
            f"QToolButton {{ color: {color}; border: none; padding: 0 4px; background: transparent; font-size: 10pt; }}"
            "QToolButton:hover { color: white; }"
        )
        try:
            breakdown = self._ai_bridge.get_context_breakdown()
            # Use simple plain text for tooltip to avoid rich-text rendering path triggers setPointSize warnings
            tip = (
                f"AI Context Usage: {free_pct}% free\n"
                f"System: {_fmt(int(breakdown['system_tokens']))} | "
                f"Chat: {_fmt(int(breakdown['chat_tokens']))}\n"
                f"Used: {_fmt(int(breakdown['used_tokens']))} / {_fmt(int(breakdown['total_tokens']))} tok"
            )
            self._ctx_btn.setToolTip(tip)
        except Exception:
            logger.exception("Failed to update AI context tooltip")

    def _on_ctx_menu_requested(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        inspect_act = menu.addAction("Inspect Current Context Payload...")
        inspect_act.triggered.connect(self._inspect_context)
        inspect_graph_act = menu.addAction("Inspect Graph Context Payload...")
        inspect_graph_act.triggered.connect(self._inspect_graph_context)
        menu.exec(self._ctx_btn.mapToGlobal(pos))

    def _inspect_context(self) -> None:
        report = self._ai_bridge.get_context_report()
        dlg = AiContextInspectorDialog(report, self)
        dlg.exec()

    def _inspect_graph_context(self) -> None:
        report = self._ai_bridge.get_chat_context_report()
        dlg = AiContextInspectorDialog(report, self)
        dlg.exec()

    def _on_ai_settings_toggle(self, checked: bool) -> None:
        self._ai_quick_panel.setVisible(checked)
        if checked:
            self._ai_quick_panel.raise_()
            self._reposition_overlays()

    def _open_full_ai_config(self) -> None:
        from .ai_provider_config_dialog import AiProviderConfigDialog
        dlg = AiProviderConfigDialog(self._ai_store, self)
        dlg.exec()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition_timer.start()

    def _reposition_overlays(self) -> None:
        # Position quick panel below the toolbar area
        if not self._ai_quick_panel.isVisible():
            return
            
        self._ai_quick_panel.adjustSize()
        # Horizontal: aligned to the right with 8px margin
        px = self.width() - self._ai_quick_panel.width() - 8
        # Vertical: right below the toolbar container
        py = self._toolbar_container.height()
        
        # Clamp px to stay visible
        px = max(8, px)
        
        self._ai_quick_panel.move(px, py)
        self._ai_quick_panel.raise_()

    def _wire_graph_signals(self) -> None:
        graph = self._studio_graph
        if graph is None:
            return
        graph.node_selected.connect(self._on_graph_selection_signal)  # type: ignore[attr-defined]
        graph.node_selection_changed.connect(self._on_graph_selection_changed)  # type: ignore[attr-defined]
        graph.nodes_deleted.connect(self._on_graph_nodes_deleted)  # type: ignore[attr-defined]
        graph.property_changed.connect(self._on_graph_property_changed)  # type: ignore[attr-defined]
        graph.port_connected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]
        graph.port_disconnected.connect(self._on_graph_ports_changed)  # type: ignore[attr-defined]
        self._selection_timer.start()

    def _refresh_context_toolbar(self) -> None:
        snapshot = self._current_selected_snapshot_preview
        if self._selection_mode == "active" and snapshot is not None:
            selected_text = f"Sel: {snapshot.selection_label}"
            selected_tooltip = (
                f"Selected nodes: {snapshot.total_selected_count}\n"
                f"One-hop context nodes: {snapshot.total_one_hop_count}\n"
                f"Included connections: {snapshot.total_connection_count}"
            )
        else:
            selected_text = "Sel: none"
            selected_tooltip = "Select one or more nodes to preview graph subgraph context."
        _set_status_label_text(self._selected_node_label, selected_text, max_width=self._selected_node_label.maximumWidth())
        self._selected_node_label.setToolTip(selected_tooltip)

        pinned = self._pinned_graph_context_snapshot
        if pinned is None:
            _set_status_label_text(self._pinned_node_label, "Pin: none", max_width=self._pinned_node_label.maximumWidth())
            self._pinned_node_label.setToolTip("No graph context is currently pinned into chat.")
        else:
            _set_status_label_text(
                self._pinned_node_label,
                f"Pin: {pinned.selection_label}",
                max_width=self._pinned_node_label.maximumWidth(),
            )
            self._pinned_node_label.setToolTip(
                f"Selected nodes: {pinned.total_selected_count}\n"
                f"One-hop context nodes: {pinned.total_one_hop_count}\n"
                f"Included connections: {pinned.total_connection_count}"
            )

        self._pin_context_btn.setEnabled(self._current_selected_snapshot_preview is not None)
        self._clear_context_btn.setEnabled(pinned is not None)

    def _pin_selected_context(self) -> None:
        snapshot = self._current_selected_snapshot_preview
        if snapshot is None:
            return
        self._pinned_graph_context_snapshot = snapshot
        self._ai_bridge.set_chat_context_snapshot(snapshot)
        self._refresh_context_toolbar()

    def _clear_pinned_context(self) -> None:
        self._pinned_graph_context_snapshot = None
        self._ai_bridge.clear_chat_context_snapshot()
        self._refresh_context_toolbar()

    def _set_current_selection_snapshot(self, snapshot: GraphContextSnapshot | None, *, mode: str) -> None:
        self._selection_mode = mode
        if snapshot is None:
            self._current_selection_label = ""
            self._current_selected_snapshot_preview = None
        else:
            self._current_selected_snapshot_preview = snapshot
            self._current_selection_label = snapshot.selection_label
        self._refresh_context_toolbar()

    @QtCore.Slot(object)
    def _on_graph_selection_signal(self, _node: object) -> None:
        self._selection_timer.start()

    @QtCore.Slot(list, list)
    def _on_graph_selection_changed(self, _selected: list[object], _deselected: list[object]) -> None:
        self._selection_timer.start()

    @QtCore.Slot(list)
    def _on_graph_nodes_deleted(self, _node_ids: list[str]) -> None:
        self._selection_timer.start()

    @QtCore.Slot(object, str, object)
    def _on_graph_property_changed(self, _node: object, _name: str, _value: object) -> None:
        if self._selection_mode != "none":
            self._selection_timer.start()

    @QtCore.Slot(object, object)
    def _on_graph_ports_changed(self, _port_a: object, _port_b: object) -> None:
        if self._selection_mode != "none":
            self._selection_timer.start()

    def _apply_graph_selection(self) -> None:
        graph = self._studio_graph
        if graph is None:
            self._set_current_selection_snapshot(None, mode="none")
            return
        try:
            selected_nodes = list(graph.selected_nodes() or [])  # type: ignore[attr-defined]
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
        if not has_context:
            self._pinned_graph_context_snapshot = None
        self._refresh_context_toolbar()
