from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from qtpy import QtGui

from ...ui.support.ui_icons import StudioIcon


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
    insert_component_action: QtGui.QAction
    auto_save_action: QtGui.QAction
    auto_deploy_action: QtGui.QAction
    performance_overlay_action: QtGui.QAction
    auto_proxy_action: QtGui.QAction
    export_published_session_action: QtGui.QAction
    clear_all_nodes_action: QtGui.QAction
    deploy_action: QtGui.QAction
    stop_all_services_action: QtGui.QAction
    global_hotkeys_action: QtGui.QAction


def build_main_window_actions(
    *,
    create_action: Callable[..., QtGui.QAction],
    auto_save_enabled: bool,
    auto_deploy_enabled: bool,
    performance_overlay_enabled: bool,
    auto_proxy_enabled: bool,
    on_quickload_project_action: Callable[[], None],
    on_quicksave_project_action: Callable[[], None],
    on_open_project_action: Callable[[], None],
    on_import_project_json_action: Callable[[], None],
    on_import_graph_action: Callable[[], None],
    on_save_project_as_action: Callable[[], None],
    on_export_project_json_action: Callable[[], None],
    on_project_history_action: Callable[[], None],
    on_save_component_action: Callable[[], None],
    on_manage_components_action: Callable[[], None],
    on_insert_component_action: Callable[[], None],
    on_auto_save_toggled: Callable[[bool], None],
    on_auto_deploy_toggled: Callable[[bool], None],
    on_performance_overlay_toggled: Callable[[bool], None],
    on_auto_proxy_toggled: Callable[[bool], None],
    on_export_published_session_action: Callable[[], None],
    on_clear_all_nodes_action: Callable[[], None],
    on_deploy_action: Callable[[], None],
    on_stop_all_services_action: Callable[[], None],
    on_global_hotkeys_action: Callable[[], None],
) -> MainWindowActionBundle:
    return MainWindowActionBundle(
        quickload_project_action=create_action(
            "Load Last Project",
            handler=on_quickload_project_action,
            icon=StudioIcon.FOLDER_OPEN,
            tool_tip="Load the most recent project",
        ),
        quicksave_project_action=create_action(
            "Save Project",
            handler=on_quicksave_project_action,
            shortcut="Ctrl+S",
            icon=StudioIcon.SAVE,
            tool_tip="Save the current graph to the current project",
        ),
        open_project_action=create_action(
            "Open Project…",
            handler=on_open_project_action,
            shortcut="Ctrl+O",
            icon=StudioIcon.FOLDER_OPEN,
            tool_tip="Open a project from the local project catalog",
        ),
        import_project_json_action=create_action(
            "Import Project JSON…",
            handler=on_import_project_json_action,
            icon=StudioIcon.FOLDER_PLUS,
            tool_tip="Import a JSON session file into the local project store",
        ),
        import_graph_action=create_action(
            "Insert Graph JSON…",
            handler=on_import_graph_action,
            shortcut="Ctrl+Shift+I",
            icon=StudioIcon.PACKAGE_IMPORT,
            tool_tip="Insert a session JSON file into the current graph as a copied snapshot",
        ),
        save_project_as_action=create_action(
            "Save Project As…",
            handler=on_save_project_as_action,
            shortcut="Ctrl+Shift+S",
            icon=StudioIcon.SAVE,
            tool_tip="Save the current graph as a new local project",
        ),
        export_project_json_action=create_action(
            "Export Project JSON…",
            handler=on_export_project_json_action,
            icon=StudioIcon.PACKAGE_EXPORT,
            tool_tip="Export the current graph as a full session JSON file",
        ),
        project_history_action=create_action(
            "Project History…",
            handler=on_project_history_action,
            icon=StudioIcon.ARTICLE,
            tool_tip="Browse local project versions and restore an older snapshot as the latest version",
        ),
        save_component_action=create_action(
            "Save As Component…",
            handler=on_save_component_action,
            icon=StudioIcon.PACKAGE_EXPORT,
            tool_tip="Create a publish-safe component from the current graph",
        ),
        manage_components_action=create_action(
            "Components…",
            handler=on_manage_components_action,
            icon=StudioIcon.PACKAGE_IMPORT,
            tool_tip="Browse local and remote reusable graph components",
        ),
        insert_component_action=create_action(
            "Insert Component…",
            handler=on_insert_component_action,
            icon=StudioIcon.PACKAGE_IMPORT,
            tool_tip="Quickly browse reusable components and insert one into the current graph",
        ),
        auto_save_action=create_action(
            "Auto Save",
            handler=on_auto_save_toggled,
            tool_tip="Auto save project changes after graph edits",
            checkable=True,
            checked=auto_save_enabled,
        ),
        auto_deploy_action=create_action(
            "Auto Deploy",
            handler=on_auto_deploy_toggled,
            icon=StudioIcon.AUTOMATION,
            tool_tip="Auto deploy after graph edits (2s debounce)",
            checkable=True,
            checked=auto_deploy_enabled,
        ),
        performance_overlay_action=create_action(
            "Performance Overlay",
            handler=on_performance_overlay_toggled,
            shortcut="Ctrl+Shift+P",
            tool_tip="Show graph viewer paint/perf overlay",
            checkable=True,
            checked=performance_overlay_enabled,
        ),
        auto_proxy_action=create_action(
            "Auto Proxy",
            handler=on_auto_proxy_toggled,
            shortcut="Ctrl+Shift+O",
            tool_tip="Enable zoom-out auto proxy mode for service nodes",
            checkable=True,
            checked=auto_proxy_enabled,
        ),
        export_published_session_action=create_action(
            "Export Publish JSON…",
            handler=on_export_published_session_action,
            icon=StudioIcon.PACKAGE_EXPORT,
            tool_tip="Export a publish-safe component JSON with redacted sensitive state",
        ),
        clear_all_nodes_action=create_action(
            "Clear All Nodes",
            handler=on_clear_all_nodes_action,
            icon=StudioIcon.TRASH,
            tool_tip="Clear all nodes from graph",
        ),
        deploy_action=create_action(
            "Deploy Graph",
            handler=on_deploy_action,
            shortcut="F5",
            icon=StudioIcon.SEND,
            tool_tip="Deploy rungraph",
        ),
        stop_all_services_action=create_action(
            "Stop All Services",
            handler=on_stop_all_services_action,
            icon=StudioIcon.STOP_ALL,
            tool_tip="Stop all services",
        ),
        global_hotkeys_action=create_action(
            "Global Hotkeys…",
            handler=on_global_hotkeys_action,
            icon=StudioIcon.KEYBOARD,
            tool_tip="Show the current global hotkey registry",
        ),
    )
