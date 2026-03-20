from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtWidgets

from ..service_bridge_protocol import ServiceBridge
from ..viewer import F8StudioNodeViewer


def backend_node(node_item: Any) -> Any | None:
    """
    Best-effort access to the backing BaseNode object for this view item.
    """
    graph_obj = graph(node_item)
    if graph_obj is None:
        return None
    try:
        node_id = str(node_item.id or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        node_id = ""
    if not node_id:
        return None
    try:
        return graph_obj.get_node_by_id(node_id)
    except KeyError:
        return None


def graph(node_item: Any) -> Any | None:
    viewer = viewer_safe(node_item)
    if not isinstance(viewer, F8StudioNodeViewer):
        return None
    return viewer.f8_graph


def viewer_safe(node_item: Any) -> Any | None:
    try:
        return node_item.viewer()
    except RuntimeError:
        return None


def ensure_graph_property_hook(node_item: Any) -> None:
    if node_item._graph_prop_hooked:
        return
    graph_obj = graph(node_item)
    if graph_obj is None:
        return
    try:
        graph_obj.property_changed.connect(node_item._sync_state_inline_controls_from_graph_property)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError):
        node_item._graph_prop_hooked = False
        return
    node_item._graph_prop_hooked = True


def select_node_from_embedded_widget(node_item: Any) -> None:
    node = backend_node(node_item)
    graph_obj = graph(node_item)
    if node is None or graph_obj is None:
        return
    scene = None
    try:
        scene = node_item.scene()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        scene = None

    try:
        mods = QtWidgets.QApplication.keyboardModifiers()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        mods = QtCore.Qt.NoModifier
    multi = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier))

    if scene is not None and not multi:
        try:
            scene.clearSelection()
        except (AttributeError, RuntimeError, TypeError):
            pass
    try:
        node_item.setSelected(True)
    except (AttributeError, RuntimeError, TypeError):
        pass

    try:
        graph_obj.node_selected.emit(node)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        graph_obj.node_selection_changed.emit([node], [])  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError):
        pass


def bridge(node_item: Any) -> ServiceBridge | None:
    graph_obj = graph(node_item)
    try:
        return graph_obj.service_bridge if graph_obj is not None else None
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        return None


def ensure_bridge_process_hook(node_item: Any) -> None:
    if node_item._bridge_proc_hooked:
        return
    bridge_obj = bridge(node_item)
    if bridge_obj is None:
        return
    try:
        bridge_obj.service_process_state.connect(node_item._on_bridge_service_process_state)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        node_item._bridge_proc_hooked = False
        return
    node_item._bridge_proc_hooked = True


def is_service_running(node_item: Any) -> bool:
    bridge_obj = bridge(node_item)
    service_id = current_service_id(node_item)
    if bridge_obj is None or not service_id:
        return False
    try:
        return bool(bridge_obj.is_service_running(service_id))
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        return False


def on_bridge_service_process_state(node_item: Any, service_id: str, running: bool) -> None:
    if str(service_id or "").strip() != current_service_id(node_item):
        return
    enabled = bool(running)
    for button in list(node_item._cmd_buttons):
        try:
            button.setEnabled(enabled)
            if not enabled:
                button.setToolTip(
                    (button.toolTip() or "").strip()
                    + ("\nService not running" if button.toolTip() else "Service not running")
                )
        except (AttributeError, RuntimeError, TypeError):
            continue
    try:
        QtCore.QTimer.singleShot(0, node_item.draw_node)
    except (AttributeError, RuntimeError, TypeError):
        pass


def current_service_id(node_item: Any) -> str:
    try:
        return str(node_item.id or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        return ""
