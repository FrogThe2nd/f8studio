from __future__ import annotations

import logging
from qtpy import QtCore, QtGui, QtWidgets

from ...ai_assist.graph_context import GraphContextSnapshot, build_graph_context_snapshot
from ...ai_assist.llm_bridge import AiLlmBridge
from ...ai_assist.store import AiProviderStore
from ...ui.support.ai_assist_state import QtAiPanelStateStore
from ...ui.support.web_asset_utils import render_prism_asset_html, resolve_web_asset_page_base_url
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.webengine_utils import (
    configure_default_webengine_profile,
    configure_webengine_local_content_access,
    set_webengine_html,
    take_prewarmed_webengine_view,
)
from ..dialogs.ai_context_inspector import AiContextInspectorDialog
from ..support.ai_context_controls import (
    configure_icon_tool_button,
    set_status_label_text,
    set_tool_button_point_size,
    usage_pie_icon,
)
from ..widgets.ai_quick_panel import AiQuickPanel
from ..support.ai_assist_page_v2 import build_ai_assist_html_v2  # Use enhanced version
from .ai_assist_sidebar_graph_context import (
    GraphSelectionSource,
    apply_graph_selection as apply_sidebar_graph_selection,
    schedule_selection_refresh as schedule_sidebar_selection_refresh,
    wire_graph_signals as wire_sidebar_graph_signals,
)
from .ai_assist_sidebar_toolbar import (
    refresh_context_toolbar as refresh_sidebar_context_toolbar,
    update_context_usage as update_sidebar_context_usage,
)

logger = logging.getLogger(__name__)

class AiAssistSidebarWidget(QtWidgets.QWidget):
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
        
        # 1. Setup AI components
        self._ai_store = AiProviderStore()
        self._ai_bridge = AiLlmBridge(self._ai_store, state_store=QtAiPanelStateStore(), parent=self)
        
        # 2. UI Components
        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        configure_default_webengine_profile()
        prewarmed_view = take_prewarmed_webengine_view(parent=self)
        if prewarmed_view is None:
            self._view = QtWebEngineWidgets.QWebEngineView(self)
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
        self._ctx_btn.setIcon(usage_pie_icon(used_ratio=0.0, color=QtGui.QColor("#4fc3f7")))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        set_tool_button_point_size(self._ctx_btn, 10)
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
        configure_icon_tool_button(
            self._pin_context_btn,
            icon=icon_for(self._pin_context_btn, StudioIcon.PLUS),
            tooltip="Use selected subgraph context",
            accent_color="#cdd6f4",
        )

        self._clear_context_btn = QtWidgets.QToolButton()
        self._clear_context_btn.setEnabled(False)
        self._clear_context_btn.clicked.connect(self._clear_pinned_context)
        configure_icon_tool_button(
            self._clear_context_btn,
            icon=icon_for(self._clear_context_btn, StudioIcon.X),
            tooltip="Clear pinned graph context",
            accent_color="#f2cdcd",
        )

        self._inspect_graph_context_btn = QtWidgets.QToolButton()
        self._inspect_graph_context_btn.clicked.connect(self._inspect_graph_context)
        configure_icon_tool_button(
            self._inspect_graph_context_btn,
            icon=icon_for(self._inspect_graph_context_btn, StudioIcon.ARTICLE),
            tooltip="Inspect pinned graph context payload",
            accent_color="#a6e3a1",
        )
        
        # AI settings toggle button
        self._ai_settings_btn = QtWidgets.QToolButton()
        self._ai_settings_btn.setCheckable(True)
        configure_icon_tool_button(
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
        self._view.show()
        
        # Load HTML
        set_webengine_html(
            self._view,
            build_ai_assist_html_v2(
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

    @QtCore.Slot(int, int)
    def _on_context_usage_updated(self, used: int, total: int) -> None:
        update_sidebar_context_usage(
            context_button=self._ctx_btn,
            used=used,
            total=total,
            get_context_breakdown=self._ai_bridge.get_context_breakdown,
        )

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

    def _wire_graph_signals(self) -> None:
        wired = wire_sidebar_graph_signals(
            graph=self._studio_graph,
            on_graph_selection_signal=self._on_graph_selection_signal,
            on_graph_selection_changed=self._on_graph_selection_changed,
            on_graph_nodes_deleted=self._on_graph_nodes_deleted,
            on_graph_property_changed=self._on_graph_property_changed,
            on_graph_ports_changed=self._on_graph_ports_changed,
        )
        if wired:
            self._schedule_selection_refresh()

    def _schedule_selection_refresh(self) -> None:
        schedule_sidebar_selection_refresh(
            widget=self,
            selection_timer=self._selection_timer,
            apply_graph_selection=self._apply_graph_selection,
        )

    def _refresh_context_toolbar(self) -> None:
        refresh_sidebar_context_toolbar(
            selection_mode=self._selection_mode,
            selected_snapshot=self._current_selected_snapshot_preview,
            pinned_snapshot=self._pinned_graph_context_snapshot,
            selected_label=self._selected_node_label,
            pinned_label=self._pinned_node_label,
            pin_context_button=self._pin_context_btn,
            clear_context_button=self._clear_context_btn,
        )

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
        self._schedule_selection_refresh()

    @QtCore.Slot(list, list)
    def _on_graph_selection_changed(self, _selected: list[object], _deselected: list[object]) -> None:
        self._schedule_selection_refresh()

    @QtCore.Slot(list)
    def _on_graph_nodes_deleted(self, _node_ids: list[str]) -> None:
        self._schedule_selection_refresh()

    @QtCore.Slot(object, str, object)
    def _on_graph_property_changed(self, _node: object, _name: str, _value: object) -> None:
        if self._selection_mode != "none":
            self._schedule_selection_refresh()

    @QtCore.Slot(object, object)
    def _on_graph_ports_changed(self, _port_a: object, _port_b: object) -> None:
        if self._selection_mode != "none":
            self._schedule_selection_refresh()

    def _apply_graph_selection(self) -> None:
        apply_sidebar_graph_selection(
            graph=self._studio_graph,
            set_current_selection_snapshot=self._set_current_selection_snapshot,
        )

    @QtCore.Slot(bool, str)
    def _on_bridge_chat_context_changed(self, has_context: bool, _node_name: str) -> None:
        if not has_context:
            self._pinned_graph_context_snapshot = None
        self._refresh_context_toolbar()
