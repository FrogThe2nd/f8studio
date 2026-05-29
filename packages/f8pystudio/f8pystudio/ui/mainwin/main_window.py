from __future__ import annotations

import logging
from typing import Callable, Iterable

from qtpy import QtCore, QtGui, QtWidgets

from ...assets.common.asset_cache_events import subscribe_asset_cache_changed
from ...assets.subscriptions import SubscriptionSyncService
from ...assets.variants.variant_sync import VariantSyncClient
from ...global_hotkeys.controller import ControlPanelGlobalHotkeyController
from ...monitoring.alerts import MonitorAlertNotifier
from ...nodegraph.node_graph import F8StudioGraph
from ...nodegraph.session import last_session_path
from ...nodegraph.viewer import F8StudioNodeViewer
from ...ui.support.ui_notifications import show_info, show_warning
from ...ui.support.webengine_utils import flush_qt_deferred_deletes
from ..support.runtime_state_sync import RuntimeStateSyncController
from ..widgets.layers_panel import LayersPanelWidget
from ..widgets.node_property_panel import F8StudioSingleNodePropertiesWidget
from ..widgets.service_log_widget import ServiceLogDock
from .ai_assist_sidebar import AiAssistSidebarWidget
from .main_window_project_mixin import MainWindowProjectMixin, _ProjectAutoLoadWorker
from .main_window_runtime_mixin import MainWindowRuntimeMixin
from .main_window_state_mixin import MainWindowStateMixin
from .main_window_ui_mixin import MainWindowUiMixin
from .node_library_widget import F8StudioNodeLibraryWidget
from .service_manager_widget import ServiceManagerWidget
from f8pystudio.bridge.studio_bridge import (
    STARTUP_GATE_TIMEOUT_S,
    PyStudioServiceBridge,
    PyStudioServiceBridgeConfig,
)
from f8pystudio.automation.gui_host import StudioAutomationHost
from f8pystudio.studio_specs.registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS

logger = logging.getLogger(__name__)
_LOG_DOCK_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_MAIN_WINDOW_QT_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


class F8StudioMainWin(
    MainWindowProjectMixin,
    MainWindowRuntimeMixin,
    MainWindowStateMixin,
    MainWindowUiMixin,
    QtWidgets.QMainWindow,
):
    _WINDOW_LAYOUT_SETTINGS_ORGANIZATION = "Feel8"
    _WINDOW_LAYOUT_SETTINGS_APPLICATION = "F8PyStudio"
    _WINDOW_LAYOUT_SETTINGS_GROUP = "main_window/layout/v1"
    _WINDOW_LAYOUT_STATE_KEY = "state"
    _WINDOW_LAYOUT_GEOMETRY_KEY = "geometry"
    _WINDOW_LAYOUT_STATE_VERSION = 1
    _LOG_LEVEL_SETTINGS_GROUP = "main_window/logging/v1"
    _LOG_LEVEL_SETTINGS_KEY = "level_name"
    _AUTOMATION_SETTINGS_GROUP = "main_window/automation/v1"
    _AUTO_SAVE_ENABLED_SETTINGS_KEY = "auto_save_enabled"
    _AUTO_DEPLOY_ENABLED_SETTINGS_KEY = "auto_deploy_enabled"
    _KILL_MANAGED_SERVICES_ON_EXIT_SETTINGS_KEY = "kill_managed_services_on_exit"
    _VIEW_SETTINGS_GROUP = "main_window/view/v1"
    _AUTO_PROXY_ENABLED_SETTINGS_KEY = "auto_proxy_enabled"
    _PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY = "performance_overlay_enabled"
    _PERIODIC_AUTO_SAVE_INTERVAL_MS = 15000
    _AUTO_DEPLOY_DEBOUNCE_MS = 2000
    _DEFERRED_STARTUP_DELAY_MS = 150
    _LOG_LEVEL_CHOICES: tuple[tuple[str, int], ...] = (
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    )

    studio_graph: F8StudioGraph
    _quickload_project_action: QtGui.QAction
    _quicksave_project_action: QtGui.QAction
    _open_project_action: QtGui.QAction
    _import_project_json_action: QtGui.QAction
    _import_graph_action: QtGui.QAction
    _save_project_as_action: QtGui.QAction
    _export_project_json_action: QtGui.QAction
    _project_history_action: QtGui.QAction
    _save_component_action: QtGui.QAction
    _manage_components_action: QtGui.QAction
    _deploy_action: QtGui.QAction
    _stop_all_services_action: QtGui.QAction
    _exec_lines_action: QtGui.QAction
    _data_lines_action: QtGui.QAction
    _state_lines_action: QtGui.QAction
    _view_menu: QtWidgets.QMenu
    _reset_layout_action: QtGui.QAction
    _log_level_menu: QtWidgets.QMenu
    _clear_all_nodes_action: QtGui.QAction
    _export_published_session_action: QtGui.QAction
    _auto_save_action: QtGui.QAction
    _auto_deploy_action: QtGui.QAction
    _kill_managed_services_on_exit_action: QtGui.QAction
    _auto_proxy_action: QtGui.QAction
    _performance_overlay_action: QtGui.QAction
    _global_hotkeys_action: QtGui.QAction
    _variant_catalog_action: QtGui.QAction
    _log_level_action_group: QtGui.QActionGroup
    _log_level_actions: dict[int, QtGui.QAction]
    _dock_widgets: list[QtWidgets.QDockWidget]
    _service_manager_dock: QtWidgets.QDockWidget
    _default_dock_layout_state: QtCore.QByteArray
    _periodic_auto_save_timer: QtCore.QTimer
    _auto_deploy_timer: QtCore.QTimer
    _deferred_auto_deploy_fingerprint_timer: QtCore.QTimer
    _studio_runtime_sync_timer: QtCore.QTimer
    _prop_editor: F8StudioSingleNodePropertiesWidget
    _log_dock: ServiceLogDock
    _layers_panel: LayersPanelWidget
    _properties_dock: QtWidgets.QDockWidget
    _node_library_dock: QtWidgets.QDockWidget
    _layers_dock: QtWidgets.QDockWidget
    _ai_assist_dock: QtWidgets.QDockWidget
    _service_manager: ServiceManagerWidget | None
    _asset_cloud_sync_client: VariantSyncClient | None
    _subscription_sync_service: SubscriptionSyncService | None
    _asset_cloud_account_button: QtWidgets.QToolButton | None
    _asset_cloud_sync_total: int
    _asset_cloud_sync_done: int
    _asset_cloud_last_sync_message: str
    _asset_cloud_last_sync_timer: QtCore.QTimer | None
    _node_library_widget: F8StudioNodeLibraryWidget | None
    _unsubscribe_asset_cache_changed: Callable[[], None] | None
    _ai_assist_sidebar: AiAssistSidebarWidget | None
    _auto_load_worker: _ProjectAutoLoadWorker | None
    _runtime_state_sync: RuntimeStateSyncController
    _global_hotkey_controller: ControlPanelGlobalHotkeyController
    _monitor_alert_notifier: MonitorAlertNotifier
    _automation_host: StudioAutomationHost | None

    def __init__(
        self,
        node_classes: Iterable[type],
        parent=None,
        *,
        bridge: PyStudioServiceBridge | None = None,
        automation_enabled: bool = False,
        automation_token_file: str | None = None,
        automation_port_file: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("F8PyStudio")
        self.resize(1920, 980)

        self._session_file = last_session_path()
        self._session_dialog_dir = str(self._session_file.parent)
        self._exit_autosaved = False
        self._bridge_stopped = False
        self._dock_widgets = []
        self._log_level_actions = {}
        self._default_dock_layout_state = QtCore.QByteArray()
        self._auto_save_enabled = self._read_saved_auto_save_enabled()
        self._auto_deploy_enabled = self._read_saved_auto_deploy_enabled()
        self._kill_managed_services_on_exit_enabled = self._read_saved_kill_managed_services_on_exit_enabled()
        self._auto_proxy_enabled = self._read_saved_auto_proxy_enabled()
        self._performance_overlay_enabled = self._read_saved_performance_overlay_enabled()
        self._asset_cloud_sync_client = None
        self._subscription_sync_service = None
        self._asset_cloud_account_button = None
        self._asset_cloud_sync_total = 0
        self._asset_cloud_sync_done = 0
        self._asset_cloud_last_sync_message = ""
        self._asset_cloud_last_sync_timer = None
        self._node_library_widget = None
        self._unsubscribe_asset_cache_changed = None
        self._ai_assist_sidebar = None
        self._deferred_startup_scheduled = False
        self._deferred_startup_completed = False
        self._closing = False
        self._shutdown_started = False
        self._auto_load_worker = None
        self._automation_host = None

        self.studio_graph = F8StudioGraph(asset_cache_auto_refresh=False)
        self.studio_graph.node_factory.clear_registered_nodes()
        node_class_list = list(node_classes)
        for node_class in node_class_list:
            self.studio_graph.node_factory.register_node(node_class)
        self.studio_graph.install_node_docs_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_component_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_backdrop_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_variant_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_identity_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_duplicate_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_node_state_context_menu_for_nodes(node_class_list)
        self.studio_graph.install_monitor_context_menu_for_nodes(node_class_list)
        self.studio_graph._undo_stack.indexChanged.connect(self._on_graph_undo_index_changed)  # type: ignore[attr-defined]
        self.studio_graph.session_loaded.connect(self._on_graph_session_loaded)  # type: ignore[attr-defined]
        self.studio_graph.graph_inserted.connect(self._on_graph_inserted)  # type: ignore[attr-defined]
        self._last_saved_undo_index = self._current_undo_index()
        self._last_auto_deploy_observed_undo_index = self._current_undo_index()
        self._last_auto_deploy_fingerprint = ""

        self._periodic_auto_save_timer = QtCore.QTimer(self)
        self._periodic_auto_save_timer.setInterval(self._PERIODIC_AUTO_SAVE_INTERVAL_MS)
        self._periodic_auto_save_timer.timeout.connect(self._on_periodic_auto_save_timeout)  # type: ignore[attr-defined]
        self._periodic_auto_save_timer.start()

        self._auto_deploy_timer = QtCore.QTimer(self)
        self._auto_deploy_timer.setSingleShot(True)
        self._auto_deploy_timer.setInterval(self._AUTO_DEPLOY_DEBOUNCE_MS)
        self._auto_deploy_timer.timeout.connect(self._on_auto_deploy_timeout)  # type: ignore[attr-defined]

        self._deferred_auto_deploy_fingerprint_timer = QtCore.QTimer(self)
        self._deferred_auto_deploy_fingerprint_timer.setSingleShot(True)
        self._deferred_auto_deploy_fingerprint_timer.setInterval(0)
        self._deferred_auto_deploy_fingerprint_timer.timeout.connect(self._on_deferred_auto_deploy_fingerprint_timeout)  # type: ignore[attr-defined]

        self._studio_runtime_sync_timer = QtCore.QTimer(self)
        self._studio_runtime_sync_timer.setSingleShot(True)
        self._studio_runtime_sync_timer.setInterval(120)
        self._studio_runtime_sync_timer.timeout.connect(self._on_studio_runtime_sync_timeout)  # type: ignore[attr-defined]

        self.setCentralWidget(self.studio_graph.widget)
        self._setup_docks()
        self._create_graph_actions()
        self._service_manager = None
        self._monitor_alert_notifier = MonitorAlertNotifier()

        self._bridge = bridge or PyStudioServiceBridge(PyStudioServiceBridgeConfig(), parent=self)
        if bridge is not None and self._bridge.parent() is None:
            self._bridge.setParent(self)
        self._apply_kill_managed_services_on_exit_enabled(
            enabled=self._kill_managed_services_on_exit_enabled,
            persist=False,
        )
        self._bridge.ui_command.connect(self._on_ui_command)  # type: ignore[attr-defined]
        self._bridge.service_output.connect(self._on_service_output)  # type: ignore[attr-defined]
        self._bridge.service_process_state.connect(self._on_service_process_state)  # type: ignore[attr-defined]
        self._bridge.log.connect(lambda line: self._log_dock.append("studio", str(line) + "\n"))  # type: ignore[attr-defined]

        self._setup_service_manager_dock()
        self._apply_auto_proxy_enabled(enabled=self._auto_proxy_enabled, persist=False)
        self._apply_performance_overlay_enabled(enabled=self._performance_overlay_enabled, persist=False)
        self._restore_saved_log_level()
        self._setup_menu()
        self._setup_toolbar()
        self._capture_default_dock_layout_state()
        self._restore_saved_window_layout()

        self._shortcut_escape_cancel = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape), self)
        self._shortcut_escape_cancel.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        self._shortcut_escape_cancel.activated.connect(self._on_escape_cancel_placement)  # type: ignore[attr-defined]

        try:
            self.studio_graph.set_service_bridge(self._bridge)
        except _MAIN_WINDOW_QT_ERRORS as exc:
            self._log_dock.report_exception("studio", "studio_graph.set_service_bridge failed", exc)

        self._runtime_state_sync = RuntimeStateSyncController(
            studio_graph=self.studio_graph,
            property_editor=self._prop_editor,
            bridge=self._bridge,
            studio_service_class=STUDIO_SERVICE_CLASS,
        )
        if automation_enabled:
            self._automation_host = StudioAutomationHost(
                main_window=self,
                studio_graph=self.studio_graph,
                bridge=self._bridge,
                token_file=automation_token_file,
                port_file=automation_port_file,
                parent=self,
            )
            automation_info = self._automation_host.start()
            self._log_dock.append(
                "studio",
                f"[automation] listening on {automation_info.host}:{automation_info.port}\n",
            )
        self._global_hotkey_controller = ControlPanelGlobalHotkeyController(
            studio_graph=self.studio_graph,
            emit_log_line=self._append_studio_log_line,
        )
        self.studio_graph.set_global_hotkey_controller(self._global_hotkey_controller)
        self.studio_graph.property_changed.connect(self._on_ui_property_changed)  # type: ignore[attr-defined]
        self.studio_graph.set_node_docs_dialog_opener(self._open_node_docs_dialog_for_graph)
        self._unsubscribe_asset_cache_changed = subscribe_asset_cache_changed(self._on_asset_cache_changed)

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._auto_save_project)  # type: ignore[attr-defined]

    def schedule_deferred_startup(self) -> None:
        if self._deferred_startup_completed or self._deferred_startup_scheduled:
            return
        self._deferred_startup_scheduled = True
        QtCore.QTimer.singleShot(self._DEFERRED_STARTUP_DELAY_MS, self._run_deferred_startup)

    def start_bridge_and_wait_for_startup(self, *, timeout_s: float = STARTUP_GATE_TIMEOUT_S) -> str | None:
        return self._bridge.start_and_wait_for_startup(timeout_s=float(timeout_s))

    def prepare_before_show(self) -> None:
        try:
            self._ensure_ai_assist_sidebar()
        except _MAIN_WINDOW_QT_ERRORS as exc:
            self._log_dock.report_exception("studio", "prepare AI Assist before show failed", exc)
            logger.error("prepare AI Assist before show failed", exc_info=True)

    def stop_bridge(self) -> None:
        if self._bridge_stopped:
            return
        try:
            self._bridge.stop()
        except _MAIN_WINDOW_QT_ERRORS as exc:
            self._log_dock.report_exception("studio", "bridge.stop failed", exc)
            return
        self._bridge_stopped = True

    def _stop_shutdown_timer(self, timer: QtCore.QTimer | None, *, context: str) -> None:
        if timer is None:
            return
        try:
            timer.stop()
        except RuntimeError:
            logger.debug("failed to stop timer during shutdown context=%s", context, exc_info=True)

    def _shutdown_ai_assist_sidebar(self) -> None:
        sidebar = self._ai_assist_sidebar
        self._ai_assist_sidebar = None
        if sidebar is None:
            return
        try:
            sidebar.shutdown()
        except _MAIN_WINDOW_QT_ERRORS:
            logger.exception("failed to shutdown AI Assist sidebar")
        try:
            sidebar.deleteLater()
        except RuntimeError:
            logger.debug("failed to deleteLater AI Assist sidebar", exc_info=True)

    def _teardown_graph_nodes_for_exit(self) -> None:
        try:
            nodes = list(self.studio_graph.all_nodes() or [])
        except _MAIN_WINDOW_QT_ERRORS:
            logger.exception("failed to list graph nodes during shutdown")
            return
        try:
            self.studio_graph._teardown_nodes(nodes)
        except _MAIN_WINDOW_QT_ERRORS:
            logger.exception("failed to teardown graph nodes during shutdown")

    def _request_qt_app_quit(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        try:
            QtCore.QTimer.singleShot(0, app.quit)
        except RuntimeError:
            logger.debug("failed to request Qt app quit during shutdown", exc_info=True)

    def _run_shutdown_step(self, context: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:
            logger.exception("shutdown step failed context=%s", str(context or "").strip())

    def shutdown_for_app_exit(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._closing = True
        self._run_shutdown_step(
            "automation-host-stop",
            lambda: self._automation_host.stop() if self._automation_host is not None else None,
        )
        self._run_shutdown_step("asset-cache-unsubscribe", self._clear_asset_cache_changed_subscription)
        self._run_shutdown_step(
            "timer-periodic-auto-save",
            lambda: self._stop_shutdown_timer(self._periodic_auto_save_timer, context="periodic-auto-save"),
        )
        self._run_shutdown_step(
            "timer-auto-deploy",
            lambda: self._stop_shutdown_timer(self._auto_deploy_timer, context="auto-deploy"),
        )
        self._run_shutdown_step(
            "timer-auto-deploy-fingerprint",
            lambda: self._stop_shutdown_timer(
                self._deferred_auto_deploy_fingerprint_timer,
                context="auto-deploy-fingerprint",
            ),
        )
        self._run_shutdown_step(
            "timer-studio-runtime-sync",
            lambda: self._stop_shutdown_timer(self._studio_runtime_sync_timer, context="studio-runtime-sync"),
        )
        self._run_shutdown_step(
            "timer-asset-cloud-last-sync",
            lambda: self._stop_shutdown_timer(self._asset_cloud_last_sync_timer, context="asset-cloud-last-sync"),
        )
        self._run_shutdown_step("save-window-layout", self._save_window_layout)
        self._run_shutdown_step("auto-save-project", self._auto_save_project)
        sync_service = self._subscription_sync_service
        if sync_service is not None:
            self._run_shutdown_step("subscription-sync-shutdown", sync_service.shutdown)
            self._subscription_sync_service = None
        self._run_shutdown_step("global-hotkeys-close", self._global_hotkey_controller.close)
        self._run_shutdown_step("ai-assist-sidebar-shutdown", self._shutdown_ai_assist_sidebar)
        self._run_shutdown_step("graph-node-teardown", self._teardown_graph_nodes_for_exit)
        self._run_shutdown_step("bridge-stop", self.stop_bridge)
        self._run_shutdown_step("qt-deferred-delete-flush", flush_qt_deferred_deletes)
        self._run_shutdown_step("qt-app-quit", self._request_qt_app_quit)

    def append_discovery_logs(self, *, timing_lines: Iterable[str], error_lines: Iterable[str]) -> None:
        try:
            timing_line_texts = [str(line) for line in timing_lines]
            for line in timing_line_texts:
                self._log_dock.append("studio", line)
            if any("discovery errors:" in line for line in timing_line_texts):
                return
            for line in error_lines:
                self._log_dock.append("studio", str(line))
        except _LOG_DOCK_ERRORS:
            logger.exception("Failed to emit discovery logs to studio log dock")

    def closeEvent(self, event) -> None:
        self.shutdown_for_app_exit()
        super().closeEvent(event)
