from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from qtpy import QtCore, QtGui, QtWidgets

from f8pystudio.agents.qt_bridge import AiLlmBridge
from f8pystudio.agents.store import AiProviderStore
from f8pystudio.ui.support.ai_context_controls import set_tool_button_point_size, usage_pie_icon
from f8pystudio.ui.support.studio_theme import ai_context_button_qss, studio_dark_theme
from f8pystudio.ui.support.ui_icons import StudioIcon, icon_for
from f8pystudio.ui.widgets.ai_quick_panel import AiQuickPanel

logger = logging.getLogger(__name__)


class AgentSurfaceScope(Enum):
    GRAPH = "graph"
    EDITOR = "editor"
    NODE = "node"


class AgentContextBreakdownBridge(Protocol):
    context_usage_updated: QtCore.Signal

    def get_context_breakdown(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class AgentContextUsageStyle:
    include_background: bool
    compact_tooltip: bool


_CONTEXT_STYLE_BY_SCOPE: dict[AgentSurfaceScope, AgentContextUsageStyle] = {
    AgentSurfaceScope.GRAPH: AgentContextUsageStyle(include_background=True, compact_tooltip=True),
    AgentSurfaceScope.EDITOR: AgentContextUsageStyle(include_background=False, compact_tooltip=False),
    AgentSurfaceScope.NODE: AgentContextUsageStyle(include_background=False, compact_tooltip=False),
}


class AgentContextUsageButton(QtWidgets.QToolButton):
    def __init__(
        self,
        bridge: AgentContextBreakdownBridge,
        *,
        scope: AgentSurfaceScope,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._scope = scope
        self._style = _CONTEXT_STYLE_BY_SCOPE[scope]
        p = studio_dark_theme().palette

        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QtCore.QSize(14, 14))
        self.setIcon(usage_pie_icon(used_ratio=0.0, color=QtGui.QColor(p.info)))
        self.setText("100% free")
        self.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        set_tool_button_point_size(self, 10)
        self.setStyleSheet(ai_context_button_qss(text_color=p.text_muted, include_background=self._style.include_background))
        bridge.context_usage_updated.connect(self.update_usage)  # type: ignore[attr-defined]

    @QtCore.Slot(int, int)
    def update_usage(self, used: int, total: int) -> None:
        if total <= 0:
            return
        used_ratio = max(0.0, min(1.0, float(used) / float(total)))
        free_pct = int(round(max(0.0, 1.0 - used_ratio) * 100.0))
        color = _context_usage_color(used_ratio)
        self.setIcon(usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
        self.setText(f"{free_pct}% free")
        set_tool_button_point_size(self, 10)
        self.setStyleSheet(ai_context_button_qss(text_color=color, include_background=self._style.include_background))
        self.setToolTip(self._tooltip_text(free_pct=free_pct))

    def _tooltip_text(self, *, free_pct: int) -> str:
        try:
            breakdown = self._bridge.get_context_breakdown()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.exception("Failed to read AI context breakdown")
            return f"AI context usage\nFree: {int(free_pct)}%"

        system_tokens = _int_breakdown_value(breakdown, "system_tokens")
        code_tokens = _int_breakdown_value(breakdown, "code_tokens")
        chat_tokens = _int_breakdown_value(breakdown, "chat_tokens")
        used_tokens = _int_breakdown_value(breakdown, "used_tokens")
        total_tokens = _int_breakdown_value(breakdown, "total_tokens")
        if self._style.compact_tooltip:
            return (
                f"AI Context Usage: {int(free_pct)}% free\n"
                f"System: {_format_tokens(system_tokens)} | Chat: {_format_tokens(chat_tokens)}\n"
                f"Used: {_format_tokens(used_tokens)} / {_format_tokens(total_tokens)} tok"
            )
        return (
            "AI Context Usage\n"
            f"System: {_format_tokens(system_tokens)} tok\n"
            f"Code: {_format_tokens(code_tokens)} tok\n"
            f"Chat: {_format_tokens(chat_tokens)} tok\n"
            f"Free: {int(free_pct)}%\n"
            f"Used: {_format_tokens(used_tokens)} / {_format_tokens(total_tokens)} tok"
        )

class AgentQuickSettingsController(QtCore.QObject):
    def __init__(
        self,
        *,
        store: AiProviderStore,
        bridge: AiLlmBridge,
        host: QtWidgets.QWidget,
        panel_parent: QtWidgets.QWidget,
        scope: AgentSurfaceScope,
    ) -> None:
        super().__init__(host)
        self._host = host
        self._scope = scope
        self.button = QtWidgets.QToolButton(host)
        self.button.setCheckable(True)
        self.button.setIconSize(QtCore.QSize(16, 16))
        self.button.setIcon(icon_for(self.button, StudioIcon.ROBOT_FACE))
        self.button.setToolTip("Toggle AI settings")
        set_tool_button_point_size(self.button, 16 if scope == AgentSurfaceScope.EDITOR else 10)
        self.button.setStyleSheet(_agent_settings_button_qss(include_border=scope != AgentSurfaceScope.EDITOR))
        self.button.toggled.connect(self._on_toggled)  # type: ignore[attr-defined]

        self.panel = AiQuickPanel(store, bridge, panel_parent)
        self.panel.setVisible(False)
        self.panel.raise_()

    def reposition_below(self, anchor: QtWidgets.QWidget, *, right_margin: int = 8) -> None:
        if not self.panel.isVisible():
            return
        self.panel.adjustSize()
        px = self._host.width() - self.panel.width() - int(right_margin)
        py = anchor.height()
        self.panel.move(max(0, px), max(0, py))

    def reposition_inside(self, anchor: QtWidgets.QWidget, *, margin: int = 10) -> None:
        if not self.panel.isVisible():
            return
        rect = anchor.geometry()
        if rect.width() <= 0:
            return
        self.panel.adjustSize()
        x = rect.x() + int(margin)
        y = rect.y() + rect.height() - self.panel.height() - int(margin)
        self.panel.move(max(0, x), max(0, y))

    @QtCore.Slot(bool)
    def _on_toggled(self, checked: bool) -> None:
        self.panel.setVisible(bool(checked))
        if bool(checked):
            self.panel.raise_()


def _agent_settings_button_qss(*, include_border: bool) -> str:
    p = studio_dark_theme().palette
    border = f"1px solid {p.border_subtle}" if include_border else "none"
    return (
        f"QToolButton {{ border: {border}; padding: 0 4px; border-radius: 4px; }}"
        f"QToolButton:hover:enabled {{ background: {p.button_hover_bg}; }}"
        f"QToolButton:checked {{ background: {p.button_hover_bg}; border-color: {p.border_focus}; }}"
    )


def _context_usage_color(used_ratio: float) -> str:
    p = studio_dark_theme().palette
    if used_ratio < 0.5:
        return p.info
    if used_ratio < 0.8:
        return p.warning
    return p.error


def _format_tokens(value: int) -> str:
    return f"{float(value) / 1000.0:.0f}k" if int(value) >= 1000 else str(int(value))


def _int_breakdown_value(breakdown: dict[str, object], key: str) -> int:
    value = breakdown.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(float(stripped))
            except ValueError:
                return 0
    return 0
