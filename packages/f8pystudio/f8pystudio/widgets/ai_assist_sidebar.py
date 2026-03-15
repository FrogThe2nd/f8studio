from __future__ import annotations

import logging
from qtpy import QtCore, QtGui, QtWidgets

from ..ai_assist.llm_bridge import AiLlmBridge
from ..ai_assist.store import AiProviderStore
from ..qt_font_utils import normalize_font_point_size
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

logger = logging.getLogger(__name__)

class AiAssistSidebarWidget(QtWidgets.QWidget):
    """
    Standalone AI Assist sidebar widget for the main studio window.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        
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
        
        # AI settings toggle button
        self._ai_settings_btn = QtWidgets.QToolButton()
        self._ai_settings_btn.setText("🤖")
        self._ai_settings_btn.setCheckable(True)
        self._ai_settings_btn.setToolTip("Toggle AI Settings")
        _set_tool_button_point_size(self._ai_settings_btn, 16)
        self._ai_settings_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; color: #cba6f7; border-radius: 4px; font-size: 16px; padding: 0 4px; }"
            "QToolButton:hover { background: #45475a; }"
            "QToolButton:checked { background: #313244; color: #cba6f7; border-radius: 4px; }"
        )
        self._ai_settings_btn.toggled.connect(self._on_ai_settings_toggle)
        
        # Toolbar Container
        self._toolbar_container = QtWidgets.QWidget()
        toolbar_row = QtWidgets.QHBoxLayout(self._toolbar_container)
        toolbar_row.setContentsMargins(8, 4, 8, 4)
        toolbar_row.addWidget(self._ctx_btn)
        toolbar_row.addStretch()
        toolbar_row.addWidget(self._ai_settings_btn)

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
        menu.exec(self._ctx_btn.mapToGlobal(pos))

    def _inspect_context(self) -> None:
        report = self._ai_bridge.get_context_report()
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
