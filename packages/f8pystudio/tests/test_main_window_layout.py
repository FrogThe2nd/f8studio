from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from qtpy import QtCore, QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.widgets.main_window import F8StudioMainWin  # noqa: E402


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

    _as_qbytearray = staticmethod(F8StudioMainWin._as_qbytearray)
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
    _capture_default_dock_layout_state = F8StudioMainWin._capture_default_dock_layout_state
    _restore_saved_window_layout = F8StudioMainWin._restore_saved_window_layout
    _save_window_layout = F8StudioMainWin._save_window_layout
    _setup_view_menu = F8StudioMainWin._setup_view_menu
    _setup_log_level_menu = F8StudioMainWin._setup_log_level_menu
    _on_reset_layout_triggered = F8StudioMainWin._on_reset_layout_triggered

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

    def _layout_settings(self) -> QtCore.QSettings:
        return self._settings


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
