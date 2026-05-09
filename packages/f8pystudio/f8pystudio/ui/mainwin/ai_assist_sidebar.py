from __future__ import annotations

import logging
from typing import Any
from qtpy import QtCore, QtGui, QtWidgets

from ...ai_assist.graph_context import GraphContextSnapshot
from ...ai_assist.llm_bridge import AiLlmBridge
from ...ai_assist.store import AiProviderStore
from ...ui.support.ai_assist_state import QtAiPanelStateStore
from ...ui.support.web_asset_utils import render_prism_asset_html, resolve_web_asset_page_base_url
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.studio_theme import ai_context_button_qss, ai_status_label_qss, studio_dark_theme
from ...ui.support.webengine_utils import (
    configure_default_webengine_profile,
    configure_webengine_local_content_access,
    flush_qt_deferred_deletes,
    release_webengine_view,
    set_webengine_html,
    take_prewarmed_webengine_view,
)
from ..support.ai_context_controls import (
    configure_icon_tool_button,
    set_tool_button_point_size,
    usage_pie_icon,
)
from ..widgets.ai_quick_panel import AiQuickPanel
from ..support.ai_assist_page import build_ai_assist_html
from .ai_assist_sidebar_graph_context_mixin import (
    AiAssistSidebarGraphContextMixin,
    GraphSelectionSource,
)
from .ai_assist_sidebar_toolbar_mixin import AiAssistSidebarToolbarMixin

logger = logging.getLogger(__name__)


class AiAssistSidebarWidget(AiAssistSidebarToolbarMixin, AiAssistSidebarGraphContextMixin, QtWidgets.QWidget):
    """
    Standalone AI Assist sidebar widget for the main studio window.
    """

    def __init__(
        self,
        studio_graph: GraphSelectionSource | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._selection_mode = "none"
        self._current_selection_label = ""
        self._current_selected_snapshot_preview: GraphContextSnapshot | None = None
        self._pinned_graph_context_snapshot: GraphContextSnapshot | None = None
        self._shutdown_started = False
        theme_palette = studio_dark_theme().palette
        
        # 1. Setup AI components
        self._ai_store = AiProviderStore()
        self._ai_bridge = AiLlmBridge(self._ai_store, state_store=QtAiPanelStateStore(), parent=self)
        
        # 2. UI Components
        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        configure_default_webengine_profile()
        prewarmed_view = take_prewarmed_webengine_view(parent=self)
        if prewarmed_view is None:
            self._view: Any = QtWebEngineWidgets.QWebEngineView(self)
        else:
            self._view = prewarmed_view
        configure_webengine_local_content_access(self._view)
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        
        self._web_channel = QtWebChannel.QWebChannel(self._view.page())
        self._web_channel.registerObject("aiAssist", self._ai_bridge)
        self._view.page().setWebChannel(self._web_channel)

        # Context usage indicator
        self._ctx_btn = QtWidgets.QToolButton()
        self._ctx_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._ctx_btn.setIconSize(QtCore.QSize(14, 14))
        self._ctx_btn.setIcon(usage_pie_icon(used_ratio=0.0, color=QtGui.QColor(theme_palette.info)))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(ai_context_button_qss(text_color=theme_palette.text_muted, include_background=True))
        self._ctx_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._ctx_btn.customContextMenuRequested.connect(self._on_ctx_menu_requested)
        self._ai_bridge.context_usage_updated.connect(self._on_context_usage_updated)
        self._ai_bridge.chat_context_snapshot_changed.connect(self._on_bridge_chat_context_changed)

        self._selected_node_label = QtWidgets.QLabel("Sel: none")
        self._selected_node_label.setStyleSheet(ai_status_label_qss(text_color=theme_palette.text_muted))
        self._selected_node_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self._selected_node_label.setMaximumWidth(150)
        self._selected_node_label.setToolTip("Current graph selection subgraph preview.")

        self._pinned_node_label = QtWidgets.QLabel("Pin: none")
        self._pinned_node_label.setStyleSheet(ai_status_label_qss(text_color=theme_palette.accent_hover))
        self._pinned_node_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self._pinned_node_label.setMaximumWidth(150)
        self._pinned_node_label.setToolTip("Pinned graph context injected into AI chat.")

        self._pin_context_btn = QtWidgets.QToolButton()
        self._pin_context_btn.setEnabled(False)
        self._pin_context_btn.clicked.connect(self._pin_selected_context)
        configure_icon_tool_button(
            self._pin_context_btn,
            icon=icon_for(self._pin_context_btn, StudioIcon.PLUS),
            tooltip="Use selected subgraph context",
            accent_color=theme_palette.text_primary,
        )

        self._clear_context_btn = QtWidgets.QToolButton()
        self._clear_context_btn.setEnabled(False)
        self._clear_context_btn.clicked.connect(self._clear_pinned_context)
        configure_icon_tool_button(
            self._clear_context_btn,
            icon=icon_for(self._clear_context_btn, StudioIcon.X),
            tooltip="Clear pinned graph context",
            accent_color=theme_palette.error,
        )

        self._inspect_graph_context_btn = QtWidgets.QToolButton()
        self._inspect_graph_context_btn.clicked.connect(self._inspect_graph_context)
        configure_icon_tool_button(
            self._inspect_graph_context_btn,
            icon=icon_for(self._inspect_graph_context_btn, StudioIcon.ARTICLE),
            tooltip="Inspect pinned graph context payload",
            accent_color=theme_palette.success,
        )
        
        # AI settings toggle button
        self._ai_settings_btn = QtWidgets.QToolButton()
        self._ai_settings_btn.setCheckable(True)
        configure_icon_tool_button(
            self._ai_settings_btn,
            icon=icon_for(self._ai_settings_btn, StudioIcon.ROBOT_FACE),
            tooltip="Toggle AI settings",
            accent_color=theme_palette.purple,
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
        self._view.show()
        
        # Load HTML
        set_webengine_html(
            self._view,
            build_ai_assist_html(
                prism_asset_html=render_prism_asset_html(languages=("python",)),
            ),
            base_url=resolve_web_asset_page_base_url(),
        )
        
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

    def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._reposition_timer.stop()
        self._selection_timer.stop()
        self._unwire_graph_signals()
        try:
            self._ai_bridge.abort_all_requests()
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("failed to abort AI requests during sidebar shutdown", exc_info=True)
        view = self._view
        release_webengine_view(view, context="ai-assist-sidebar")
        flush_qt_deferred_deletes()

    def _unwire_graph_signals(self) -> None:
        graph = self._studio_graph
        if graph is None:
            return
        for signal, slot in (
            (graph.node_selected, self._on_graph_selection_signal),
            (graph.node_selection_changed, self._on_graph_selection_changed),
            (graph.nodes_deleted, self._on_graph_nodes_deleted),
            (graph.property_changed, self._on_graph_property_changed),
            (graph.port_connected, self._on_graph_ports_changed),
            (graph.port_disconnected, self._on_graph_ports_changed),
        ):
            try:
                signal.disconnect(slot)  # type: ignore[attr-defined]
            except (TypeError, RuntimeError):
                pass

    def _on_ai_settings_toggle(self, checked: bool) -> None:
        self._ai_quick_panel.setVisible(checked)
        if checked:
            self._ai_quick_panel.raise_()
            self._reposition_overlays()

    def _open_full_ai_config(self) -> None:
        from ..dialogs.ai_provider_config_dialog import AiProviderConfigDialog
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
