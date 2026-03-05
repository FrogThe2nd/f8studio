from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from qtpy import QtCore, QtGui, QtWidgets

from ..nodegraph import F8StudioGraph
from ..nodegraph.edge_rules import EDGE_KIND_DATA, EDGE_KIND_EXEC, EDGE_KIND_STATE
from ..nodegraph.session import last_session_path
from ..nodegraph.runtime_compiler import compile_runtime_graphs_from_studio
from ..pystudio_service_bridge import PyStudioServiceBridge, PyStudioServiceBridgeConfig
from ..pystudio_node_registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from ..ui_notifications import show_info, show_warning
from ..ui_bus import UiCommand, UiCommandApplier
from ..ui_icons import StudioIcon, icon_for
from .node_property_widgets import F8StudioSingleNodePropertiesWidget
from .node_library_widget import F8StudioNodeLibraryWidget
from .service_manager_widget import ServiceManagerWidget
from .service_inventory import collect_declared_service_ids, collect_declared_services
from .service_log_widget import ServiceLogDock
from .runtime_state_sync import RuntimeStateSyncController
from .session_actions import (
    auto_load_session as session_auto_load_session,
    auto_save_session as session_auto_save_session,
    insert_graph_from_dialog as session_insert_graph_from_dialog,
    load_last_session as session_load_last_session,
    load_session_from_dialog as session_load_session_from_dialog,
    save_session as session_save_session,
    save_session_as_dialog as session_save_session_as_dialog,
)
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

logger = logging.getLogger(__name__)


class F8StudioMainWin(QtWidgets.QMainWindow):
    _WINDOW_LAYOUT_SETTINGS_ORGANIZATION = "Feel8"
    _WINDOW_LAYOUT_SETTINGS_APPLICATION = "F8PyStudio"
    _WINDOW_LAYOUT_SETTINGS_GROUP = "main_window/layout/v1"
    _WINDOW_LAYOUT_STATE_KEY = "state"
    _WINDOW_LAYOUT_GEOMETRY_KEY = "geometry"
    _WINDOW_LAYOUT_STATE_VERSION = 1
    _LOG_LEVEL_SETTINGS_GROUP = "main_window/logging/v1"
    _LOG_LEVEL_SETTINGS_KEY = "level_name"
    _LOG_LEVEL_CHOICES: tuple[tuple[str, int], ...] = (
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    )

    studio_graph: F8StudioGraph
    _exec_lines_action: QtGui.QAction
    _data_lines_action: QtGui.QAction
    _state_lines_action: QtGui.QAction
    _view_menu: QtWidgets.QMenu
    _reset_layout_action: QtGui.QAction
    _log_level_menu: QtWidgets.QMenu
    _log_level_action_group: QtGui.QActionGroup
    _log_level_actions: dict[int, QtGui.QAction]
    _dock_widgets: list[QtWidgets.QDockWidget]
    _default_dock_layout_state: QtCore.QByteArray

    def __init__(self, node_classes: Iterable[type], parent=None):
        super().__init__(parent)
        self.setWindowTitle("F8PyStudio")
        self.resize(1920, 980)

        self._session_file = last_session_path()
        self._session_dialog_dir = str(self._session_file.parent)
        self._exit_autosaved: bool = False
        self._dock_widgets = []
        self._log_level_actions = {}
        self._default_dock_layout_state = QtCore.QByteArray()

        self.studio_graph = F8StudioGraph()
        self.studio_graph.node_factory.clear_registered_nodes()
        for cls in node_classes:
            self.studio_graph.node_factory.register_node(cls)
        self.studio_graph.install_variant_context_menu_for_nodes(list(node_classes))
        self.studio_graph.install_identity_context_menu_for_nodes(list(node_classes))
        self.studio_graph.install_duplicate_context_menu_for_nodes(list(node_classes))

        self.setCentralWidget(self.studio_graph.widget)

        self._setup_docks()
        self._deploy_action = self._create_deploy_action()
        self._stop_all_services_action = self._create_stop_all_services_action()
        self._setup_menu()
        self._setup_toolbar()
        self._service_manager: ServiceManagerWidget | None = None

        self._bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig(), parent=self)
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
        self._bridge.start()
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
        self.studio_graph.property_changed.connect(self._on_ui_property_changed)  # type: ignore[attr-defined]

        QtCore.QTimer.singleShot(0, self._auto_load_session)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._auto_save_session)  # type: ignore[attr-defined]

    @QtCore.Slot(str, str)
    def _on_service_output(self, service_id: str, line: str) -> None:
        try:
            svc_name = str(self._bridge.get_service_class(service_id) or "").strip()
        except Exception:
            svc_name = ""
        if svc_name:
            try:
                self._log_dock.set_service_name(service_id, svc_name)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self._log_dock.append(service_id, line)

    @QtCore.Slot(str, bool)
    def _on_service_process_state(self, service_id: str, running: bool) -> None:
        manager = self._service_manager
        if bool(running):
            if manager is not None:
                manager.queue_refresh()
            return
        try:
            self._log_dock.close_service_tab(service_id)
        except (AttributeError, RuntimeError, TypeError):
            pass
        if manager is not None:
            manager.queue_refresh()

    def _setup_docks(self) -> None:
        prop_editor = F8StudioSingleNodePropertiesWidget(node_graph=self.studio_graph)
        self._prop_editor = prop_editor
        self._properties_dock = QtWidgets.QDockWidget("Properties", self)
        self._properties_dock.setObjectName("PropertiesDock")
        self._properties_dock.setWidget(prop_editor)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self._properties_dock)

        self._log_dock = ServiceLogDock(self)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._log_dock)

        node_library = F8StudioNodeLibraryWidget(node_graph=self.studio_graph)
        self._node_library_dock = QtWidgets.QDockWidget("Node Library", self)
        self._node_library_dock.setObjectName("NodeLibraryDock")
        self._node_library_dock.setWidget(node_library)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self._node_library_dock)

        self._dock_widgets = [self._properties_dock, self._log_dock, self._node_library_dock]

    def _setup_service_manager_dock(self) -> None:
        manager = ServiceManagerWidget(
            bridge=self._bridge,
            get_declared_services=self._declared_graph_services,
            parent=self,
        )
        self._service_manager = manager
        self._service_manager_dock = QtWidgets.QDockWidget("Service Manager", self)
        self._service_manager_dock.setObjectName("ServiceManagerDock")
        self._service_manager_dock.setWidget(manager)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._service_manager_dock)
        self.tabifyDockWidget(self._log_dock, self._service_manager_dock)
        self._dock_widgets.append(self._service_manager_dock)

    def _declared_graph_services(self) -> dict[str, str]:
        return collect_declared_services(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )

    def _setup_menu(self) -> None:
        menu = self.menuBar().addMenu("Graph")

        load_action = QtWidgets.QAction("Load Last Session", self)
        load_action.setShortcut("Ctrl+O")
        load_action.triggered.connect(self._load_session_action)  # type: ignore[attr-defined]
        menu.addAction(load_action)

        save_action = QtWidgets.QAction("Save Session", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_session_action)  # type: ignore[attr-defined]
        menu.addAction(save_action)

        menu.addSeparator()

        load_from_action = QtWidgets.QAction("Load Session…", self)
        load_from_action.setShortcut("Ctrl+Shift+O")
        load_from_action.triggered.connect(self._load_session_from_action)  # type: ignore[attr-defined]
        menu.addAction(load_from_action)

        insert_action = QtWidgets.QAction("Insert Graph…", self)
        insert_action.setShortcut("Ctrl+Shift+I")
        insert_action.triggered.connect(self._insert_graph_from_action)  # type: ignore[attr-defined]
        menu.addAction(insert_action)

        save_as_action = QtWidgets.QAction("Save Session As…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_session_as_action)  # type: ignore[attr-defined]
        menu.addAction(save_as_action)

        menu.addSeparator()

        compile_action = QtWidgets.QAction("Compile Runtime Graph (print)", self)
        compile_action.setShortcut("Ctrl+R")
        compile_action.triggered.connect(self._compile_runtime_action)  # type: ignore[attr-defined]
        menu.addAction(compile_action)

        menu.addAction(self._deploy_action)
        menu.addAction(self._stop_all_services_action)

    def _setup_view_menu(self) -> None:
        self._view_menu = self.menuBar().addMenu("View")
        for dock in self._dock_widgets:
            action = dock.toggleViewAction()
            action.setCheckable(True)
            self._view_menu.addAction(action)
        self._view_menu.addSeparator()
        self._reset_layout_action = QtGui.QAction("Reset Layout", self)
        self._reset_layout_action.triggered.connect(self._on_reset_layout_triggered)  # type: ignore[attr-defined]
        self._view_menu.addAction(self._reset_layout_action)

    def _setup_log_level_menu(self) -> None:
        self._log_level_menu = self.menuBar().addMenu("Logs")
        self._log_level_action_group = QtGui.QActionGroup(self)
        self._log_level_action_group.setExclusive(True)

        current_level = self._normalize_supported_log_level(logging.getLogger().getEffectiveLevel())
        for level_name, level_value in self._LOG_LEVEL_CHOICES:
            action = QtGui.QAction(level_name, self)
            action.setCheckable(True)
            action.setChecked(level_value == current_level)
            action.toggled.connect(  # type: ignore[attr-defined]
                lambda checked, selected_level=level_value: self._on_log_level_toggled(checked, selected_level)
            )
            self._log_level_action_group.addAction(action)
            self._log_level_menu.addAction(action)
            self._log_level_actions[level_value] = action

    def _layout_settings(self) -> QtCore.QSettings:
        return QtCore.QSettings(self._WINDOW_LAYOUT_SETTINGS_ORGANIZATION, self._WINDOW_LAYOUT_SETTINGS_APPLICATION)

    @staticmethod
    def _as_qbytearray(value: Any) -> QtCore.QByteArray | None:
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

    @QtCore.Slot()
    def _on_reset_layout_triggered(self) -> None:
        if self._default_dock_layout_state.isEmpty():
            return
        restored = self.restoreState(self._default_dock_layout_state, self._WINDOW_LAYOUT_STATE_VERSION)
        if not restored:
            logger.warning("Failed to reset dock layout to defaults")
            return
        self._save_window_layout()

    def _create_deploy_action(self) -> QtGui.QAction:
        deploy_action = QtGui.QAction("Send Graph", self)
        deploy_action.setShortcut("F5")
        deploy_action.triggered.connect(self._on_deploy_action_triggered)  # type: ignore[attr-defined]
        return deploy_action

    def _create_stop_all_services_action(self) -> QtGui.QAction:
        action = QtGui.QAction("Stop All Services", self)
        action.triggered.connect(self._on_stop_all_services_triggered)  # type: ignore[attr-defined]
        return action

    def _setup_toolbar(self) -> None:
        tb = QtWidgets.QToolBar("Run", self)
        tb.setObjectName("RunToolBar")
        tb.setMovable(False)
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.addToolBar(QtCore.Qt.TopToolBarArea, tb)

        # Graph file management.
        self._open_icon = icon_for(self, StudioIcon.FOLDER_OPEN)
        self._insert_icon = icon_for(self, StudioIcon.FOLDER_PLUS)
        self._save_icon = icon_for(self, StudioIcon.SAVE)

        self._load_from_action = QtGui.QAction("Load Session…", self)
        self._load_from_action.setIcon(self._open_icon)
        self._load_from_action.setToolTip("Load session from file… (Ctrl+Shift+O)")
        self._load_from_action.triggered.connect(self._load_session_from_action)  # type: ignore[attr-defined]
        tb.addAction(self._load_from_action)

        self._insert_graph_action = QtGui.QAction("Insert Graph…", self)
        self._insert_graph_action.setIcon(self._insert_icon)
        self._insert_graph_action.setToolTip("Insert session graph at cursor… (Ctrl+Shift+I)")
        self._insert_graph_action.triggered.connect(self._insert_graph_from_action)  # type: ignore[attr-defined]
        tb.addAction(self._insert_graph_action)

        self._save_as_action = QtGui.QAction("Save Session As…", self)
        self._save_as_action.setIcon(self._save_icon)
        self._save_as_action.setToolTip("Save session to file… (Ctrl+Shift+S)")
        self._save_as_action.triggered.connect(self._save_session_as_action)  # type: ignore[attr-defined]
        tb.addAction(self._save_as_action)

        tb.addSeparator()

        # Send Graph(F5).
        self._send_icon = icon_for(self, StudioIcon.SEND)
        self._deploy_action.setIcon(self._send_icon)
        self._deploy_action.setToolTip("Send graph to services (F5)")
        tb.addAction(self._deploy_action)
        self._stop_all_services_action.setIcon(icon_for(self, StudioIcon.STOP_ALL))
        self._stop_all_services_action.setToolTip("Stop all service processes in graph")
        tb.addAction(self._stop_all_services_action)

        # Push the edge-visibility toolbar to the far-right in the same top toolbar row.
        toolbar_spacer = QtWidgets.QWidget(tb)
        toolbar_spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(toolbar_spacer)

        edge_tb = QtWidgets.QToolBar("Pipe Visibility", self)
        edge_tb.setObjectName("PipeVisibilityToolBar")
        edge_tb.setMovable(False)
        edge_tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.addToolBar(QtCore.Qt.TopToolBarArea, edge_tb)

        edge_left_spacer = QtWidgets.QWidget(edge_tb)
        edge_left_spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        edge_tb.addWidget(edge_left_spacer)

        edge_label = QtWidgets.QLabel("Pipe Visibility:", edge_tb)
        edge_tb.addWidget(edge_label)
        edge_tb.addSeparator()

        self._exec_lines_action = QtGui.QAction("EXEC", self)
        self._exec_lines_action.setCheckable(True)
        self._exec_lines_action.setChecked(True)
        self._exec_lines_action.toggled.connect(self._on_exec_lines_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(self._exec_lines_action, True)
        edge_tb.addAction(self._exec_lines_action)
        self._set_action_text_beside_icon(edge_tb, self._exec_lines_action, italic=True)

        self._data_lines_action = QtGui.QAction("DATA", self)
        self._data_lines_action.setCheckable(True)
        self._data_lines_action.setChecked(True)
        self._data_lines_action.toggled.connect(self._on_data_lines_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(self._data_lines_action, True)
        edge_tb.addAction(self._data_lines_action)
        self._set_action_text_beside_icon(edge_tb, self._data_lines_action, italic=True)

        self._state_lines_action = QtGui.QAction("STATE", self)
        self._state_lines_action.setCheckable(True)
        self._state_lines_action.setChecked(True)
        self._state_lines_action.toggled.connect(self._on_state_lines_toggled)  # type: ignore[attr-defined]
        self._set_edge_visibility_action_icon(self._state_lines_action, True)
        edge_tb.addAction(self._state_lines_action)
        self._set_action_text_beside_icon(edge_tb, self._state_lines_action, italic=True)

    def _set_action_text_beside_icon(
        self, toolbar: QtWidgets.QToolBar, action: QtGui.QAction, italic: bool = False
    ) -> None:
        action_widget = toolbar.widgetForAction(action)
        if isinstance(action_widget, QtWidgets.QToolButton):
            action_widget.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            if italic:
                font = QtGui.QFont(action_widget.font())
                font.setItalic(True)
                action_widget.setFont(font)

    def _set_edge_visibility_action_icon(self, action: QtGui.QAction, visible: bool) -> None:
        token = StudioIcon.EYE if visible else StudioIcon.EYE_SLASH
        action.setIcon(icon_for(self, token))

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
        self._auto_save_session()
        try:
            self._bridge.stop()
        except Exception as exc:
            self._log_dock.report_exception("studio", "bridge.stop failed", exc)
        super().closeEvent(event)

    def _auto_load_session(self) -> None:
        session_auto_load_session(studio_graph=self.studio_graph, log_dock=self._log_dock)

    def _auto_save_session(self) -> None:
        # Called from both `closeEvent` and `QApplication.aboutToQuit`; guard to avoid double-save on exit.
        self._exit_autosaved = session_auto_save_session(
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            already_saved=self._exit_autosaved,
        )

    def _save_session_action(self) -> None:
        session_save_session(parent=self, studio_graph=self.studio_graph, show_info=show_info)

    def _load_session_action(self) -> None:
        session_load_last_session(
            parent=self,
            studio_graph=self.studio_graph,
            session_file=self._session_file,
            show_info=show_info,
        )

    def _load_session_from_action(self) -> None:
        self._session_dialog_dir = session_load_session_from_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )

    def _save_session_as_action(self) -> None:
        self._session_dialog_dir = session_save_session_as_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )

    def _insert_graph_from_action(self) -> None:
        self._session_dialog_dir = session_insert_graph_from_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )

    def _compile_runtime_action(self) -> None:
        try:
            compiled = compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or "compile failed"
            self._log_dock.append("studio", f"[compile][blocked] {msg}\n")
            show_warning(self, "Compile blocked", msg)
            return
        except Exception as exc:
            self._log_dock.append("studio", f"[compile][error] {exc}\n")
            self._log_dock.report_exception("studio", "compile runtime graph failed", exc)
            show_warning(self, "Compile failed", str(exc))
            return

        payload = compiled.global_graph.model_dump(mode="json", by_alias=True)
        print("\n=== F8Studio RuntimeGraph (global) ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

        print("\n=== F8Studio RuntimeGraph (per-service) ===")
        for sid, g in compiled.per_service.items():
            p = g.model_dump(mode="json", by_alias=True)
            print(f"\n--- serviceId={sid} ---")
            print(json.dumps(p, ensure_ascii=False, indent=2, default=str))

    def _on_deploy_action_triggered(self) -> None:
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
        for warning in list(compiled.warnings or ()):
            self._log_dock.append("studio", f"[compile][warn] {warning}\n")
        self._bridge.deploy(compiled)

    def _on_stop_all_services_triggered(self) -> None:
        service_ids = collect_declared_service_ids(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )

        if not service_ids:
            self._log_dock.append("studio", "[service] no graph service instances to stop\n")
            return

        for service_id in sorted(service_ids):
            try:
                self._bridge.stop_service(service_id)
                self._log_dock.append("studio", f"[service] stop requested: {service_id}\n")
            except Exception as exc:
                self._log_dock.report_exception("studio", f"stop service failed ({service_id})", exc)

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

    def _on_runtime_state_updated(self, service_id: str, node_id: str, field: str, value: Any, ts_ms: Any) -> None:
        self._runtime_state_sync.on_runtime_state_updated(service_id, node_id, field, value, ts_ms)

    def _on_ui_command(self, cmd: UiCommand) -> None:
        if str(cmd.command) == "monitor.update":
            manager = self._service_manager
            if manager is not None:
                manager.queue_refresh()
        if str(cmd.command) == "state.update":
            payload = dict(cmd.payload or {})
            field = str(payload.get("field") or "")
            value = payload.get("value")
            service_id = str(payload.get("serviceId") or "")
            node_id = str(cmd.node_id or "")
            if node_id and field:
                self._on_runtime_state_updated(service_id, node_id, field, value, cmd.ts_ms)
            return

        node_id = str(cmd.node_id or "").strip()
        if not node_id:
            return
        try:
            node = self.studio_graph.get_node_by_id(node_id)
        except Exception:
            node = None
        if node is None:
            return
        try:
            if isinstance(node, UiCommandApplier):
                node.apply_ui_command(cmd)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    def _on_ui_property_changed(self, node: Any, name: str, value: Any) -> None:
        self._runtime_state_sync.on_ui_property_changed(node, name, value)
