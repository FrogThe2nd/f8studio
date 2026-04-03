from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtCore, QtWidgets


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


def add_dock_widget(
    parent: QtWidgets.QMainWindow,
    *,
    title: str,
    object_name: str,
    widget: QtWidgets.QWidget,
    area: QtCore.Qt.DockWidgetArea,
) -> QtWidgets.QDockWidget:
    dock = QtWidgets.QDockWidget(title, parent)
    dock.setObjectName(object_name)
    dock.setWidget(widget)
    parent.addDockWidget(area, dock)
    return dock


def build_main_window_docks(
    parent: QtWidgets.QMainWindow,
    *,
    properties_widget: QtWidgets.QWidget,
    log_dock: QtWidgets.QDockWidget,
    node_library_widget: QtWidgets.QWidget,
    layers_widget: QtWidgets.QWidget,
    ai_assist_widget: QtWidgets.QWidget,
) -> MainWindowDockBundle:
    properties_dock = add_dock_widget(
        parent,
        title="Properties",
        object_name="PropertiesDock",
        widget=properties_widget,
        area=QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    parent.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

    node_library_dock = add_dock_widget(
        parent,
        title="Node Library",
        object_name="NodeLibraryDock",
        widget=node_library_widget,
        area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
    )
    layers_dock = add_dock_widget(
        parent,
        title="Layers",
        object_name="LayersDock",
        widget=layers_widget,
        area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
    )
    parent.tabifyDockWidget(node_library_dock, layers_dock)

    ai_assist_dock = add_dock_widget(
        parent,
        title="AI Assist",
        object_name="AiAssistDock",
        widget=ai_assist_widget,
        area=QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
    )
    parent.tabifyDockWidget(node_library_dock, ai_assist_dock)

    return MainWindowDockBundle(
        properties_dock=properties_dock,
        log_dock=log_dock,
        node_library_dock=node_library_dock,
        layers_dock=layers_dock,
        ai_assist_dock=ai_assist_dock,
    )


def build_service_manager_dock(
    parent: QtWidgets.QMainWindow,
    *,
    manager_widget: QtWidgets.QWidget,
    log_dock: QtWidgets.QDockWidget,
) -> QtWidgets.QDockWidget:
    service_manager_dock = add_dock_widget(
        parent,
        title="Service Manager",
        object_name="ServiceManagerDock",
        widget=manager_widget,
        area=QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
    )
    parent.tabifyDockWidget(log_dock, service_manager_dock)
    return service_manager_dock
