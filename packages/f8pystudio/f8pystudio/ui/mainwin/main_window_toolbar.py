from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from qtpy import QtCore, QtGui, QtWidgets

from ...ui.support.ui_icons import StudioIcon, icon_for


@dataclass(frozen=True)
class MainWindowToolbarBundle:
    run_toolbar: QtWidgets.QToolBar
    dock_toolbar: QtWidgets.QToolBar
    edge_toolbar: QtWidgets.QToolBar
    account_toolbar: QtWidgets.QToolBar
    account_button: QtWidgets.QToolButton
    exec_lines_action: QtGui.QAction
    data_lines_action: QtGui.QAction
    state_lines_action: QtGui.QAction


def _add_expanding_spacer(toolbar: QtWidgets.QToolBar) -> None:
    spacer = QtWidgets.QWidget(toolbar)
    spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
    toolbar.addWidget(spacer)


def set_action_text_beside_icon(
    toolbar: QtWidgets.QToolBar,
    action: QtGui.QAction,
    *,
    italic: bool = False,
) -> None:
    action_widget = toolbar.widgetForAction(action)
    if not isinstance(action_widget, QtWidgets.QToolButton):
        return
    action_widget.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if italic:
        font = QtGui.QFont(action_widget.font())
        font.setItalic(True)
        action_widget.setFont(font)


def set_edge_visibility_action_icon(
    icon_owner: QtWidgets.QWidget,
    action: QtGui.QAction,
    *,
    visible: bool,
) -> None:
    token = StudioIcon.EYE if visible else StudioIcon.EYE_SLASH
    action.setIcon(icon_for(icon_owner, token))


def build_main_window_toolbars(
    parent: QtWidgets.QMainWindow,
    *,
    graph_actions: Sequence[QtGui.QAction],
    deploy_actions: Sequence[QtGui.QAction],
    dock_actions: Sequence[QtGui.QAction],
    account_clicked: Callable[[], None],
    exec_toggled: Callable[[bool], None],
    data_toggled: Callable[[bool], None],
    state_toggled: Callable[[bool], None],
) -> MainWindowToolbarBundle:
    run_toolbar = QtWidgets.QToolBar("Run", parent)
    run_toolbar.setObjectName("RunToolBar")
    run_toolbar.setMovable(False)
    run_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    parent.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, run_toolbar)

    for action in graph_actions:
        run_toolbar.addAction(action)
    run_toolbar.addSeparator()
    for action in deploy_actions:
        run_toolbar.addAction(action)

    dock_toolbar = QtWidgets.QToolBar("Dock Widgets", parent)
    dock_toolbar.setObjectName("DockWidgetsToolBar")
    dock_toolbar.setMovable(False)
    dock_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    parent.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, dock_toolbar)

    for action in dock_actions:
        dock_toolbar.addAction(action)

    edge_toolbar = QtWidgets.QToolBar("Link Visibility", parent)
    edge_toolbar.setObjectName("PipeVisibilityToolBar")
    edge_toolbar.setMovable(False)
    edge_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    parent.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, edge_toolbar)

    exec_lines_action = QtGui.QAction("EXEC", parent)
    exec_lines_action.setCheckable(True)
    exec_lines_action.setChecked(True)
    exec_lines_action.toggled.connect(exec_toggled)  # type: ignore[attr-defined]
    set_edge_visibility_action_icon(parent, exec_lines_action, visible=True)
    edge_toolbar.addAction(exec_lines_action)
    set_action_text_beside_icon(edge_toolbar, exec_lines_action, italic=True)

    data_lines_action = QtGui.QAction("DATA", parent)
    data_lines_action.setCheckable(True)
    data_lines_action.setChecked(True)
    data_lines_action.toggled.connect(data_toggled)  # type: ignore[attr-defined]
    set_edge_visibility_action_icon(parent, data_lines_action, visible=True)
    edge_toolbar.addAction(data_lines_action)
    set_action_text_beside_icon(edge_toolbar, data_lines_action, italic=True)

    state_lines_action = QtGui.QAction("STATE", parent)
    state_lines_action.setCheckable(True)
    state_lines_action.setChecked(True)
    state_lines_action.toggled.connect(state_toggled)  # type: ignore[attr-defined]
    set_edge_visibility_action_icon(parent, state_lines_action, visible=True)
    edge_toolbar.addAction(state_lines_action)
    set_action_text_beside_icon(edge_toolbar, state_lines_action, italic=True)

    spacer_toolbar = QtWidgets.QToolBar("ToolbarSpacer", parent)
    spacer_toolbar.setObjectName("ToolbarSpacerToolBar")
    spacer_toolbar.setMovable(False)
    parent.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, spacer_toolbar)
    _add_expanding_spacer(edge_toolbar)

    account_toolbar = QtWidgets.QToolBar("Account", parent)
    account_toolbar.setObjectName("AssetCloudAccountToolBar")
    account_toolbar.setMovable(False)
    account_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    parent.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, account_toolbar)

    _add_expanding_spacer(spacer_toolbar)
    _add_expanding_spacer(account_toolbar)

    account_button = QtWidgets.QToolButton(account_toolbar)
    account_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    account_button.clicked.connect(account_clicked)  # type: ignore[attr-defined]
    account_toolbar.addWidget(account_button)

    return MainWindowToolbarBundle(
        run_toolbar=run_toolbar,
        dock_toolbar=dock_toolbar,
        edge_toolbar=edge_toolbar,
        account_toolbar=account_toolbar,
        account_button=account_button,
        exec_lines_action=exec_lines_action,
        data_lines_action=data_lines_action,
        state_lines_action=state_lines_action,
    )


def refresh_asset_cloud_account_button(
    button: QtWidgets.QToolButton,
    *,
    username: str | None,
    display_name: str | None,
    signed_in: bool,
) -> None:
    if not signed_in:
        button.setText("")
        button.setIcon(icon_for(button, StudioIcon.USER_OFF))
        button.setToolTip("Manage Feel8 asset cloud accounts")
        return

    button.setText("")
    button.setIcon(icon_for(button, StudioIcon.USER))
    account_name = str(username or display_name or "")
    if account_name:
        button.setToolTip(f"Manage Feel8 asset cloud account ({account_name})")
    else:
        button.setToolTip("Manage Feel8 asset cloud account")
