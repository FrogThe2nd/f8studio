from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence, TypeAlias

from qtpy import QtCore, QtGui, QtWidgets

from ...assets.subscriptions import SubscriptionSyncService
from ...assets.ui.asset_cloud_account_menu import build_asset_account_menu
from ...assets.variants.variant_sync import VariantSyncClient
from ...ui.support.ui_notifications import show_info, show_warning
from ...nodegraph.edge_rules import EDGE_KIND_DATA, EDGE_KIND_EXEC, EDGE_KIND_STATE
from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from ...ui.support.studio_theme import label_qss, studio_dark_theme
from ...ui.support.ui_icons import StudioIcon, icon_for
from ..support.service_inventory import collect_declared_services
from ..widgets.layers_panel import LayersPanelWidget
from ..widgets.node_property_panel import F8StudioSingleNodePropertiesWidget
from ..widgets.service_log_widget import ServiceLogDock
from .ai_assist_sidebar import AiAssistSidebarWidget
from .node_library_widget import F8StudioNodeLibraryWidget
from .service_manager_widget import ServiceManagerWidget
from f8pystudio.studio_specs.registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS

if TYPE_CHECKING:
    from ...nodegraph.node_graph import F8StudioGraph
    from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge

logger = logging.getLogger(__name__)

ActionHandler: TypeAlias = Callable[[], None] | Callable[[bool], None]


@dataclass(frozen=True)
class MainWindowActionBundle:
    quickload_project_action: QtGui.QAction
    quicksave_project_action: QtGui.QAction
    open_project_action: QtGui.QAction
    import_project_json_action: QtGui.QAction
    import_graph_action: QtGui.QAction
    save_project_as_action: QtGui.QAction
    export_project_json_action: QtGui.QAction
    project_history_action: QtGui.QAction
    save_component_action: QtGui.QAction
    manage_components_action: QtGui.QAction
    auto_save_action: QtGui.QAction
    auto_deploy_action: QtGui.QAction
    performance_overlay_action: QtGui.QAction
    auto_proxy_action: QtGui.QAction
    export_published_session_action: QtGui.QAction
    clear_all_nodes_action: QtGui.QAction
    deploy_action: QtGui.QAction
    stop_all_services_action: QtGui.QAction
    global_hotkeys_action: QtGui.QAction
    variant_catalog_action: QtGui.QAction


@dataclass(frozen=True)
class MainWindowDockBundle:
    properties_dock: QtWidgets.QDockWidget
    log_dock: QtWidgets.QDockWidget
    node_library_dock: QtWidgets.QDockWidget
    layers_dock: QtWidgets.QDockWidget
    ai_assist_dock: QtWidgets.QDockWidget

    @property
    def all_docks(self) -> list[QtWidgets.QDockWidget]:
        return [
            self.properties_dock,
            self.log_dock,
            self.node_library_dock,
            self.layers_dock,
            self.ai_assist_dock,
        ]


@dataclass(frozen=True)
class MainWindowViewMenuBundle:
    view_menu: QtWidgets.QMenu
    reset_layout_action: QtGui.QAction


@dataclass(frozen=True)
class MainWindowLogMenuBundle:
    log_level_menu: QtWidgets.QMenu
    log_level_action_group: QtGui.QActionGroup
    log_level_actions: dict[int, QtGui.QAction]


@dataclass(frozen=True)
class MainWindowToolbarBundle:
    account_button: QtWidgets.QToolButton
    exec_lines_action: QtGui.QAction
    data_lines_action: QtGui.QAction
    state_lines_action: QtGui.QAction


class MainWindowUiMixin:
    if TYPE_CHECKING:
        studio_graph: F8StudioGraph
        _bridge: PyStudioServiceBridge
        _prop_editor: F8StudioSingleNodePropertiesWidget
        _log_dock: ServiceLogDock
        _layers_panel: LayersPanelWidget
        _properties_dock: QtWidgets.QDockWidget
        _node_library_dock: QtWidgets.QDockWidget
        _layers_dock: QtWidgets.QDockWidget
        _ai_assist_dock: QtWidgets.QDockWidget
        _dock_widgets: list[QtWidgets.QDockWidget]
        _service_manager_dock: QtWidgets.QDockWidget
        _service_manager: ServiceManagerWidget | None
        _asset_cloud_sync_client: VariantSyncClient | None
        _subscription_sync_service: SubscriptionSyncService | None
        _asset_cloud_account_button: QtWidgets.QToolButton | None
        _node_library_widget: F8StudioNodeLibraryWidget | None
        _unsubscribe_asset_cache_changed: Callable[[], None] | None
        _ai_assist_sidebar: AiAssistSidebarWidget | None
        _deferred_startup_scheduled: bool
        _deferred_startup_completed: bool
        _asset_cloud_sync_total: int
        _asset_cloud_sync_done: int
        _asset_cloud_last_sync_message: str
        _asset_cloud_last_sync_timer: QtCore.QTimer | None
        _default_dock_layout_state: QtCore.QByteArray
        _auto_save_enabled: bool
        _auto_deploy_enabled: bool
        _auto_proxy_enabled: bool
        _performance_overlay_enabled: bool
        _open_project_action: QtGui.QAction
        _quicksave_project_action: QtGui.QAction
        _save_project_as_action: QtGui.QAction
        _clear_all_nodes_action: QtGui.QAction
        _auto_save_action: QtGui.QAction
        _project_history_action: QtGui.QAction
        _import_project_json_action: QtGui.QAction
        _export_project_json_action: QtGui.QAction
        _export_published_session_action: QtGui.QAction
        _deploy_action: QtGui.QAction
        _stop_all_services_action: QtGui.QAction
        _auto_deploy_action: QtGui.QAction
        _manage_components_action: QtGui.QAction
        _variant_catalog_action: QtGui.QAction
        _global_hotkeys_action: QtGui.QAction
        _auto_proxy_action: QtGui.QAction
        _performance_overlay_action: QtGui.QAction
        _import_graph_action: QtGui.QAction
        _save_component_action: QtGui.QAction
        _exec_lines_action: QtGui.QAction
        _data_lines_action: QtGui.QAction
        _state_lines_action: QtGui.QAction
        _view_menu: QtWidgets.QMenu
        _reset_layout_action: QtGui.QAction
        _log_level_menu: QtWidgets.QMenu
        _log_level_action_group: QtGui.QActionGroup
        _log_level_actions: dict[int, QtGui.QAction]
        _LOG_LEVEL_CHOICES: tuple[tuple[str, int], ...]
        _WINDOW_LAYOUT_STATE_VERSION: int

        def menuBar(self) -> QtWidgets.QMenuBar: ...
        def addDockWidget(self, area: QtCore.Qt.DockWidgetArea, dockwidget: QtWidgets.QDockWidget) -> None: ...
        def tabifyDockWidget(self, first: QtWidgets.QDockWidget, second: QtWidgets.QDockWidget) -> None: ...
        def addToolBar(self, area: QtCore.Qt.ToolBarArea, toolbar: QtWidgets.QToolBar) -> None: ...
        def restoreState(self, state: QtCore.QByteArray, version: int = 0) -> bool: ...
        def _auto_load_project(self) -> None: ...
        def _normalize_supported_log_level(self, level: int) -> int: ...
        def _on_log_level_toggled(self, checked: bool, level: int) -> None: ...
        def _on_quickload_project_action(self) -> None: ...
        def _on_quicksave_project_action(self) -> None: ...
        def _on_open_project_action(self) -> None: ...
        def _on_import_project_json_action(self) -> None: ...
        def _on_import_graph_action(self) -> None: ...
        def _on_save_project_as_action(self) -> None: ...
        def _on_export_project_json_action(self) -> None: ...
        def _on_project_history_action(self) -> None: ...
        def _on_save_component_action(self) -> None: ...
        def _on_manage_components_action(self) -> None: ...
        def _on_auto_save_toggled(self, checked: bool) -> None: ...
        def _on_auto_deploy_toggled(self, checked: bool) -> None: ...
        def _on_performance_overlay_toggled(self, checked: bool) -> None: ...
        def _on_auto_proxy_toggled(self, checked: bool) -> None: ...
        def _on_export_published_session_action(self) -> None: ...
        def _on_clear_all_nodes_action(self) -> None: ...
        def _on_deploy_action(self) -> None: ...
        def _on_stop_all_services_action(self) -> None: ...
        def _on_global_hotkeys_action(self) -> None: ...
        def _on_variant_catalog_action(self) -> None: ...
        def _save_window_layout(self) -> None: ...

    def _setup_docks(self) -> None:
        prop_editor = F8StudioSingleNodePropertiesWidget(node_graph=self.studio_graph)
        self._prop_editor = prop_editor
        self._log_dock = ServiceLogDock(self)
        self._layers_panel = LayersPanelWidget(studio_graph=self.studio_graph, parent=self)

        dock_bundle = self._build_main_window_docks(
            properties_widget=prop_editor,
            log_dock=self._log_dock,
            node_library_widget=self._build_deferred_dock_placeholder(
                title="Node Library",
                body="Loading the node catalog after the window is ready.",
            ),
            layers_widget=self._layers_panel,
            ai_assist_widget=self._build_deferred_dock_placeholder(
                title="AI Assist",
                body="AI Assist will initialize when you open this dock.",
            ),
        )
        self._properties_dock = dock_bundle.properties_dock
        self._node_library_dock = dock_bundle.node_library_dock
        self._layers_dock = dock_bundle.layers_dock
        self._ai_assist_dock = dock_bundle.ai_assist_dock
        self._dock_widgets = dock_bundle.all_docks
        self._node_library_dock.visibilityChanged.connect(self._on_node_library_dock_visibility_changed)  # type: ignore[attr-defined]
        self._ai_assist_dock.visibilityChanged.connect(self._on_ai_assist_dock_visibility_changed)  # type: ignore[attr-defined]

    def _build_deferred_dock_placeholder(self, *, title: str, body: str) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title_label = QtWidgets.QLabel(str(title), container)
        title_font = QtGui.QFont(title_label.font())
        title_font.setBold(True)
        title_label.setFont(title_font)

        body_label = QtWidgets.QLabel(str(body), container)
        body_label.setWordWrap(True)
        body_label.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted))

        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addStretch(1)
        return container

    def _replace_dock_widget(self, dock: QtWidgets.QDockWidget, widget: QtWidgets.QWidget) -> None:
        previous_widget = dock.widget()
        dock.setWidget(widget)
        if previous_widget is not None and previous_widget is not widget:
            previous_widget.deleteLater()

    def _ensure_node_library_widget(self) -> None:
        if self._node_library_widget is not None:
            return
        widget = F8StudioNodeLibraryWidget(node_graph=self.studio_graph, asset_cache_auto_refresh=False)
        self._node_library_widget = widget
        self._replace_dock_widget(self._node_library_dock, widget)
        self.rebuild_asset_search_sources()

    def rebuild_asset_search_sources(self) -> None:
        self.studio_graph.rebuild_asset_search_sources()
        widget = self._node_library_widget
        if widget is None:
            return
        try:
            widget.rebuild_asset_search_sources()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._node_library_widget = None
                return
            raise

    def _on_asset_cache_changed(self) -> None:
        self.rebuild_asset_search_sources()

    def _clear_asset_cache_changed_subscription(self) -> None:
        unsubscribe = self._unsubscribe_asset_cache_changed
        self._unsubscribe_asset_cache_changed = None
        if unsubscribe is not None:
            unsubscribe()

    def _ensure_ai_assist_sidebar(self) -> None:
        if self._ai_assist_sidebar is not None:
            return
        sidebar = AiAssistSidebarWidget(studio_graph=self.studio_graph, parent=self)
        self._ai_assist_sidebar = sidebar
        self._replace_dock_widget(self._ai_assist_dock, sidebar)

    def _require_asset_cloud_sync_client(self) -> VariantSyncClient:
        sync_client = self._asset_cloud_sync_client
        if sync_client is None:
            sync_client = VariantSyncClient()
            self._asset_cloud_sync_client = sync_client
        return sync_client

    def _ensure_subscription_sync_service(self) -> SubscriptionSyncService:
        service = self._subscription_sync_service
        if service is not None:
            return service
        service = SubscriptionSyncService(variant_client=self._require_asset_cloud_sync_client(), parent=self)
        service.sync_started.connect(self._on_subscription_sync_started)  # type: ignore[attr-defined]
        service.sync_progress.connect(self._on_subscription_sync_progress)  # type: ignore[attr-defined]
        service.sync_item_failed.connect(self._on_subscription_sync_item_failed)  # type: ignore[attr-defined]
        service.sync_finished.connect(self._on_subscription_sync_finished)  # type: ignore[attr-defined]
        self._subscription_sync_service = service
        return service

    @QtCore.Slot()
    def _run_deferred_startup(self) -> None:
        self._deferred_startup_scheduled = False
        if self._deferred_startup_completed:
            return
        self._deferred_startup_completed = True
        self._auto_load_project()
        self._refresh_asset_cloud_account_button(load_client=False)
        self._ensure_subscription_sync_service().start_initial_sync()
        if self._node_library_dock.isVisible():
            QtCore.QTimer.singleShot(0, self._ensure_node_library_widget)
        if self._ai_assist_dock.isVisible():
            QtCore.QTimer.singleShot(0, self._ensure_ai_assist_sidebar)

    @QtCore.Slot(int)
    def _on_subscription_sync_started(self, total: int) -> None:
        self._asset_cloud_sync_total = int(total)
        self._asset_cloud_sync_done = 0
        self._asset_cloud_last_sync_message = ""
        self._refresh_asset_cloud_account_button(load_client=False)

    @QtCore.Slot(int, int)
    def _on_subscription_sync_progress(self, done: int, total: int) -> None:
        self._asset_cloud_sync_done = int(done)
        self._asset_cloud_sync_total = int(total)
        self._refresh_asset_cloud_account_button(load_client=False)

    @QtCore.Slot(str, str)
    def _on_subscription_sync_item_failed(self, asset_id: str, reason: str) -> None:
        logger.warning(
            "Subscription sync item failed asset_id=%s reason=%s",
            str(asset_id),
            str(reason),
        )

    @QtCore.Slot(int, int, int)
    def _on_subscription_sync_finished(self, installed: int, failed: int, skipped: int) -> None:
        del skipped
        self._asset_cloud_sync_done = self._asset_cloud_sync_total
        self._asset_cloud_last_sync_message = f"Synced {int(installed)}"
        if int(failed) > 0:
            self._asset_cloud_last_sync_message = f"Sync errors ({int(failed)})"
        self._refresh_asset_cloud_account_button(load_client=False)
        timer = self._asset_cloud_last_sync_timer
        if timer is not None:
            timer.stop()
        else:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._clear_asset_cloud_last_sync_message)  # type: ignore[attr-defined]
            self._asset_cloud_last_sync_timer = timer
        timer.start(3500)

        service = self._subscription_sync_service
        if service is None:
            return
        if service.last_completed_request_kind() != "manual":
            return
        if int(failed) > 0:
            show_warning(self, "Refresh subscriptions", f"Installed {int(installed)} assets.\nFailed: {int(failed)}")
            return
        show_info(self, "Refresh subscriptions", f"Installed {int(installed)} assets.")

    @QtCore.Slot()
    def _clear_asset_cloud_last_sync_message(self) -> None:
        self._asset_cloud_last_sync_message = ""
        self._refresh_asset_cloud_account_button(load_client=False)

    @QtCore.Slot(bool)
    def _on_node_library_dock_visibility_changed(self, visible: bool) -> None:
        if not bool(visible):
            return
        if not self._deferred_startup_completed:
            return
        QtCore.QTimer.singleShot(0, self._ensure_node_library_widget)

    @QtCore.Slot(bool)
    def _on_ai_assist_dock_visibility_changed(self, visible: bool) -> None:
        if not bool(visible):
            return
        if not self._deferred_startup_completed:
            return
        QtCore.QTimer.singleShot(0, self._ensure_ai_assist_sidebar)

    def _setup_service_manager_dock(self) -> None:
        manager = ServiceManagerWidget(
            bridge=self._bridge,
            get_declared_services=self._declared_graph_services,
            parent=self,
        )
        self._service_manager = manager
        self._service_manager_dock = self._build_service_manager_dock(
            manager_widget=manager,
            log_dock=self._log_dock,
        )
        self._dock_widgets.append(self._service_manager_dock)

    def _dock_toggle_actions(self) -> list[QtGui.QAction]:
        return [
            self._properties_dock.toggleViewAction(),
            self._node_library_dock.toggleViewAction(),
            self._layers_dock.toggleViewAction(),
            self._ai_assist_dock.toggleViewAction(),
            self._log_dock.toggleViewAction(),
            self._service_manager_dock.toggleViewAction(),
        ]

    def _ordered_view_docks(self) -> list[QtWidgets.QDockWidget]:
        return [
            self._properties_dock,
            self._node_library_dock,
            self._layers_dock,
            self._ai_assist_dock,
            self._log_dock,
            self._service_manager_dock,
        ]

    def _declared_graph_services(self) -> dict[str, str]:
        return collect_declared_services(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )

    def _setup_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self._add_menu_section(
            file_menu,
            [
                self._open_project_action,
                self._quicksave_project_action,
                self._save_project_as_action,
                self._clear_all_nodes_action,
                self._auto_save_action,
                self._project_history_action,
            ],
        )
        self._add_menu_section(
            file_menu,
            [
                self._import_project_json_action,
                self._export_project_json_action,
                self._save_component_action,
                self._export_published_session_action,
            ],
        )

        deploy_menu = self.menuBar().addMenu("Deploy")
        self._add_menu_section(
            deploy_menu,
            [
                self._deploy_action,
                self._stop_all_services_action,
                self._auto_deploy_action,
            ],
        )

        view_menu_bundle = self._build_view_menu(
            dock_widgets=self._ordered_view_docks(),
            auto_proxy_action=self._auto_proxy_action,
            performance_overlay_action=self._performance_overlay_action,
            on_reset_layout=self._on_reset_layout_action,
        )
        self._view_menu = view_menu_bundle.view_menu
        self._reset_layout_action = view_menu_bundle.reset_layout_action

        log_menu_bundle = self._build_log_level_menu(
            choices=self._LOG_LEVEL_CHOICES,
            current_level=self._normalize_supported_log_level(logging.getLogger().getEffectiveLevel()),
            on_level_toggled=self._on_log_level_toggled,
        )
        self._log_level_menu = log_menu_bundle.log_level_menu
        self._log_level_action_group = log_menu_bundle.log_level_action_group
        self._log_level_actions = log_menu_bundle.log_level_actions

        tools_menu = self.menuBar().addMenu("Tools")
        self._add_menu_section(
            tools_menu,
            [
                self._manage_components_action,
                self._variant_catalog_action,
                self._global_hotkeys_action,
            ],
        )
        tools_menu.addSeparator()
        tools_menu.addMenu(self._log_level_menu)

    def _create_action(
        self,
        text: str,
        *,
        handler: ActionHandler,
        shortcut: str | None = None,
        icon: StudioIcon | None = None,
        tool_tip: str | None = None,
        checkable: bool = False,
        checked: bool = False,
    ) -> QtGui.QAction:
        action = QtGui.QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        if icon is not None:
            action.setIcon(icon_for(self, icon))
        if tool_tip:
            action.setToolTip(tool_tip)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(handler)  # type: ignore[attr-defined]
        else:
            action.triggered.connect(handler)  # type: ignore[attr-defined]
        return action

    def _create_graph_actions(self) -> None:
        action_bundle = MainWindowActionBundle(
            quickload_project_action=self._create_action(
                "Load Last Project",
                handler=self._on_quickload_project_action,
                icon=StudioIcon.FOLDER_OPEN,
                tool_tip="Load the most recent project",
            ),
            quicksave_project_action=self._create_action(
                "Save Project",
                handler=self._on_quicksave_project_action,
                shortcut="Ctrl+S",
                icon=StudioIcon.SAVE,
                tool_tip="Save current project (Ctrl+S)",
            ),
            open_project_action=self._create_action(
                "Open Project",
                handler=self._on_open_project_action,
                shortcut="Ctrl+O",
                icon=StudioIcon.FOLDER_OPEN,
                tool_tip="Open project (Ctrl+O)",
            ),
            import_project_json_action=self._create_action(
                "Import Project JSON",
                handler=self._on_import_project_json_action,
                tool_tip="Import a JSON session file into the local project store",
            ),
            import_graph_action=self._create_action(
                "Insert Graph JSON",
                handler=self._on_import_graph_action,
                tool_tip="Insert a session JSON file into the current graph as a copied snapshot",
            ),
            save_project_as_action=self._create_action(
                "Save Project As",
                handler=self._on_save_project_as_action,
                shortcut="Ctrl+Shift+S",
                icon=StudioIcon.SAVE_AS,
                tool_tip="Save as new project (Ctrl+Shift+S)",
            ),
            export_project_json_action=self._create_action(
                "Export Project JSON",
                handler=self._on_export_project_json_action,
                tool_tip="Export the current graph as a full session JSON file",
            ),
            project_history_action=self._create_action(
                "Project History",
                handler=self._on_project_history_action,
                icon=StudioIcon.ARTICLE,
                tool_tip="Browse local project versions and restore an older snapshot as the latest version",
            ),
            save_component_action=self._create_action(
                "Export to Component",
                handler=self._on_save_component_action,
                icon=StudioIcon.PACKAGE_EXPORT,
                tool_tip="Create a publish-safe component from the current graph",
            ),
            manage_components_action=self._create_action(
                "Components Catalog",
                handler=self._on_manage_components_action,
                icon=StudioIcon.CUBE_UNFOLDED,
                tool_tip="Components Catalog",
            ),
            auto_save_action=self._create_action(
                "Auto Save",
                handler=self._on_auto_save_toggled,
                tool_tip="Auto save project after edits (10s debounce)",
                checkable=True,
                checked=self._auto_save_enabled,
            ),
            auto_deploy_action=self._create_action(
                "Auto Deploy",
                handler=self._on_auto_deploy_toggled,
                icon=StudioIcon.AUTOMATION,
                tool_tip="Automatically deploy the graph after edits (10s debounce)",
                checkable=True,
                checked=self._auto_deploy_enabled,
            ),
            performance_overlay_action=self._create_action(
                "Performance Overlay",
                handler=self._on_performance_overlay_toggled,
                tool_tip="Show graph viewer paint/perf overlay",
                checkable=True,
                checked=self._performance_overlay_enabled,
            ),
            auto_proxy_action=self._create_action(
                "Auto Proxy",
                handler=self._on_auto_proxy_toggled,
                tool_tip="Enable zoom-out auto proxy mode for service nodes",
                checkable=True,
                checked=self._auto_proxy_enabled,
            ),
            export_published_session_action=self._create_action(
                "Export Publish JSON",
                handler=self._on_export_published_session_action,
                tool_tip="Export a publish-safe component JSON with redacted sensitive state",
            ),
            clear_all_nodes_action=self._create_action(
                "Clear All Nodes",
                handler=self._on_clear_all_nodes_action,
                icon=StudioIcon.TRASH,
                tool_tip="Clear all nodes from the current graph",
            ),
            deploy_action=self._create_action(
                "Deploy Graph",
                handler=self._on_deploy_action,
                shortcut="F5",
                icon=StudioIcon.SEND,
                tool_tip="Deploy rungraph to the cluster (F5)",
            ),
            stop_all_services_action=self._create_action(
                "Stop All Services",
                handler=self._on_stop_all_services_action,
                shortcut="Shift+F5",
                icon=StudioIcon.STOP_ALL,
                tool_tip="Stop all services (Shift+F5)",
            ),
            global_hotkeys_action=self._create_action(
                "Global Hotkeys",
                handler=self._on_global_hotkeys_action,
                icon=StudioIcon.KEYBOARD,
                tool_tip="Show the current global hotkey registry",
            ),
            variant_catalog_action=self._create_action(
                "Variant Catalog",
                handler=self._on_variant_catalog_action,
                icon=StudioIcon.CUBE,
                tool_tip="Variant Catalog",
            ),
        )
        self._quickload_project_action = action_bundle.quickload_project_action
        self._quicksave_project_action = action_bundle.quicksave_project_action
        self._open_project_action = action_bundle.open_project_action
        self._import_project_json_action = action_bundle.import_project_json_action
        self._import_graph_action = action_bundle.import_graph_action
        self._save_project_as_action = action_bundle.save_project_as_action
        self._export_project_json_action = action_bundle.export_project_json_action
        self._project_history_action = action_bundle.project_history_action
        self._save_component_action = action_bundle.save_component_action
        self._manage_components_action = action_bundle.manage_components_action
        self._auto_save_action = action_bundle.auto_save_action
        self._auto_deploy_action = action_bundle.auto_deploy_action
        self._performance_overlay_action = action_bundle.performance_overlay_action
        self._auto_proxy_action = action_bundle.auto_proxy_action
        self._export_published_session_action = action_bundle.export_published_session_action
        self._clear_all_nodes_action = action_bundle.clear_all_nodes_action
        self._deploy_action = action_bundle.deploy_action
        self._stop_all_services_action = action_bundle.stop_all_services_action
        self._global_hotkeys_action = action_bundle.global_hotkeys_action
        self._variant_catalog_action = action_bundle.variant_catalog_action

    @QtCore.Slot()
    def _on_reset_layout_action(self) -> None:
        if self._default_dock_layout_state.isEmpty():
            return
        restored = self.restoreState(self._default_dock_layout_state, self._WINDOW_LAYOUT_STATE_VERSION)
        if not restored:
            logger.warning("Failed to reset dock layout to defaults")
            return
        self._save_window_layout()

    def _setup_toolbar(self) -> None:
        toolbar_bundle = self._build_main_window_toolbars(
            graph_actions=[
                self._open_project_action,
                self._quicksave_project_action,
                self._save_project_as_action,
                self._clear_all_nodes_action,
                self._project_history_action,
                self._manage_components_action,
                self._variant_catalog_action,
                self._global_hotkeys_action,
            ],
            deploy_actions=[
                self._deploy_action,
                self._stop_all_services_action,
                self._auto_deploy_action,
            ],
            dock_actions=self._dock_toggle_actions(),
            account_clicked=self._on_asset_cloud_account_clicked,
            exec_toggled=self._on_exec_lines_toggled,
            data_toggled=self._on_data_lines_toggled,
            state_toggled=self._on_state_lines_toggled,
        )
        self._asset_cloud_account_button = toolbar_bundle.account_button
        self._exec_lines_action = toolbar_bundle.exec_lines_action
        self._data_lines_action = toolbar_bundle.data_lines_action
        self._state_lines_action = toolbar_bundle.state_lines_action
        self._refresh_asset_cloud_account_button(load_client=True)

    def _set_action_text_beside_icon(
        self,
        toolbar: QtWidgets.QToolBar,
        action: QtGui.QAction,
        italic: bool = False,
    ) -> None:
        action_widget = toolbar.widgetForAction(action)
        if not isinstance(action_widget, QtWidgets.QToolButton):
            return
        action_widget.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if not italic:
            return
        font = QtGui.QFont(action_widget.font())
        font.setItalic(True)
        action_widget.setFont(font)

    def _set_edge_visibility_action_icon(self, action: QtGui.QAction, visible: bool) -> None:
        token = StudioIcon.EYE if visible else StudioIcon.EYE_SLASH
        action.setIcon(icon_for(self, token))

    def _refresh_asset_cloud_account_button(self, *, load_client: bool) -> None:
        button = self._asset_cloud_account_button
        if button is None:
            return
        sync_service = self._subscription_sync_service

        sync_client = self._asset_cloud_sync_client
        if sync_client is None:
            if not load_client:
                self._update_asset_cloud_account_button(
                    button,
                    email=None,
                    account_name=None,
                    signed_in=False,
                )
                return
            sync_client = self._require_asset_cloud_sync_client()

        user = sync_client.current_user()
        self._update_asset_cloud_account_button(
            button,
            email=None if user is None else str(user.email or ""),
            account_name=None if user is None else str(user.name or ""),
            signed_in=user is not None,
            sync_running=False if sync_service is None else sync_service.is_running(),
            sync_done=self._asset_cloud_sync_done,
            sync_total=self._asset_cloud_sync_total,
            status_message=self._asset_cloud_last_sync_message,
        )

    @QtCore.Slot()
    def _on_asset_cloud_account_clicked(self) -> None:
        button = self._asset_cloud_account_button
        if button is None:
            return
        sync_client = self._require_asset_cloud_sync_client()
        self._refresh_asset_cloud_account_button(load_client=False)
        menu = build_asset_account_menu(
            parent=self,
            sync_client=sync_client,
            on_changed=self._on_asset_cloud_account_changed,
            on_refresh_subscriptions=self._on_refresh_subscriptions_requested,
            refresh_subscriptions_enabled=sync_client.current_user() is not None and not self._ensure_subscription_sync_service().is_running(),
        )
        menu.exec(button.mapToGlobal(QtCore.QPoint(0, button.height())))

    def _on_asset_cloud_account_changed(self) -> None:
        service = self._subscription_sync_service
        if service is not None:
            service.cancel()
        self._asset_cloud_sync_done = 0
        self._asset_cloud_sync_total = 0
        self._asset_cloud_last_sync_message = ""
        self._refresh_asset_cloud_account_button(load_client=False)
        self._ensure_subscription_sync_service().start_initial_sync()

    def _on_refresh_subscriptions_requested(self) -> None:
        self._ensure_subscription_sync_service().request_manual_refresh()

    @QtCore.Slot(bool)
    def _on_exec_lines_toggled(self, checked: bool) -> None:
        visible = bool(checked)
        self.studio_graph.set_edge_kind_visible(EDGE_KIND_EXEC, visible)
        self._set_edge_visibility_action_icon(self._exec_lines_action, visible)

    @QtCore.Slot(bool)
    def _on_data_lines_toggled(self, checked: bool) -> None:
        visible = bool(checked)
        self.studio_graph.set_edge_kind_visible(EDGE_KIND_DATA, visible)
        self._set_edge_visibility_action_icon(self._data_lines_action, visible)

    @QtCore.Slot(bool)
    def _on_state_lines_toggled(self, checked: bool) -> None:
        visible = bool(checked)
        self.studio_graph.set_edge_kind_visible(EDGE_KIND_STATE, visible)
        self._set_edge_visibility_action_icon(self._state_lines_action, visible)

    def _build_main_window_docks(
        self,
        *,
        properties_widget: QtWidgets.QWidget,
        log_dock: QtWidgets.QDockWidget,
        node_library_widget: QtWidgets.QWidget,
        layers_widget: QtWidgets.QWidget,
        ai_assist_widget: QtWidgets.QWidget,
    ) -> MainWindowDockBundle:
        properties_dock = self._add_dock_widget(
            title="Properties",
            object_name="PropertiesDock",
            widget=properties_widget,
            area=QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
            icon=StudioIcon.PROPERTY,
        )
        self._configure_dock_toggle_action(log_dock, title="Service Logs", icon=StudioIcon.SERVICE_LOG)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

        node_library_dock = self._add_dock_widget(
            title="Node Library",
            object_name="NodeLibraryDock",
            widget=node_library_widget,
            area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
            icon=StudioIcon.NODELIBRARY,
        )
        layers_dock = self._add_dock_widget(
            title="Layers",
            object_name="LayersDock",
            widget=layers_widget,
            area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
            icon=StudioIcon.STACK_MIDDLE,
        )
        self.tabifyDockWidget(node_library_dock, layers_dock)

        ai_assist_dock = self._add_dock_widget(
            title="AI Assist",
            object_name="AiAssistDock",
            widget=ai_assist_widget,
            area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
            icon=StudioIcon.AI,
        )
        self.tabifyDockWidget(node_library_dock, ai_assist_dock)

        return MainWindowDockBundle(
            properties_dock=properties_dock,
            log_dock=log_dock,
            node_library_dock=node_library_dock,
            layers_dock=layers_dock,
            ai_assist_dock=ai_assist_dock,
        )

    def _build_service_manager_dock(
        self,
        *,
        manager_widget: QtWidgets.QWidget,
        log_dock: QtWidgets.QDockWidget,
    ) -> QtWidgets.QDockWidget:
        service_manager_dock = self._add_dock_widget(
            title="Service Monitor",
            object_name="ServiceManagerDock",
            widget=manager_widget,
            area=QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
            icon=StudioIcon.SERVICE_MONITOR,
        )
        self.tabifyDockWidget(log_dock, service_manager_dock)
        return service_manager_dock

    def _add_dock_widget(
        self,
        *,
        title: str,
        object_name: str,
        widget: QtWidgets.QWidget,
        area: QtCore.Qt.DockWidgetArea,
        icon: StudioIcon,
    ) -> QtWidgets.QDockWidget:
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        self._configure_dock_toggle_action(dock, title=title, icon=icon)
        self.addDockWidget(area, dock)
        return dock

    def _configure_dock_toggle_action(
        self,
        dock: QtWidgets.QDockWidget,
        *,
        title: str,
        icon: StudioIcon,
    ) -> None:
        dock_icon = icon_for(self, icon)
        dock.setWindowTitle(title)
        dock.setWindowIcon(dock_icon)
        toggle_action = dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setIcon(dock_icon)

    def _add_menu_section(self, menu: QtWidgets.QMenu, actions: Sequence[QtGui.QAction]) -> None:
        section_actions = list(actions)
        if not section_actions:
            return
        if menu.actions():
            menu.addSeparator()
        for action in section_actions:
            menu.addAction(action)

    def _build_view_menu(
        self,
        *,
        dock_widgets: Sequence[QtWidgets.QDockWidget],
        auto_proxy_action: QtGui.QAction,
        performance_overlay_action: QtGui.QAction,
        on_reset_layout: Callable[[], None],
    ) -> MainWindowViewMenuBundle:
        view_menu = self.menuBar().addMenu("View")
        for dock in dock_widgets:
            action = dock.toggleViewAction()
            action.setCheckable(True)
            view_menu.addAction(action)
        view_menu.addSeparator()
        view_menu.addAction(auto_proxy_action)
        view_menu.addAction(performance_overlay_action)
        view_menu.addSeparator()

        reset_layout_action = QtGui.QAction("Reset Layout", self)
        reset_layout_action.triggered.connect(on_reset_layout)  # type: ignore[attr-defined]
        view_menu.addAction(reset_layout_action)
        return MainWindowViewMenuBundle(
            view_menu=view_menu,
            reset_layout_action=reset_layout_action,
        )

    def _build_log_level_menu(
        self,
        *,
        choices: Sequence[tuple[str, int]],
        current_level: int,
        on_level_toggled: Callable[[bool, int], None],
    ) -> MainWindowLogMenuBundle:
        log_level_menu = QtWidgets.QMenu("Log Level", self)
        log_level_action_group = QtGui.QActionGroup(self)
        log_level_action_group.setExclusive(True)
        log_level_actions: dict[int, QtGui.QAction] = {}

        for level_name, level_value in choices:
            action = QtGui.QAction(level_name, self)
            action.setCheckable(True)
            action.setChecked(level_value == current_level)
            action.toggled.connect(  # type: ignore[attr-defined]
                lambda checked, selected_level=level_value: on_level_toggled(checked, selected_level)
            )
            log_level_action_group.addAction(action)
            log_level_menu.addAction(action)
            log_level_actions[level_value] = action

        return MainWindowLogMenuBundle(
            log_level_menu=log_level_menu,
            log_level_action_group=log_level_action_group,
            log_level_actions=log_level_actions,
        )

    def _build_main_window_toolbars(
        self,
        *,
        graph_actions: Sequence[QtGui.QAction],
        deploy_actions: Sequence[QtGui.QAction],
        dock_actions: Sequence[QtGui.QAction],
        account_clicked: Callable[[], None],
        exec_toggled: Callable[[bool], None],
        data_toggled: Callable[[bool], None],
        state_toggled: Callable[[bool], None],
    ) -> MainWindowToolbarBundle:
        run_toolbar = QtWidgets.QToolBar("Run", self)
        run_toolbar.setObjectName("RunToolBar")
        run_toolbar.setMovable(False)
        run_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, run_toolbar)

        for action in graph_actions:
            run_toolbar.addAction(action)
        run_toolbar.addSeparator()
        for action in deploy_actions:
            run_toolbar.addAction(action)

        dock_toolbar = QtWidgets.QToolBar("Dock Widgets", self)
        dock_toolbar.setObjectName("DockWidgetsToolBar")
        dock_toolbar.setMovable(False)
        dock_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, dock_toolbar)
        for action in dock_actions:
            dock_toolbar.addAction(action)

        edge_toolbar = QtWidgets.QToolBar("Link Visibility", self)
        edge_toolbar.setObjectName("PipeVisibilityToolBar")
        edge_toolbar.setMovable(False)
        edge_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, edge_toolbar)

        exec_lines_action = QtGui.QAction("EXEC", self)
        exec_lines_action.setCheckable(True)
        exec_lines_action.setChecked(True)
        exec_lines_action.toggled.connect(exec_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(exec_lines_action, True)
        edge_toolbar.addAction(exec_lines_action)
        self._set_action_text_beside_icon(edge_toolbar, exec_lines_action, italic=True)

        data_lines_action = QtGui.QAction("DATA", self)
        data_lines_action.setCheckable(True)
        data_lines_action.setChecked(True)
        data_lines_action.toggled.connect(data_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(data_lines_action, True)
        edge_toolbar.addAction(data_lines_action)
        self._set_action_text_beside_icon(edge_toolbar, data_lines_action, italic=True)

        state_lines_action = QtGui.QAction("STATE", self)
        state_lines_action.setCheckable(True)
        state_lines_action.setChecked(True)
        state_lines_action.toggled.connect(state_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(state_lines_action, True)
        edge_toolbar.addAction(state_lines_action)
        self._set_action_text_beside_icon(edge_toolbar, state_lines_action, italic=True)

        spacer_toolbar = QtWidgets.QToolBar("ToolbarSpacer", self)
        spacer_toolbar.setObjectName("ToolbarSpacerToolBar")
        spacer_toolbar.setMovable(False)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, spacer_toolbar)
        self._add_expanding_spacer(edge_toolbar)

        account_toolbar = QtWidgets.QToolBar("Account", self)
        account_toolbar.setObjectName("AssetCloudAccountToolBar")
        account_toolbar.setMovable(False)
        account_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, account_toolbar)

        self._add_expanding_spacer(spacer_toolbar)
        self._add_expanding_spacer(account_toolbar)

        account_button = QtWidgets.QToolButton(account_toolbar)
        account_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        account_button.clicked.connect(account_clicked)  # type: ignore[attr-defined]
        account_toolbar.addWidget(account_button)

        return MainWindowToolbarBundle(
            account_button=account_button,
            exec_lines_action=exec_lines_action,
            data_lines_action=data_lines_action,
            state_lines_action=state_lines_action,
        )

    def _add_expanding_spacer(self, toolbar: QtWidgets.QToolBar) -> None:
        spacer = QtWidgets.QWidget(toolbar)
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

    def _update_asset_cloud_account_button(
        self,
        button: QtWidgets.QToolButton,
        *,
        email: str | None,
        account_name: str | None,
        signed_in: bool,
        sync_running: bool,
        sync_done: int,
        sync_total: int,
        status_message: str,
    ) -> None:
        if not signed_in:
            button.setText("")
            button.setIcon(icon_for(button, StudioIcon.USER_OFF))
            button.setToolTip("Manage Feel8 asset cloud accounts")
            return

        button.setText("")
        if sync_running:
            button.setIcon(icon_for(button, StudioIcon.REFRESH))
        elif status_message:
            button.setIcon(icon_for(button, StudioIcon.CHECK))
        else:
            button.setIcon(icon_for(button, StudioIcon.USER))
        tooltip_account_name = str(account_name or email or "")
        sync_suffix = ""
        if sync_running:
            sync_suffix = f" | Syncing {int(sync_done)}/{int(sync_total)}"
        elif status_message:
            sync_suffix = f" | {status_message}"
        if tooltip_account_name:
            button.setToolTip(f"Manage Feel8 asset cloud account ({tooltip_account_name}){sync_suffix}")
            return
        button.setToolTip(f"Manage Feel8 asset cloud account{sync_suffix}")
