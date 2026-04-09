from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from qtpy import QtGui, QtWidgets


@dataclass(frozen=True)
class MainWindowFileMenuSections:
    project_actions: Sequence[QtGui.QAction]
    component_actions: Sequence[QtGui.QAction]
    import_export_actions: Sequence[QtGui.QAction]


@dataclass(frozen=True)
class MainWindowFileMenuBundle:
    file_menu: QtWidgets.QMenu


@dataclass(frozen=True)
class MainWindowDeployMenuSections:
    deploy_actions: Sequence[QtGui.QAction]


@dataclass(frozen=True)
class MainWindowDeployMenuBundle:
    deploy_menu: QtWidgets.QMenu


@dataclass(frozen=True)
class MainWindowToolsMenuSections:
    catalog_actions: Sequence[QtGui.QAction]
    log_level_menu: QtWidgets.QMenu


@dataclass(frozen=True)
class MainWindowToolsMenuBundle:
    tools_menu: QtWidgets.QMenu


@dataclass(frozen=True)
class MainWindowViewMenuBundle:
    view_menu: QtWidgets.QMenu
    reset_layout_action: QtGui.QAction


@dataclass(frozen=True)
class MainWindowLogMenuBundle:
    log_level_menu: QtWidgets.QMenu
    log_level_action_group: QtGui.QActionGroup
    log_level_actions: dict[int, QtGui.QAction]


def build_file_menu(
    parent: QtWidgets.QMainWindow,
    *,
    sections: MainWindowFileMenuSections,
) -> MainWindowFileMenuBundle:
    file_menu = parent.menuBar().addMenu("File")
    _add_menu_section(file_menu, sections.project_actions)
    _add_menu_section(file_menu, sections.component_actions)
    _add_menu_section(file_menu, sections.import_export_actions)
    return MainWindowFileMenuBundle(file_menu=file_menu)


def build_deploy_menu(
    parent: QtWidgets.QMainWindow,
    *,
    sections: MainWindowDeployMenuSections,
) -> MainWindowDeployMenuBundle:
    deploy_menu = parent.menuBar().addMenu("Deploy")
    _add_menu_section(deploy_menu, sections.deploy_actions)
    return MainWindowDeployMenuBundle(deploy_menu=deploy_menu)


def build_tools_menu(
    parent: QtWidgets.QMainWindow,
    *,
    sections: MainWindowToolsMenuSections,
) -> MainWindowToolsMenuBundle:
    tools_menu = parent.menuBar().addMenu("Tools")
    _add_menu_section(tools_menu, sections.catalog_actions)
    if sections.log_level_menu is not None:
        tools_menu.addSeparator()
        tools_menu.addMenu(sections.log_level_menu)
    return MainWindowToolsMenuBundle(tools_menu=tools_menu)


def _add_menu_section(menu: QtWidgets.QMenu, actions: Sequence[QtGui.QAction]) -> None:
    section_actions = list(actions)
    if not section_actions:
        return
    if menu.actions():
        menu.addSeparator()
    for action in section_actions:
        menu.addAction(action)


def build_view_menu(
    parent: QtWidgets.QMainWindow,
    *,
    dock_widgets: Sequence[QtWidgets.QDockWidget],
    auto_proxy_action: QtGui.QAction,
    performance_overlay_action: QtGui.QAction,
    on_reset_layout: Callable[[], None],
) -> MainWindowViewMenuBundle:
    view_menu = parent.menuBar().addMenu("View")
    for dock in dock_widgets:
        action = dock.toggleViewAction()
        action.setCheckable(True)
        view_menu.addAction(action)
    view_menu.addSeparator()
    view_menu.addAction(auto_proxy_action)
    view_menu.addAction(performance_overlay_action)
    view_menu.addSeparator()

    reset_layout_action = QtGui.QAction("Reset Layout", parent)
    reset_layout_action.triggered.connect(on_reset_layout)  # type: ignore[attr-defined]
    view_menu.addAction(reset_layout_action)
    return MainWindowViewMenuBundle(
        view_menu=view_menu,
        reset_layout_action=reset_layout_action,
    )


def build_log_level_menu(
    parent: QtWidgets.QMainWindow,
    *,
    choices: Sequence[tuple[str, int]],
    current_level: int,
    on_level_toggled: Callable[[bool, int], None],
) -> MainWindowLogMenuBundle:
    log_level_menu = QtWidgets.QMenu("Log Level", parent)
    log_level_action_group = QtGui.QActionGroup(parent)
    log_level_action_group.setExclusive(True)
    log_level_actions: dict[int, QtGui.QAction] = {}

    for level_name, level_value in choices:
        action = QtGui.QAction(level_name, parent)
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
