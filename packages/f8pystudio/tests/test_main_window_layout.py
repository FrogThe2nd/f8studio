from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
    _AUTOMATION_SETTINGS_GROUP = F8StudioMainWin._AUTOMATION_SETTINGS_GROUP
    _KILL_MANAGED_SERVICES_ON_EXIT_SETTINGS_KEY = F8StudioMainWin._KILL_MANAGED_SERVICES_ON_EXIT_SETTINGS_KEY
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
    _sync_log_level_actions = F8StudioMainWin._sync_log_level_actions
    _apply_log_level = F8StudioMainWin._apply_log_level
    _restore_saved_log_level = F8StudioMainWin._restore_saved_log_level
    _on_log_level_toggled = F8StudioMainWin._on_log_level_toggled
    _create_action = F8StudioMainWin._create_action
    _capture_default_dock_layout_state = F8StudioMainWin._capture_default_dock_layout_state
    _restore_saved_window_layout = F8StudioMainWin._restore_saved_window_layout
    _save_window_layout = F8StudioMainWin._save_window_layout
    _add_menu_section = F8StudioMainWin._add_menu_section
    _build_view_menu = F8StudioMainWin._build_view_menu
    _build_log_level_menu = F8StudioMainWin._build_log_level_menu
    _setup_menu = F8StudioMainWin._setup_menu
    _on_reset_layout_triggered = F8StudioMainWin._on_reset_layout_action
    _read_saved_auto_proxy_enabled = F8StudioMainWin._read_saved_auto_proxy_enabled
    _write_saved_auto_proxy_enabled = F8StudioMainWin._write_saved_auto_proxy_enabled
    _apply_auto_proxy_enabled = F8StudioMainWin._apply_auto_proxy_enabled
    _on_auto_proxy_toggled = F8StudioMainWin._on_auto_proxy_toggled
    _read_saved_kill_managed_services_on_exit_enabled = (
        F8StudioMainWin._read_saved_kill_managed_services_on_exit_enabled
    )
    _write_saved_kill_managed_services_on_exit_enabled = (
        F8StudioMainWin._write_saved_kill_managed_services_on_exit_enabled
    )
    _apply_kill_managed_services_on_exit_enabled = F8StudioMainWin._apply_kill_managed_services_on_exit_enabled
    _on_kill_managed_services_on_exit_toggled = F8StudioMainWin._on_kill_managed_services_on_exit_toggled
    _read_saved_performance_overlay_enabled = F8StudioMainWin._read_saved_performance_overlay_enabled
    _write_saved_performance_overlay_enabled = F8StudioMainWin._write_saved_performance_overlay_enabled
    _apply_performance_overlay_enabled = F8StudioMainWin._apply_performance_overlay_enabled
    _on_performance_overlay_toggled = F8StudioMainWin._on_performance_overlay_toggled
    _replace_dock_widget = F8StudioMainWin._replace_dock_widget
    _ensure_node_library_widget = F8StudioMainWin._ensure_node_library_widget
    rebuild_asset_search_sources = F8StudioMainWin.rebuild_asset_search_sources
    _on_asset_cache_changed = F8StudioMainWin._on_asset_cache_changed
    _clear_asset_cache_changed_subscription = F8StudioMainWin._clear_asset_cache_changed_subscription
    _build_main_window_toolbars = F8StudioMainWin._build_main_window_toolbars
    _add_expanding_spacer = F8StudioMainWin._add_expanding_spacer
    _set_action_text_beside_icon = F8StudioMainWin._set_action_text_beside_icon
    _set_edge_visibility_action_icon = F8StudioMainWin._set_edge_visibility_action_icon

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

    class _FakeLogDock(QtWidgets.QDockWidget):
        def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
            super().__init__(title, parent)
            self.minimum_level = logging.getLogger().getEffectiveLevel()

        def set_minimum_level(self, level: int) -> None:
            self.minimum_level = int(level)

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

        self._log_dock = self._FakeLogDock("Service Logs", self)
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
        self._bridge = SimpleNamespace(set_kill_managed_services_on_exit=lambda _enabled: None)
        self._kill_managed_services_on_exit_enabled = self._read_saved_kill_managed_services_on_exit_enabled()
        self._auto_proxy_enabled = self._read_saved_auto_proxy_enabled()
        self._performance_overlay_enabled = self._read_saved_performance_overlay_enabled()
        self._node_library_widget = None
        self._unsubscribe_asset_cache_changed = None
        self._fake_viewer = self._FakeViewer()
        self.studio_graph = self._FakeStudioGraph(self._fake_viewer)
        self._open_project_action = self._create_action("Manage Projects", handler=lambda: None)
        self._quicksave_project_action = self._create_action("Quick Save", handler=lambda: None)
        self._save_project_as_action = self._create_action("Save Project As", handler=lambda: None)
        self._clear_all_nodes_action = self._create_action("Clear All Nodes", handler=lambda: None)
        self._auto_save_action = self._create_action(
            "Auto Save",
            handler=lambda _checked: None,
            checkable=True,
            checked=False,
        )
        self._project_history_action = self._create_action("Project History", handler=lambda: None)
        self._save_component_action = self._create_action("Export to Component", handler=lambda: None)
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
        self._kill_managed_services_on_exit_action = self._create_action(
            "Stop managed services on shutdown",
            handler=self._on_kill_managed_services_on_exit_toggled,
            checkable=True,
            checked=self._kill_managed_services_on_exit_enabled,
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

    def _ordered_view_docks(self) -> list[QtWidgets.QDockWidget]:
        return list(self._dock_widgets)

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


def test_asset_cache_changed_rebuilds_graph_and_node_library(tmp_path: Path) -> None:
    _ensure_app()
    harness = _LayoutHarness(_new_settings(tmp_path / "asset-refresh.ini"))
    calls: list[str] = []
    harness.studio_graph = SimpleNamespace(rebuild_asset_search_sources=lambda: calls.append("graph"))
    harness._node_library_widget = SimpleNamespace(rebuild_asset_search_sources=lambda: calls.append("library"))

    harness._on_asset_cache_changed()

    assert calls == ["graph", "library"]


def test_ensure_node_library_widget_uses_main_window_refresh_coordinator(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    harness = _LayoutHarness(_new_settings(tmp_path / "asset-refresh-widget.ini"))
    calls: list[str] = []
    created: dict[str, object] = {}

    class _FakeNodeLibraryWidget(QtWidgets.QWidget):
        def __init__(self, parent=None, node_graph=None, *, asset_cache_auto_refresh=True) -> None:
            super().__init__(parent)
            created["node_graph"] = node_graph
            created["asset_cache_auto_refresh"] = asset_cache_auto_refresh

        def rebuild_asset_search_sources(self) -> None:
            calls.append("library")

    harness.studio_graph = SimpleNamespace(rebuild_asset_search_sources=lambda: calls.append("graph"))
    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window_ui_mixin.F8StudioNodeLibraryWidget", _FakeNodeLibraryWidget)

    harness._ensure_node_library_widget()

    assert created["node_graph"] is harness.studio_graph
    assert created["asset_cache_auto_refresh"] is False
    assert calls == ["graph", "library"]


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


def test_file_menu_exposes_export_to_component(tmp_path: Path) -> None:
    _ensure_app()
    window = _LayoutHarness(_new_settings(tmp_path / "studio-file-menu.ini"))
    window._setup_menu()

    file_menu_action = window.menuBar().actions()[0]
    file_menu = file_menu_action.menu()
    assert file_menu is not None

    action_texts = [action.text() for action in file_menu.actions() if not action.isSeparator()]
    assert action_texts == [
        "Manage Projects",
        "Quick Save",
        "Save Project As",
        "Clear All Nodes",
        "Auto Save",
        "Project History",
        "Import Project JSON",
        "Export Project JSON",
        "Export to Component",
        "Export Published Session",
    ]
    assert window._save_component_action in file_menu.actions()


def test_run_toolbar_places_global_hotkeys_after_variant_catalog(tmp_path: Path) -> None:
    _ensure_app()
    window = _LayoutHarness(_new_settings(tmp_path / "studio-toolbar.ini"))

    window._build_main_window_toolbars(
        graph_actions=[
            window._open_project_action,
            window._quicksave_project_action,
            window._save_project_as_action,
            window._clear_all_nodes_action,
            window._project_history_action,
            window._manage_components_action,
            window._variant_catalog_action,
            window._global_hotkeys_action,
        ],
        deploy_actions=[
            window._deploy_action,
            window._stop_all_services_action,
            window._auto_deploy_action,
        ],
        dock_actions=[],
        account_clicked=lambda: None,
        exec_toggled=lambda _checked: None,
        data_toggled=lambda _checked: None,
        state_toggled=lambda _checked: None,
    )

    toolbar = window.findChild(QtWidgets.QToolBar, "RunToolBar")
    assert toolbar is not None
    action_texts_before_separator: list[str] = []
    for action in toolbar.actions():
        if action.isSeparator():
            break
        action_texts_before_separator.append(action.text())

    assert action_texts_before_separator == [
        "Manage Projects",
        "Quick Save",
        "Save Project As",
        "Clear All Nodes",
        "Project History",
        "Manage Components",
        "Variant Catalog",
        "Global Hotkeys",
    ]


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


def test_restore_saved_log_level_updates_existing_menu_selection(tmp_path: Path) -> None:
    _ensure_app()
    settings = _new_settings(tmp_path / "studio-layout.ini")
    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        root_logger.setLevel(logging.WARNING)
        writer = _LayoutHarness(settings)
        writer._write_saved_log_level_name(level_name="INFO")

        restored = _LayoutHarness(QtCore.QSettings(str(tmp_path / "studio-layout.ini"), QtCore.QSettings.IniFormat))
        restored._setup_log_level_menu()

        assert restored._log_level_actions[logging.WARNING].isChecked() is True

        restored._restore_saved_log_level()

        assert root_logger.level == logging.INFO
        assert restored._log_level_actions[logging.INFO].isChecked() is True
        assert restored._log_dock.minimum_level == logging.INFO
    finally:
        root_logger.setLevel(original_level)
