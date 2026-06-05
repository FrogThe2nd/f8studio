from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from qtpy import QtCore, QtGui, QtWidgets

from ...agents.codeact import (
    StudioAgentSkillStatus,
    StudioCodeActConfig,
    build_codeact_context_provider,
    codeact_skill_status,
)
from ...agents.graph_context import GraphContextSnapshot
from ...agents.qt_bridge import AiLlmBridge
from ...agents.store import AiProviderStore
from ...agents.tools import LocalStudioGraphToolExecutor, LocalStudioGraphTools
from ...ui.agents import AgentQuickSettingsController, AgentSurfaceScope
from ...ui.support.ai_assist_state import QtAiPanelStateStore
from ...ui.support.web_asset_utils import render_prism_asset_html, resolve_web_asset_page_base_url
from ...ui.support.studio_theme import ai_status_label_qss, studio_dark_theme
from ...ui.support.webengine_utils import (
    configure_default_webengine_profile,
    configure_webengine_local_content_access,
    flush_qt_deferred_deletes,
    release_webengine_view,
    set_webengine_html,
    take_prewarmed_webengine_view,
)
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
        runtime_bridge: Any | None = None,
        log_source: Any | None = None,
        observation_source: Any | None = None,
        property_editor: Any | None = None,
        on_graph_patch_applied: Callable[[], None] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._property_editor = property_editor
        self._graph_tool_executor: LocalStudioGraphToolExecutor | None = None
        self._graph_tools: LocalStudioGraphTools | None = None
        self._graph_tool_count = 0
        self._graph_tool_names: tuple[str, ...] = ()
        self._graph_skill_statuses: tuple[StudioAgentSkillStatus, ...] = ()
        self._selection_mode = "none"
        self._current_selection_label = ""
        self._current_selected_snapshot_preview: GraphContextSnapshot | None = None
        self._shutdown_started = False
        theme_palette = studio_dark_theme().palette
        
        # 1. Setup AI components
        self._ai_store = AiProviderStore()
        self._ai_bridge = AiLlmBridge(self._ai_store, state_store=QtAiPanelStateStore(), parent=self)
        if studio_graph is not None:
            self._graph_tool_executor = LocalStudioGraphToolExecutor(
                studio_graph,
                bridge=runtime_bridge,
                log_source=log_source,
                observation_source=observation_source,
                ui_context_source=self,
                on_graph_patch_applied=on_graph_patch_applied,
                on_tool_trace=self._ai_bridge.publish_tool_trace,
                on_tool_approval_requested=self._ai_bridge.publish_tool_approval,
                parent=self,
            )
            self._ai_bridge.set_tool_approval_resolver(self._graph_tool_executor.resolve_approval)
            self._graph_tools = LocalStudioGraphTools(self._graph_tool_executor)
            graph_tools = self._graph_tools.available_tools()
            self._graph_tool_count = len(graph_tools)
            self._graph_tool_names = self._graph_tools.available_tool_names()
            self._ai_bridge.set_agent_tools(graph_tools)
            codeact_status = codeact_skill_status(StudioCodeActConfig(enabled=True))
            codeact_provider = build_codeact_context_provider(
                tools=self._graph_tools.available_codeact_diagnostic_tools(),
                config=StudioCodeActConfig(enabled=True),
            )
            self._ai_bridge.set_agent_codeact_context_providers(() if codeact_provider is None else (codeact_provider,))
            self._ai_bridge.set_agent_skill_statuses((codeact_status,))
            self._graph_skill_statuses = self._ai_bridge.agent_skill_statuses()
        
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

        self._selected_node_label = QtWidgets.QLabel("Sel: none")
        self._selected_node_label.setStyleSheet(ai_status_label_qss(text_color=theme_palette.text_muted))
        self._selected_node_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self._selected_node_label.setMaximumWidth(150)
        self._selected_node_label.setToolTip("Current graph selection. The agent can query this through graph tools.")

        self._tools_button = QtWidgets.QToolButton()
        self._tools_button.setText("Tools: off")
        self._tools_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tools_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._tools_button.setAutoRaise(False)
        self._tools_button.setStyleSheet(ai_status_label_qss(text_color=theme_palette.success))
        self._tools_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Fixed)
        self._tools_button.setMinimumWidth(82)
        self._tools_button.setToolTip("PyStudio graph tools status.")
        self._tools_menu = QtWidgets.QMenu(self._tools_button)
        self._tools_menu.aboutToShow.connect(self._populate_graph_tools_menu)  # type: ignore[attr-defined]
        self._tools_button.setMenu(self._tools_menu)

        self._skills_button = QtWidgets.QToolButton()
        self._skills_button.setText("Skills: off")
        self._skills_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._skills_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._skills_button.setAutoRaise(False)
        self._skills_button.setStyleSheet(ai_status_label_qss(text_color=theme_palette.accent_hover))
        self._skills_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Fixed)
        self._skills_button.setMinimumWidth(82)
        self._skills_button.setToolTip("PyStudio agent skills status.")
        self._skills_menu = QtWidgets.QMenu(self._skills_button)
        self._skills_menu.aboutToShow.connect(self._populate_graph_skills_menu)  # type: ignore[attr-defined]
        self._skills_button.setMenu(self._skills_menu)

        self._agent_settings = AgentQuickSettingsController(
            store=self._ai_store,
            bridge=self._ai_bridge,
            host=self,
            panel_parent=self,
            scope=AgentSurfaceScope.GRAPH,
        )
        self._agent_settings.button.toggled.connect(self._on_ai_settings_toggle)  # type: ignore[attr-defined]
        self._ai_settings_btn = self._agent_settings.button
        
        # Toolbar Container
        self._toolbar_container = QtWidgets.QWidget()
        toolbar_layout = QtWidgets.QHBoxLayout(self._toolbar_container)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(4)
        toolbar_layout.addWidget(self._selected_node_label, 1)
        toolbar_layout.addWidget(self._tools_button, 0)
        toolbar_layout.addWidget(self._skills_button, 0)
        toolbar_layout.addWidget(self._ai_settings_btn, 0)

        self._ai_quick_panel = self._agent_settings.panel
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
        if checked:
            self._reposition_overlays()

    def _open_full_ai_config(self) -> None:
        from ..dialogs.ai_provider_config_dialog import AiProviderConfigDialog
        dlg = AiProviderConfigDialog(self._ai_store, self)
        dlg.exec()

    def graph_ui_context(self) -> dict[str, Any]:
        property_panel_node_id = ""
        editor = self._property_editor
        if editor is not None:
            try:
                property_panel_node_id = str(editor.current_node_id() or "")
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.exception("failed to read AI assist property panel node id")
        selected_snapshot = self._current_selected_snapshot_preview
        selected_node_ids = list(selected_snapshot.selected_node_ids) if selected_snapshot is not None else []
        primary_node_id = property_panel_node_id
        primary_source = "propertyPanel" if property_panel_node_id else "none"
        if not primary_node_id and len(selected_node_ids) == 1:
            primary_node_id = str(selected_node_ids[0])
            primary_source = "singleSelection"
        return {
            "graphRevision": _graph_revision(self._studio_graph),
            "selectedNodeIds": selected_node_ids,
            "selectionLabel": "" if selected_snapshot is None else selected_snapshot.selection_label,
            "selectionCount": len(selected_node_ids),
            "propertyPanelNodeId": property_panel_node_id,
            "primaryNodeId": primary_node_id,
            "primaryNodeSource": primary_source,
        }

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition_timer.start()

    def _reposition_overlays(self) -> None:
        self._agent_settings.reposition_below(self._toolbar_container)
        if self._ai_quick_panel.isVisible():
            self._ai_quick_panel.raise_()


def _graph_revision(graph: object | None) -> int | None:
    if graph is None:
        return None
    try:
        undo_stack = graph._undo_stack
        return int(undo_stack.index())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
