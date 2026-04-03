from __future__ import annotations

import logging
from typing import Callable, Iterable, TypeAlias

from qtpy import QtCore, QtGui, QtWidgets

from ...nodegraph import F8StudioGraph
from ...nodegraph.edge_rules import EDGE_KIND_DATA, EDGE_KIND_EXEC, EDGE_KIND_STATE
from ...nodegraph.session import last_session_path
from ...nodegraph.runtime_compiler import CompiledRuntimeGraphs, compile_runtime_graphs_from_studio
from ...nodegraph.viewer import F8StudioNodeViewer
from ...pystudio_service_bridge import STARTUP_GATE_TIMEOUT_S, PyStudioServiceBridge, PyStudioServiceBridgeConfig
from ...pystudio_node_registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from ...bridge.deploy_fingerprint import build_compiled_deploy_fingerprint
from ...ui.support.ui_notifications import show_info, show_warning
from ...ui_bus import UiCommand
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...global_hotkeys.controller import ControlPanelGlobalHotkeyController
from ...assets.variants.variant_sync import VariantSyncClient
from ..dialogs.node_docs_dialog import show_node_docs_dialog
from ..dialogs.global_hotkey_registry_dialog import GlobalHotkeyRegistryDialog
from ..widgets.layers_panel import LayersPanelWidget
from ..widgets.node_property_panel import F8StudioSingleNodePropertiesWidget
from ..widgets.node_library_widget import F8StudioNodeLibraryWidget
from ..widgets.service_manager_widget import ServiceManagerWidget
from ..support.service_inventory import collect_declared_service_ids, collect_declared_services
from ..widgets.service_log_widget import ServiceLogDock
from ..support.runtime_state_sync import RuntimeStateSyncController
from .main_window_docks import build_main_window_docks, build_service_manager_dock
from .main_window_toolbar import (
    build_main_window_toolbars,
    refresh_asset_cloud_account_button as refresh_main_window_account_button,
    set_action_text_beside_icon as set_toolbar_action_text_beside_icon,
    set_edge_visibility_action_icon as set_toolbar_edge_visibility_action_icon,
)
from .main_window_menus import (
    MainWindowGraphMenuSections,
    build_graph_menu,
    build_log_level_menu,
    build_view_menu,
)
from .main_window_actions import build_main_window_actions
from .ai_assist_sidebar import AiAssistSidebarWidget
from ...assets.ui.asset_cloud_account_menu import build_asset_account_menu
from . import main_window_automation as automation_ops
from . import main_window_project_ops as project_ops
from . import main_window_runtime_ops as runtime_ops
from .main_window_prefs import (
    as_qbytearray as prefs_as_qbytearray,
    log_level_name_for_value as prefs_log_level_name_for_value,
    log_level_value_from_name as prefs_log_level_value_from_name,
    normalize_supported_log_level as prefs_normalize_supported_log_level,
    read_layout_bytes as prefs_read_layout_bytes,
    read_saved_log_level_name as prefs_read_saved_log_level_name,
    write_layout_bytes as prefs_write_layout_bytes,
    write_saved_log_level_name as prefs_write_saved_log_level_name,
)
from ..dialogs.node_docs_dialog import SpecTemplate

logger = logging.getLogger(__name__)

ActionHandler: TypeAlias = Callable[[], None] | Callable[[bool], None]


class F8StudioMainWin(QtWidgets.QMainWindow):
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
    _VIEW_SETTINGS_GROUP = "main_window/view/v1"
    _AUTO_PROXY_ENABLED_SETTINGS_KEY = "auto_proxy_enabled"
    _PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY = "performance_overlay_enabled"
    _PERIODIC_AUTO_SAVE_INTERVAL_MS = 15000
    _AUTO_DEPLOY_DEBOUNCE_MS = 2000
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
    _insert_component_action: QtGui.QAction
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
    _auto_proxy_action: QtGui.QAction
    _performance_overlay_action: QtGui.QAction
    _global_hotkeys_action: QtGui.QAction
    _log_level_action_group: QtGui.QActionGroup
    _log_level_actions: dict[int, QtGui.QAction]
    _dock_widgets: list[QtWidgets.QDockWidget]
    _default_dock_layout_state: QtCore.QByteArray
    _periodic_auto_save_timer: QtCore.QTimer
    _auto_deploy_timer: QtCore.QTimer
    _studio_runtime_sync_timer: QtCore.QTimer

    def __init__(
        self,
        node_classes: Iterable[type],
        parent=None,
        *,
        bridge: PyStudioServiceBridge | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("F8PyStudio")
        self.resize(1920, 980)

        self._session_file = last_session_path()
        self._session_dialog_dir = str(self._session_file.parent)
        self._exit_autosaved: bool = False
        self._bridge_stopped: bool = False
        self._dock_widgets = []
        self._log_level_actions = {}
        self._default_dock_layout_state = QtCore.QByteArray()
        self._auto_save_enabled = self._read_saved_auto_save_enabled()
        self._auto_deploy_enabled = self._read_saved_auto_deploy_enabled()
        self._auto_proxy_enabled = self._read_saved_auto_proxy_enabled()
        self._performance_overlay_enabled = self._read_saved_performance_overlay_enabled()
        self._asset_cloud_sync_client = VariantSyncClient()
        self._asset_cloud_account_button: QtWidgets.QToolButton | None = None

        self.studio_graph = F8StudioGraph()
        self.studio_graph.node_factory.clear_registered_nodes()
        for cls in node_classes:
            self.studio_graph.node_factory.register_node(cls)
        self.studio_graph.install_node_docs_context_menu_for_nodes(list(node_classes))
        self.studio_graph.install_variant_context_menu_for_nodes(list(node_classes))
        self.studio_graph.install_identity_context_menu_for_nodes(list(node_classes))
        self.studio_graph.install_duplicate_context_menu_for_nodes(list(node_classes))
        self.studio_graph._undo_stack.indexChanged.connect(self._on_graph_undo_index_changed)  # type: ignore[attr-defined]
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
        self._studio_runtime_sync_timer = QtCore.QTimer(self)
        self._studio_runtime_sync_timer.setSingleShot(True)
        self._studio_runtime_sync_timer.setInterval(120)
        self._studio_runtime_sync_timer.timeout.connect(self._on_studio_runtime_sync_timeout)  # type: ignore[attr-defined]

        self.setCentralWidget(self.studio_graph.widget)

        self._setup_docks()
        self._create_graph_actions()
        self._apply_auto_proxy_enabled(enabled=self._auto_proxy_enabled, persist=False)
        self._apply_performance_overlay_enabled(enabled=self._performance_overlay_enabled, persist=False)
        self._setup_menu()
        self._setup_toolbar()
        self._service_manager: ServiceManagerWidget | None = None

        self._bridge = bridge or PyStudioServiceBridge(PyStudioServiceBridgeConfig(), parent=self)
        if bridge is not None and self._bridge.parent() is None:
            self._bridge.setParent(self)
        self._bridge.ui_command.connect(self._on_ui_command)  # type: ignore[attr-defined]
        self._bridge.service_output.connect(self._on_service_output)  # type: ignore[attr-defined]
        self._bridge.service_process_state.connect(self._on_service_process_state)  # type: ignore[attr-defined]
        self._bridge.log.connect(lambda s: self._log_dock.append("studio", str(s) + "\n"))  # type: ignore[attr-defined]
        self._setup_service_manager_dock()
        self._capture_default_dock_layout_state()
        self._restore_saved_window_layout()
        self._restore_saved_log_level()
        self._setup_view_menu()
        self._setup_log_level_menu()
        self._shortcut_escape_cancel = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        self._shortcut_escape_cancel.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        self._shortcut_escape_cancel.activated.connect(self._on_escape_cancel_placement)  # type: ignore[attr-defined]
        try:
            self.studio_graph.set_service_bridge(self._bridge)
        except Exception as exc:
            self._log_dock.report_exception("studio", "studio_graph.set_service_bridge failed", exc)
        self._runtime_state_sync = RuntimeStateSyncController(
            studio_graph=self.studio_graph,
            property_editor=self._prop_editor,
            bridge=self._bridge,
            studio_service_class=STUDIO_SERVICE_CLASS,
        )
        self._global_hotkey_controller = ControlPanelGlobalHotkeyController(
            studio_graph=self.studio_graph,
            emit_log_line=self._append_studio_log_line,
        )
        self.studio_graph.set_global_hotkey_controller(self._global_hotkey_controller)
        self.studio_graph.property_changed.connect(self._on_ui_property_changed)  # type: ignore[attr-defined]
        self.studio_graph.nodes_deleted.connect(self._on_graph_nodes_deleted)  # type: ignore[attr-defined]
        self.studio_graph.set_node_docs_dialog_opener(self._open_node_docs_dialog_for_graph)
        self.studio_graph.set_component_insert_dialog_opener(self._open_component_insert_dialog_for_graph)

        QtCore.QTimer.singleShot(0, self._auto_load_project)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._auto_save_project)  # type: ignore[attr-defined]

    def start_bridge_and_wait_for_startup(self, *, timeout_s: float = STARTUP_GATE_TIMEOUT_S) -> str | None:
        return self._bridge.start_and_wait_for_startup(timeout_s=float(timeout_s))

    def stop_bridge(self) -> None:
        if self._bridge_stopped:
            return
        try:
            self._bridge.stop()
        except Exception as exc:
            self._log_dock.report_exception("studio", "bridge.stop failed", exc)
            return
        self._bridge_stopped = True

    def append_discovery_logs(self, *, timing_lines: Iterable[str], error_lines: Iterable[str]) -> None:
        try:
            timing_line_texts = [str(line) for line in timing_lines]
            for line in timing_line_texts:
                self._log_dock.append("studio", line)
            if any("discovery errors:" in line for line in timing_line_texts):
                return
            for line in error_lines:
                self._log_dock.append("studio", str(line))
        except Exception:
            logger.exception("Failed to emit discovery logs to studio log dock")

    @QtCore.Slot(str, str)
    def _on_service_output(self, service_id: str, line: str) -> None:
        runtime_ops.handle_service_output(
            bridge=self._bridge,
            log_dock=self._log_dock,
            service_id=service_id,
            line=line,
        )

    @QtCore.Slot(str, bool)
    def _on_service_process_state(self, service_id: str, running: bool) -> None:
        runtime_ops.handle_service_process_state(
            manager=self._service_manager,
            log_dock=self._log_dock,
            service_id=service_id,
            running=bool(running),
        )

    def _setup_docks(self) -> None:
        prop_editor = F8StudioSingleNodePropertiesWidget(node_graph=self.studio_graph)
        self._prop_editor = prop_editor
        self._log_dock = ServiceLogDock(self)
        node_library = F8StudioNodeLibraryWidget(node_graph=self.studio_graph)
        self._layers_panel = LayersPanelWidget(studio_graph=self.studio_graph, parent=self)
        self._ai_assist_sidebar = AiAssistSidebarWidget(studio_graph=self.studio_graph, parent=self)
        dock_bundle = build_main_window_docks(
            self,
            properties_widget=prop_editor,
            log_dock=self._log_dock,
            node_library_widget=node_library,
            layers_widget=self._layers_panel,
            ai_assist_widget=self._ai_assist_sidebar,
        )
        self._properties_dock = dock_bundle.properties_dock
        self._node_library_dock = dock_bundle.node_library_dock
        self._layers_dock = dock_bundle.layers_dock
        self._ai_assist_dock = dock_bundle.ai_assist_dock
        self._dock_widgets = dock_bundle.all_docks

    def _setup_service_manager_dock(self) -> None:
        manager = ServiceManagerWidget(
            bridge=self._bridge,
            get_declared_services=self._declared_graph_services,
            parent=self,
        )
        self._service_manager = manager
        self._service_manager_dock = build_service_manager_dock(
            self,
            manager_widget=manager,
            log_dock=self._log_dock,
        )
        self._dock_widgets.append(self._service_manager_dock)

    def _declared_graph_services(self) -> dict[str, str]:
        return collect_declared_services(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )

    def _setup_menu(self) -> None:
        _ = build_graph_menu(
            self,
            sections=MainWindowGraphMenuSections(
                recent_actions=[
                    self._quickload_project_action,
                    self._quicksave_project_action,
                    self._auto_save_action,
                ],
                project_actions=[
                    self._open_project_action,
                    self._save_project_as_action,
                    self._import_project_json_action,
                    self._export_project_json_action,
                    self._project_history_action,
                    self._save_component_action,
                    self._manage_components_action,
                    self._insert_component_action,
                ],
                import_export_actions=[
                    self._import_graph_action,
                    self._export_published_session_action,
                    self._clear_all_nodes_action,
                ],
                runtime_actions=[
                    self._deploy_action,
                    self._stop_all_services_action,
                    self._auto_deploy_action,
                ],
                utility_actions=[self._global_hotkeys_action],
            ),
        )

    def _setup_view_menu(self) -> None:
        menu_bundle = build_view_menu(
            self,
            dock_widgets=self._dock_widgets,
            auto_proxy_action=self._auto_proxy_action,
            performance_overlay_action=self._performance_overlay_action,
            on_reset_layout=self._on_reset_layout_action,
        )
        self._view_menu = menu_bundle.view_menu
        self._reset_layout_action = menu_bundle.reset_layout_action

    def _setup_log_level_menu(self) -> None:
        menu_bundle = build_log_level_menu(
            self,
            choices=self._LOG_LEVEL_CHOICES,
            current_level=self._normalize_supported_log_level(logging.getLogger().getEffectiveLevel()),
            on_level_toggled=self._on_log_level_toggled,
        )
        self._log_level_menu = menu_bundle.log_level_menu
        self._log_level_action_group = menu_bundle.log_level_action_group
        self._log_level_actions = menu_bundle.log_level_actions

    def _layout_settings(self) -> QtCore.QSettings:
        return QtCore.QSettings()

    @staticmethod
    def _as_qbytearray(value: object) -> QtCore.QByteArray | None:
        return prefs_as_qbytearray(value)

    def _read_layout_bytes(self, *, key: str) -> QtCore.QByteArray | None:
        return prefs_read_layout_bytes(
            settings=self._layout_settings(),
            group=self._WINDOW_LAYOUT_SETTINGS_GROUP,
            key=key,
        )

    def _write_layout_bytes(self, *, key: str, value: QtCore.QByteArray) -> None:
        prefs_write_layout_bytes(
            settings=self._layout_settings(),
            group=self._WINDOW_LAYOUT_SETTINGS_GROUP,
            key=key,
            value=value,
        )

    @classmethod
    def _normalize_supported_log_level(cls, level: int) -> int:
        return prefs_normalize_supported_log_level(level)

    @classmethod
    def _log_level_name_for_value(cls, level: int) -> str:
        return prefs_log_level_name_for_value(level=level, choices=cls._LOG_LEVEL_CHOICES)

    @classmethod
    def _log_level_value_from_name(cls, level_name: str) -> int | None:
        return prefs_log_level_value_from_name(level_name=level_name, choices=cls._LOG_LEVEL_CHOICES)

    def _read_saved_log_level_name(self) -> str:
        return prefs_read_saved_log_level_name(
            settings=self._layout_settings(),
            group=self._LOG_LEVEL_SETTINGS_GROUP,
            key=self._LOG_LEVEL_SETTINGS_KEY,
        )

    def _write_saved_log_level_name(self, *, level_name: str) -> None:
        prefs_write_saved_log_level_name(
            settings=self._layout_settings(),
            group=self._LOG_LEVEL_SETTINGS_GROUP,
            key=self._LOG_LEVEL_SETTINGS_KEY,
            level_name=level_name,
        )

    def _apply_log_level(self, *, level: int, persist: bool) -> None:
        normalized_level = self._normalize_supported_log_level(level)
        logging.getLogger().setLevel(normalized_level)
        if persist:
            self._write_saved_log_level_name(level_name=self._log_level_name_for_value(normalized_level))

    def _restore_saved_log_level(self) -> None:
        saved_level_name = self._read_saved_log_level_name()
        if not saved_level_name:
            return
        saved_level_value = self._log_level_value_from_name(saved_level_name)
        if saved_level_value is None:
            logger.warning("Invalid saved log level ignored: %s", saved_level_name)
            return
        self._apply_log_level(level=saved_level_value, persist=False)

    def _on_log_level_toggled(self, checked: bool, level: int) -> None:
        if not bool(checked):
            return
        self._apply_log_level(level=level, persist=True)

    def _capture_default_dock_layout_state(self) -> None:
        self._default_dock_layout_state = self.saveState(self._WINDOW_LAYOUT_STATE_VERSION)

    def _restore_saved_window_layout(self) -> None:
        geometry_state = self._read_layout_bytes(key=self._WINDOW_LAYOUT_GEOMETRY_KEY)
        if geometry_state is not None and not geometry_state.isEmpty():
            self.restoreGeometry(geometry_state)
        dock_state = self._read_layout_bytes(key=self._WINDOW_LAYOUT_STATE_KEY)
        if dock_state is None or dock_state.isEmpty():
            return
        restored = self.restoreState(dock_state, self._WINDOW_LAYOUT_STATE_VERSION)
        if not restored:
            logger.warning("Failed to restore dock layout from QSettings")

    def _save_window_layout(self) -> None:
        self._write_layout_bytes(
            key=self._WINDOW_LAYOUT_STATE_KEY,
            value=self.saveState(self._WINDOW_LAYOUT_STATE_VERSION),
        )
        self._write_layout_bytes(key=self._WINDOW_LAYOUT_GEOMETRY_KEY, value=self.saveGeometry())

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
        action_bundle = build_main_window_actions(
            create_action=self._create_action,
            auto_save_enabled=self._auto_save_enabled,
            auto_deploy_enabled=self._auto_deploy_enabled,
            performance_overlay_enabled=self._performance_overlay_enabled,
            auto_proxy_enabled=self._auto_proxy_enabled,
            on_quickload_project_action=self._on_quickload_project_action,
            on_quicksave_project_action=self._on_quicksave_project_action,
            on_open_project_action=self._on_open_project_action,
            on_import_project_json_action=self._on_import_project_json_action,
            on_import_graph_action=self._on_import_graph_action,
            on_save_project_as_action=self._on_save_project_as_action,
            on_export_project_json_action=self._on_export_project_json_action,
            on_project_history_action=self._on_project_history_action,
            on_save_component_action=self._on_save_component_action,
            on_manage_components_action=self._on_manage_components_action,
            on_insert_component_action=self._on_insert_component_action,
            on_auto_save_toggled=self._on_auto_save_toggled,
            on_auto_deploy_toggled=self._on_auto_deploy_toggled,
            on_performance_overlay_toggled=self._on_performance_overlay_toggled,
            on_auto_proxy_toggled=self._on_auto_proxy_toggled,
            on_export_published_session_action=self._on_export_published_session_action,
            on_clear_all_nodes_action=self._on_clear_all_nodes_action,
            on_deploy_action=self._on_deploy_action,
            on_stop_all_services_action=self._on_stop_all_services_action,
            on_global_hotkeys_action=self._on_global_hotkeys_action,
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
        self._insert_component_action = action_bundle.insert_component_action
        self._auto_save_action = action_bundle.auto_save_action
        self._auto_deploy_action = action_bundle.auto_deploy_action
        self._performance_overlay_action = action_bundle.performance_overlay_action
        self._auto_proxy_action = action_bundle.auto_proxy_action
        self._export_published_session_action = action_bundle.export_published_session_action
        self._clear_all_nodes_action = action_bundle.clear_all_nodes_action
        self._deploy_action = action_bundle.deploy_action
        self._stop_all_services_action = action_bundle.stop_all_services_action
        self._global_hotkeys_action = action_bundle.global_hotkeys_action

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
        toolbar_bundle = build_main_window_toolbars(
            self,
            graph_actions=[
                self._open_project_action,
                self._quicksave_project_action,
                self._save_project_as_action,
                self._project_history_action,
                self._save_component_action,
                self._manage_components_action,
                self._insert_component_action,
                self._import_graph_action,
                self._export_project_json_action,
                self._export_published_session_action,
                self._clear_all_nodes_action,
            ],
            deploy_actions=[
                self._deploy_action,
                self._stop_all_services_action,
                self._auto_deploy_action,
            ],
            account_clicked=self._on_asset_cloud_account_clicked,
            exec_toggled=self._on_exec_lines_toggled,
            data_toggled=self._on_data_lines_toggled,
            state_toggled=self._on_state_lines_toggled,
        )
        self._asset_cloud_account_button = toolbar_bundle.account_button
        self._exec_lines_action = toolbar_bundle.exec_lines_action
        self._data_lines_action = toolbar_bundle.data_lines_action
        self._state_lines_action = toolbar_bundle.state_lines_action
        self._refresh_asset_cloud_account_button()

    def _set_action_text_beside_icon(
        self, toolbar: QtWidgets.QToolBar, action: QtGui.QAction, italic: bool = False
    ) -> None:
        set_toolbar_action_text_beside_icon(toolbar, action, italic=italic)

    def _set_edge_visibility_action_icon(self, action: QtGui.QAction, visible: bool) -> None:
        set_toolbar_edge_visibility_action_icon(self, action, visible=visible)

    def _refresh_asset_cloud_account_button(self) -> None:
        button = self._asset_cloud_account_button
        if button is None:
            return
        user = self._asset_cloud_sync_client.current_user()
        refresh_main_window_account_button(
            button,
            username=None if user is None else str(user.username or ""),
            display_name=None if user is None else str(user.displayName or ""),
            signed_in=user is not None,
        )

    @QtCore.Slot()
    def _on_asset_cloud_account_clicked(self) -> None:
        button = self._asset_cloud_account_button
        if button is None:
            return
        self._refresh_asset_cloud_account_button()
        menu = build_asset_account_menu(
            parent=self,
            sync_client=self._asset_cloud_sync_client,
            on_changed=self._refresh_asset_cloud_account_button,
        )
        menu.exec(button.mapToGlobal(QtCore.QPoint(0, button.height())))

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

    def closeEvent(self, event):
        self._save_window_layout()
        self._auto_save_project()
        self._global_hotkey_controller.close()
        self.stop_bridge()
        super().closeEvent(event)

    @QtCore.Slot()
    def _auto_load_project(self) -> None:
        project_ops.auto_load_project(studio_graph=self.studio_graph, log_dock=self._log_dock)
        self._mark_session_saved()
        self._mark_auto_deploy_synced()
        self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _auto_save_project(self) -> None:
        # Called from both `closeEvent` and `QApplication.aboutToQuit`; guard to avoid double-save on exit.
        if not self._auto_save_enabled:
            return
        if not self._graph_has_unsaved_changes():
            self._exit_autosaved = True
            return
        self._exit_autosaved = project_ops.auto_save_project(
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            already_saved=self._exit_autosaved,
        )
        if self._exit_autosaved:
            self._mark_session_saved()

    @QtCore.Slot()
    def _on_quicksave_project_action(self) -> None:
        project_ops.save_project(parent=self, studio_graph=self.studio_graph, show_info=show_info)
        self._mark_session_saved()

    @QtCore.Slot()
    def _on_quickload_project_action(self) -> None:
        loaded = project_ops.load_last_project(
            parent=self,
            studio_graph=self.studio_graph,
            session_file=self._session_file,
            show_info=show_info,
        )
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_open_project_action(self) -> None:
        session_dir, loaded = project_ops.open_project(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = session_dir
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_import_project_json_action(self) -> None:
        session_dir, loaded = project_ops.import_project_json(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = session_dir
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_save_project_as_action(self) -> None:
        session_dir, saved = project_ops.save_project_as(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = session_dir
        if saved:
            self._mark_session_saved()

    @QtCore.Slot()
    def _on_export_project_json_action(self) -> None:
        session_dir, exported_path = project_ops.export_project_json(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = session_dir
        if exported_path:
            show_info(self, "Project JSON exported", f"Exported project JSON to:\n{exported_path}")

    @QtCore.Slot()
    def _on_project_history_action(self) -> None:
        restored = project_ops.restore_project_history(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            show_warning=show_warning,
            show_info_message=show_info,
        )
        if restored:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_save_component_action(self) -> None:
        project_ops.save_component(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            show_warning=show_warning,
        )

    @QtCore.Slot()
    def _on_manage_components_action(self) -> None:
        project_ops.manage_components(parent=self, studio_graph=self.studio_graph)

    @QtCore.Slot()
    def _on_insert_component_action(self) -> None:
        project_ops.insert_component(parent=self, studio_graph=self.studio_graph)

    def _open_component_insert_dialog_for_graph(self, scene_pos: tuple[float, float] | None) -> None:
        project_ops.insert_component(parent=self, studio_graph=self.studio_graph, scene_pos=scene_pos)

    def _open_node_docs_dialog_for_graph(self, spec: SpecTemplate, node_id: str, node_name: str) -> None:
        project_ops.show_node_docs(parent=self, spec=spec, node_id=node_id, node_name=node_name)

    @QtCore.Slot()
    def _on_export_published_session_action(self) -> None:
        session_dir, published_path = project_ops.export_publish_json(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = session_dir
        if published_path:
            show_info(self, "Publish JSON exported", f"Exported publish-safe JSON to:\n{published_path}")

    @QtCore.Slot()
    def _on_import_graph_action(self) -> None:
        self._session_dialog_dir = project_ops.import_graph_json(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )

    @QtCore.Slot()
    def _on_clear_all_nodes_action(self) -> None:
        runtime_ops.clear_all_nodes(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            show_warning=show_warning,
        )

    @QtCore.Slot()
    def _on_deploy_action(self) -> None:
        try:
            compiled = compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or "deploy blocked by invalid graph"
            self._log_dock.append("studio", f"[deploy][blocked] {msg}\n")
            show_warning(self, "Deploy blocked", msg)
            return
        except Exception as exc:
            self._log_dock.append("studio", f"[deploy][error] {exc}\n")
            self._log_dock.report_exception("studio", "deploy compile failed", exc)
            show_warning(self, "Deploy failed", str(exc))
            return
        runtime_ops.deploy_graph(
            parent=self,
            compiled=compiled,
            log_dock=self._log_dock,
            bridge=self._bridge,
        )
        self._mark_auto_deploy_synced(compiled=compiled)

    @QtCore.Slot()
    def _on_stop_all_services_action(self) -> None:
        service_ids = collect_declared_service_ids(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )
        runtime_ops.stop_all_services(
            service_ids=service_ids,
            bridge=self._bridge,
            log_dock=self._log_dock,
        )

    @QtCore.Slot()
    def _on_global_hotkeys_action(self) -> None:
        dialog = GlobalHotkeyRegistryDialog(
            self,
            entries_provider=self._global_hotkey_controller.registry_entries,
        )
        dialog.node_requested.connect(self._focus_node_by_id)  # type: ignore[attr-defined]
        self._global_hotkey_controller.registry_changed.connect(dialog.refresh_entries)  # type: ignore[attr-defined]
        dialog.exec()

    @QtCore.Slot(str)
    def _focus_node_by_id(self, node_id: str) -> None:
        target_node_id = str(node_id or "").strip()
        if not target_node_id:
            return
        try:
            node = self.studio_graph.get_node_by_id(target_node_id)
        except Exception:
            node = None
        if node is None:
            return
        try:
            for existing in list(self.studio_graph.selected_nodes() or []):
                existing.set_property("selected", False, push_undo=False)
        except Exception:
            logger.exception("Failed to clear selection while focusing hotkey row nodeId=%s", target_node_id)
        try:
            node.set_property("selected", True, push_undo=False)
        except Exception:
            logger.exception("Failed to select hotkey row nodeId=%s", target_node_id)
        self._prop_editor.set_node(node)
        viewer = self.studio_graph.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            try:
                view_item = node.view
                viewer.centerOn(view_item)
            except Exception:
                logger.exception("Failed to center focused hotkey row nodeId=%s", target_node_id)

    @QtCore.Slot()
    def _on_escape_cancel_placement(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None and app.activePopupWidget() is not None:
            return
        viewer = self.studio_graph.viewer()
        if viewer is None:
            return
        try:
            if viewer.is_graph_placement_active():
                self.studio_graph.cancel_graph_placement()
                return
            if viewer.is_node_placement_active():
                self.studio_graph.cancel_node_placement()
                return
        except (AttributeError, RuntimeError, TypeError):
            return

    def _on_runtime_state_updated(self, service_id: str, node_id: str, field: str, value: object, ts_ms: object) -> None:
        self._runtime_state_sync.on_runtime_state_updated(service_id, node_id, field, value, ts_ms)

    def _on_ui_command(self, cmd: UiCommand) -> None:
        runtime_ops.handle_ui_command(
            cmd=cmd,
            service_manager=self._service_manager,
            on_runtime_state_updated=self._on_runtime_state_updated,
            studio_graph=self.studio_graph,
        )

    def _on_ui_property_changed(self, node: object, name: str, value: object) -> None:
        self._runtime_state_sync.on_ui_property_changed(node, name, value)
        self._global_hotkey_controller.on_graph_property_changed(node, name, value)

    def _on_graph_nodes_deleted(self, node_ids: list[str]) -> None:
        self._global_hotkey_controller.on_nodes_deleted(node_ids)

    def _append_studio_log_line(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        if not text.endswith("\n"):
            text = text + "\n"
        self._log_dock.append("studio", text)

    @staticmethod
    def _coerce_bool_setting(raw: object, *, default: bool) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        text = str(raw or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _read_saved_auto_save_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_SAVE_ENABLED_SETTINGS_KEY, True)
        finally:
            settings.endGroup()
        return self._coerce_bool_setting(raw, default=True)

    def _write_saved_auto_save_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_SAVE_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _read_saved_auto_deploy_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_DEPLOY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return self._coerce_bool_setting(raw, default=False)

    def _write_saved_auto_deploy_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_DEPLOY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _read_saved_performance_overlay_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            raw = settings.value(self._PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return self._coerce_bool_setting(raw, default=False)

    def _write_saved_performance_overlay_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            settings.setValue(self._PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _apply_performance_overlay_enabled(self, *, enabled: bool, persist: bool) -> None:
        self._performance_overlay_enabled = bool(enabled)
        viewer = self.studio_graph.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.set_performance_overlay_enabled(self._performance_overlay_enabled)
        if persist:
            self._write_saved_performance_overlay_enabled(enabled=self._performance_overlay_enabled)

    def _read_saved_auto_proxy_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_PROXY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return self._coerce_bool_setting(raw, default=False)

    def _write_saved_auto_proxy_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_PROXY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _apply_auto_proxy_enabled(self, *, enabled: bool, persist: bool) -> None:
        self._auto_proxy_enabled = bool(enabled)
        viewer = self.studio_graph.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.set_auto_proxy_enabled(self._auto_proxy_enabled)
        if persist:
            self._write_saved_auto_proxy_enabled(enabled=self._auto_proxy_enabled)

    def _current_undo_index(self) -> int:
        return automation_ops.current_undo_index(self.studio_graph._undo_stack)  # type: ignore[attr-defined]

    def _graph_has_unsaved_changes(self) -> bool:
        return automation_ops.graph_has_unsaved_changes(
            current_undo_index=self._current_undo_index(),
            last_saved_undo_index=self._last_saved_undo_index,
        )

    def _mark_session_saved(self) -> None:
        self._last_saved_undo_index = automation_ops.mark_session_saved(
            current_undo_index=self._current_undo_index()
        )

    def _mark_auto_deploy_observed(self) -> None:
        self._last_auto_deploy_observed_undo_index = automation_ops.mark_auto_deploy_observed(
            current_undo_index=self._current_undo_index()
        )

    def _deploy_fingerprint_from_compiled(self, compiled: CompiledRuntimeGraphs) -> str:
        return automation_ops.deploy_fingerprint_from_compiled(compiled)

    def _refresh_auto_deploy_fingerprint(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        self._last_auto_deploy_fingerprint = automation_ops.refresh_auto_deploy_fingerprint(
            current_fingerprint=self._last_auto_deploy_fingerprint,
            compile_compiled=lambda: compile_runtime_graphs_from_studio(self.studio_graph),
            compiled=compiled,
        )

    def _mark_auto_deploy_synced(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        (
            self._last_auto_deploy_observed_undo_index,
            self._last_auto_deploy_fingerprint,
        ) = automation_ops.mark_auto_deploy_synced(
            current_undo_index=self._current_undo_index(),
            compile_compiled=lambda: compile_runtime_graphs_from_studio(self.studio_graph),
            current_fingerprint=self._last_auto_deploy_fingerprint,
            compiled=compiled,
        )

    @QtCore.Slot(bool)
    def _on_auto_save_toggled(self, checked: bool) -> None:
        self._auto_save_enabled = bool(checked)
        self._write_saved_auto_save_enabled(enabled=self._auto_save_enabled)

    @QtCore.Slot(bool)
    def _on_auto_deploy_toggled(self, checked: bool) -> None:
        self._auto_deploy_enabled = automation_ops.on_auto_deploy_toggled(
            checked=bool(checked),
            current_undo_index=self._current_undo_index(),
            last_auto_deploy_observed_undo_index=self._last_auto_deploy_observed_undo_index,
            auto_deploy_timer=self._auto_deploy_timer,
        )
        self._write_saved_auto_deploy_enabled(enabled=self._auto_deploy_enabled)

    @QtCore.Slot(bool)
    def _on_performance_overlay_toggled(self, checked: bool) -> None:
        self._apply_performance_overlay_enabled(enabled=bool(checked), persist=True)

    @QtCore.Slot(bool)
    def _on_auto_proxy_toggled(self, checked: bool) -> None:
        self._apply_auto_proxy_enabled(enabled=bool(checked), persist=True)

    @QtCore.Slot(int)
    def _on_graph_undo_index_changed(self, index: int) -> None:
        _ = index
        self._exit_autosaved = False
        automation_ops.on_graph_undo_index_changed(
            loading_session=bool(self.studio_graph._loading_session),  # type: ignore[attr-defined]
            studio_runtime_sync_timer=self._studio_runtime_sync_timer,
            auto_deploy_enabled=self._auto_deploy_enabled,
            auto_deploy_timer=self._auto_deploy_timer,
        )

    @QtCore.Slot()
    def _on_studio_runtime_sync_timeout(self) -> None:
        try:
            compiled = compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or "studio runtime sync blocked by invalid graph"
            self._log_dock.append("studio", f"[studio][sync][blocked] {msg}\n")
            return
        except Exception as exc:
            self._log_dock.append("studio", f"[studio][sync][error] {exc}\n")
            self._log_dock.report_exception("studio", "studio runtime sync compile failed", exc)
            return

        try:
            runtime_ops.sync_studio_runtime(
                compiled=compiled,
                log_dock=self._log_dock,
                bridge=self._bridge,
            )
        except Exception as exc:
            self._log_dock.report_exception("studio", "studio runtime sync failed", exc)

    @QtCore.Slot()
    def _on_periodic_auto_save_timeout(self) -> None:
        saved = automation_ops.periodic_auto_save_timeout(
            auto_save_enabled=self._auto_save_enabled,
            has_unsaved_changes=self._graph_has_unsaved_changes(),
            save_last_project=self.studio_graph.save_last_project,
            log_dock=self._log_dock,
        )
        if saved:
            self._mark_session_saved()

    @QtCore.Slot()
    def _on_auto_deploy_timeout(self) -> None:
        if not self._auto_deploy_enabled:
            return
        current_undo_index = self._current_undo_index()
        if current_undo_index == self._last_auto_deploy_observed_undo_index:
            return

        try:
            compiled = compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or "auto deploy blocked by invalid graph"
            self._log_dock.append("studio", f"[deploy][auto][blocked] {msg}\n")
            self._mark_auto_deploy_observed()
            return
        except Exception as exc:
            self._log_dock.append("studio", f"[deploy][auto][error] {exc}\n")
            self._log_dock.report_exception("studio", "auto deploy compile failed", exc)
            self._mark_auto_deploy_observed()
            return

        (
            self._last_auto_deploy_observed_undo_index,
            self._last_auto_deploy_fingerprint,
        ) = runtime_ops.apply_auto_deploy(
            compiled=compiled,
            current_undo_index=current_undo_index,
            last_auto_deploy_observed_undo_index=self._last_auto_deploy_observed_undo_index,
            last_auto_deploy_fingerprint=self._last_auto_deploy_fingerprint,
            bridge=self._bridge,
            log_dock=self._log_dock,
            declared_service_ids=self._declared_graph_services().keys(),
            fingerprint=self._deploy_fingerprint_from_compiled(compiled),
        )
