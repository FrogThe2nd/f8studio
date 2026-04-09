from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from qtpy import QtCore, QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.codec import coerce_bool  # noqa: E402
from f8pystudio.nodegraph.viewer import F8StudioNodeViewer  # noqa: E402
from f8pystudio.ui.mainwin.main_window import F8StudioMainWin  # noqa: E402


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _process_events() -> None:
    app = _ensure_app()
    app.processEvents()


def _new_settings(settings_path: Path) -> QtCore.QSettings:
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.IniFormat)
    settings.clear()
    settings.sync()
    return settings


class _LayoutHarness(QtWidgets.QMainWindow):
    _WINDOW_LAYOUT_SETTINGS_GROUP = F8StudioMainWin._WINDOW_LAYOUT_SETTINGS_GROUP
    _WINDOW_LAYOUT_STATE_KEY = F8StudioMainWin._WINDOW_LAYOUT_STATE_KEY
    _WINDOW_LAYOUT_GEOMETRY_KEY = F8StudioMainWin._WINDOW_LAYOUT_GEOMETRY_KEY
    _WINDOW_LAYOUT_STATE_VERSION = F8StudioMainWin._WINDOW_LAYOUT_STATE_VERSION
    _LOG_LEVEL_SETTINGS_GROUP = F8StudioMainWin._LOG_LEVEL_SETTINGS_GROUP
    _LOG_LEVEL_SETTINGS_KEY = F8StudioMainWin._LOG_LEVEL_SETTINGS_KEY
    _LOG_LEVEL_CHOICES = F8StudioMainWin._LOG_LEVEL_CHOICES
    _VIEW_SETTINGS_GROUP = F8StudioMainWin._VIEW_SETTINGS_GROUP
    _AUTO_PROXY_ENABLED_SETTINGS_KEY = F8StudioMainWin._AUTO_PROXY_ENABLED_SETTINGS_KEY
    _PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY = F8StudioMainWin._PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY

    _as_qbytearray = staticmethod(F8StudioMainWin._as_qbytearray)
    _coerce_bool_setting = staticmethod(coerce_bool)
    _normalize_supported_log_level = staticmethod(F8StudioMainWin._normalize_supported_log_level)
    _log_level_name_for_value = staticmethod(F8StudioMainWin._log_level_name_for_value)
    _log_level_value_from_name = staticmethod(F8StudioMainWin._log_level_value_from_name)
    _read_layout_bytes = F8StudioMainWin._read_layout_bytes
    _write_layout_bytes = F8StudioMainWin._write_layout_bytes
    _read_saved_log_level_name = F8StudioMainWin._read_saved_log_level_name
    _write_saved_log_level_name = F8StudioMainWin._write_saved_log_level_name
    _apply_log_level = F8StudioMainWin._apply_log_level
    _restore_saved_log_level = F8StudioMainWin._restore_saved_log_level
    _on_log_level_toggled = F8StudioMainWin._on_log_level_toggled
    _create_action = F8StudioMainWin._create_action
    _capture_default_dock_layout_state = F8StudioMainWin._capture_default_dock_layout_state
    _restore_saved_window_layout = F8StudioMainWin._restore_saved_window_layout
    _save_window_layout = F8StudioMainWin._save_window_layout
    _setup_menu = F8StudioMainWin._setup_menu
    _on_reset_layout_triggered = F8StudioMainWin._on_reset_layout_action
    _read_saved_auto_proxy_enabled = F8StudioMainWin._read_saved_auto_proxy_enabled
    _write_saved_auto_proxy_enabled = F8StudioMainWin._write_saved_auto_proxy_enabled
    _apply_auto_proxy_enabled = F8StudioMainWin._apply_auto_proxy_enabled
    _on_auto_proxy_toggled = F8StudioMainWin._on_auto_proxy_toggled
    _read_saved_performance_overlay_enabled = F8StudioMainWin._read_saved_performance_overlay_enabled
    _write_saved_performance_overlay_enabled = F8StudioMainWin._write_saved_performance_overlay_enabled
    _apply_performance_overlay_enabled = F8StudioMainWin._apply_performance_overlay_enabled
    _on_performance_overlay_toggled = F8StudioMainWin._on_performance_overlay_toggled

    class _FakeViewer(F8StudioNodeViewer):
        def __init__(self) -> None:
            super().__init__()
            self.enabled = False
            self.calls: list[bool] = []
            self.auto_proxy_enabled_state = False
            self.auto_proxy_calls: list[bool] = []

        def set_performance_overlay_enabled(self, enabled: bool) -> None:
            super().set_performance_overlay_enabled(enabled)
            self.enabled = self.performance_overlay_enabled()
            self.calls.append(self.enabled)

        def set_auto_proxy_enabled(self, enabled: bool) -> None:
            super().set_auto_proxy_enabled(enabled)
            self.auto_proxy_enabled_state = self.auto_proxy_enabled()
            self.auto_proxy_calls.append(self.auto_proxy_enabled_state)

    class _FakeStudioGraph:
        def __init__(self, viewer: "_LayoutHarness._FakeViewer") -> None:
            self._viewer = viewer

        def viewer(self) -> "_LayoutHarness._FakeViewer":
            return self._viewer

    def __init__(self, settings: QtCore.QSettings) -> None:
        super().__init__(None)
        self._settings = settings
        self.setCentralWidget(QtWidgets.QWidget(self))
        self.resize(1000, 700)

        self._properties_dock = QtWidgets.QDockWidget("Properties", self)
        self._properties_dock.setObjectName("PropertiesDock")
        self._properties_dock.setWidget(QtWidgets.QLabel("properties", self._properties_dock))
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self._properties_dock)

        self._log_dock = QtWidgets.QDockWidget("Service Logs", self)
        self._log_dock.setObjectName("ServiceLogsDock")
        self._log_dock.setWidget(QtWidgets.QLabel("logs", self._log_dock))
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._log_dock)

        self._node_library_dock = QtWidgets.QDockWidget("Node Library", self)
        self._node_library_dock.setObjectName("NodeLibraryDock")
        self._node_library_dock.setWidget(QtWidgets.QLabel("library", self._node_library_dock))
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self._node_library_dock)

        self._service_manager_dock = QtWidgets.QDockWidget("Service Manager", self)
        self._service_manager_dock.setObjectName("ServiceManagerDock")
        self._service_manager_dock.setWidget(QtWidgets.QLabel("manager", self._service_manager_dock))
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._service_manager_dock)
        self.tabifyDockWidget(self._log_dock, self._service_manager_dock)

        self._dock_widgets = [
            self._properties_dock,
            self._log_dock,
            self._node_library_dock,
            self._service_manager_dock,
        ]
        self._log_level_actions = {}
        self._default_dock_layout_state = QtCore.QByteArray()
        self._auto_proxy_enabled = self._read_saved_auto_proxy_enabled()
        self._performance_overlay_enabled = self._read_saved_performance_overlay_enabled()
        self._fake_viewer = self._FakeViewer()
        self.studio_graph = self._FakeStudioGraph(self._fake_viewer)
        self._open_project_action = self._create_action("Open Project", handler=lambda: None)
        self._quicksave_project_action = self._create_action("Quick Save", handler=lambda: None)
        self._save_project_as_action = self._create_action("Save Project As", handler=lambda: None)
        self._auto_save_action = self._create_action(
            "Auto Save",
            handler=lambda _checked: None,
            checkable=True,
            checked=False,
        )
        self._project_history_action = self._create_action("Project History", handler=lambda: None)
        self._insert_component_action = self._create_action("Insert Component", handler=lambda: None)
        self._save_component_action = self._create_action("Save Component", handler=lambda: None)
        self._import_project_json_action = self._create_action("Import Project JSON", handler=lambda: None)
        self._export_project_json_action = self._create_action("Export Project JSON", handler=lambda: None)
        self._export_published_session_action = self._create_action(
            "Export Published Session",
            handler=lambda: None,
        )
        self._deploy_action = self._create_action("Deploy", handler=lambda: None)
        self._stop_all_services_action = self._create_action("Stop All Services", handler=lambda: None)
        self._auto_deploy_action = self._create_action(
            "Auto Deploy",
            handler=lambda _checked: None,
            checkable=True,
            checked=False,
        )
        self._manage_components_action = self._create_action("Manage Components", handler=lambda: None)
        self._variant_catalog_action = self._create_action("Variant Catalog", handler=lambda: None)
        self._global_hotkeys_action = self._create_action("Global Hotkeys", handler=lambda: None)
        self._auto_proxy_action = self._create_action(
            "Auto Proxy",
            handler=self._on_auto_proxy_toggled,
            checkable=True,
            checked=self._auto_proxy_enabled,
        )
        self._performance_overlay_action = self._create_action(
            "Performance Overlay",
            handler=self._on_performance_overlay_toggled,
            checkable=True,
            checked=self._performance_overlay_enabled,
        )

    def _layout_settings(self) -> QtCore.QSettings:
        return self._settings

    def _setup_view_menu(self) -> None:
        self._setup_menu()

    def _setup_log_level_menu(self) -> None:
        self._setup_menu()


def test_saved_dock_layout_is_restored_from_settings(tmp_path: Path) -> None:
    _ensure_app()
    settings_path = tmp_path / "studio-layout.ini"
    writer_settings = _new_settings(settings_path)
    writer = _LayoutHarness(writer_settings)
    writer.show()
    _process_events()
    writer._capture_default_dock_layout_state()
    writer._setup_view_menu()
    writer._node_library_dock.hide()
    _process_events()
    writer._save_window_layout()
    writer.close()

    reader_settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.IniFormat)
    reader = _LayoutHarness(reader_settings)
    reader.show()
    _process_events()
    reader._capture_default_dock_layout_state()
    reader._restore_saved_window_layout()
    _process_events()

    assert reader._node_library_dock.isVisible() is False


def test_view_menu_actions_are_checkable_and_reset_restores_defaults(tmp_path: Path) -> None:
    _ensure_app()
    settings = _new_settings(tmp_path / "studio-layout.ini")
    window = _LayoutHarness(settings)
    window.show()
    _process_events()
    window._capture_default_dock_layout_state()
    window._setup_view_menu()

    menu_actions = [action for action in window._view_menu.actions() if not action.isSeparator()]
    for dock in window._dock_widgets:
        toggle_action = dock.toggleViewAction()
        assert toggle_action in menu_actions
        assert toggle_action.isCheckable() is True
    assert window._auto_proxy_action in menu_actions
    assert window._auto_proxy_action.isCheckable() is True
    assert window._performance_overlay_action in menu_actions
    assert window._performance_overlay_action.isCheckable() is True
    assert window._reset_layout_action in menu_actions

    window._node_library_dock.hide()
    _process_events()
    assert window._node_library_dock.isVisible() is False

    window._on_reset_layout_triggered()
    _process_events()
    assert window._node_library_dock.isVisible() is True

    saved_state = window._read_layout_bytes(key=window._WINDOW_LAYOUT_STATE_KEY)
    assert saved_state is not None
    assert saved_state == window.saveState(window._WINDOW_LAYOUT_STATE_VERSION)


def test_performance_overlay_setting_is_applied_and_restored(tmp_path: Path) -> None:
    _ensure_app()
    settings = _new_settings(tmp_path / "studio-layout.ini")
    window = _LayoutHarness(settings)
    window._setup_view_menu()

    assert window._performance_overlay_action.isChecked() is False
    assert window._fake_viewer.enabled is False

    window._performance_overlay_action.trigger()
    _process_events()

    assert window._performance_overlay_action.isChecked() is True
    assert window._fake_viewer.enabled is True
    assert window._read_saved_performance_overlay_enabled() is True

    restored = _LayoutHarness(QtCore.QSettings(str(tmp_path / "studio-layout.ini"), QtCore.QSettings.IniFormat))
    assert restored._performance_overlay_enabled is True


def test_auto_proxy_setting_is_applied_and_restored(tmp_path: Path) -> None:
    _ensure_app()
    settings = _new_settings(tmp_path / "studio-layout.ini")
    window = _LayoutHarness(settings)
    window._setup_view_menu()

    assert window._auto_proxy_action.isChecked() is False
    assert window._fake_viewer.auto_proxy_enabled_state is False

    window._auto_proxy_action.trigger()
    _process_events()

    assert window._auto_proxy_action.isChecked() is True
    assert window._fake_viewer.auto_proxy_enabled_state is True
    assert window._read_saved_auto_proxy_enabled() is True

    restored = _LayoutHarness(QtCore.QSettings(str(tmp_path / "studio-layout.ini"), QtCore.QSettings.IniFormat))
    assert restored._auto_proxy_enabled is True


def test_log_level_menu_applies_and_restores_saved_level(tmp_path: Path) -> None:
    _ensure_app()
    settings = _new_settings(tmp_path / "studio-layout.ini")
    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        root_logger.setLevel(logging.WARNING)
        window = _LayoutHarness(settings)
        window._setup_log_level_menu()
        warning_action = window._log_level_actions[logging.WARNING]
        assert warning_action.isChecked() is True

        debug_action = window._log_level_actions[logging.DEBUG]
        debug_action.trigger()
        _process_events()

        assert root_logger.level == logging.DEBUG
        assert window._read_saved_log_level_name() == "DEBUG"

        root_logger.setLevel(logging.WARNING)
        window_restore = _LayoutHarness(settings)
        window_restore._restore_saved_log_level()
        window_restore._setup_log_level_menu()
        assert root_logger.level == logging.DEBUG
        assert window_restore._log_level_actions[logging.DEBUG].isChecked() is True
    finally:
        root_logger.setLevel(original_level)
