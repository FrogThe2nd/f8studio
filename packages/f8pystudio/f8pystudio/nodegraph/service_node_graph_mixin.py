from __future__ import annotations

from typing import Any, Protocol, cast

from qtpy import QtCore, QtWidgets

from .service_bridge_protocol import ServiceBridge
from .viewer import F8StudioNodeViewer


class _ServiceNodeGraphGraph(Protocol):
    property_changed: Any
    node_selected: Any
    node_selection_changed: Any
    service_bridge: ServiceBridge | None

    def get_node_by_id(self, node_id: str) -> Any: ...


class _ServiceNodeGraphHost(Protocol):
    id: Any
    _graph_prop_hooked: bool
    _bridge_proc_hooked: bool

    def viewer(self) -> Any: ...

    def scene(self) -> Any: ...

    def setSelected(self, selected: bool) -> None: ...

    def _sync_state_inline_controls_from_graph_property(self, node: Any, name: str, value: Any) -> None: ...

    def _refresh_inline_command_rows(self) -> None: ...

    def draw_node(self) -> None: ...


class ServiceNodeGraphMixin:
    def _backend_node(self) -> Any | None:
        host = cast(_ServiceNodeGraphHost, self)
        graph_obj = cast(_ServiceNodeGraphGraph | None, self._graph())
        if graph_obj is None:
            return None
        try:
            node_id = str(host.id or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            node_id = ""
        if not node_id:
            return None
        try:
            return graph_obj.get_node_by_id(node_id)
        except KeyError:
            return None

    def _graph(self) -> Any | None:
        viewer = self._viewer_safe()
        if not isinstance(viewer, F8StudioNodeViewer):
            return None
        return viewer.f8_graph

    def _viewer_safe(self) -> Any | None:
        host = cast(_ServiceNodeGraphHost, self)
        try:
            return host.viewer()
        except RuntimeError:
            return None

    def _ensure_graph_property_hook(self) -> None:
        host = cast(_ServiceNodeGraphHost, self)
        if host._graph_prop_hooked:
            return
        graph_obj = cast(_ServiceNodeGraphGraph | None, self._graph())
        if graph_obj is None:
            return
        try:
            graph_obj.property_changed.connect(host._sync_state_inline_controls_from_graph_property)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            host._graph_prop_hooked = False
            return
        host._graph_prop_hooked = True

    def _select_node_from_embedded_widget(self) -> None:
        host = cast(_ServiceNodeGraphHost, self)
        node = self._backend_node()
        graph_obj = cast(_ServiceNodeGraphGraph | None, self._graph())
        if node is None or graph_obj is None:
            return

        scene = None
        try:
            scene = host.scene()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            scene = None

        try:
            modifiers = QtWidgets.QApplication.keyboardModifiers()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            modifiers = QtCore.Qt.KeyboardModifier.NoModifier
        multi_select = bool(
            modifiers
            & (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
        )

        if scene is not None and not multi_select:
            try:
                scene.clearSelection()
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            host.setSelected(True)
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

    def _bridge(self) -> ServiceBridge | None:
        graph_obj = cast(_ServiceNodeGraphGraph | None, self._graph())
        try:
            return graph_obj.service_bridge if graph_obj is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            return None

    def _ensure_bridge_process_hook(self) -> None:
        host = cast(_ServiceNodeGraphHost, self)
        if host._bridge_proc_hooked:
            return
        bridge_obj = self._bridge()
        if bridge_obj is None:
            return
        try:
            bridge_obj.service_process_state.connect(self._on_bridge_service_process_state)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            host._bridge_proc_hooked = False
            return
        host._bridge_proc_hooked = True

    def _is_service_running(self) -> bool:
        bridge_obj = self._bridge()
        service_id = self._service_id()
        if bridge_obj is None or not service_id:
            return False
        try:
            return bool(bridge_obj.is_service_running(service_id))
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            return False

    def _on_bridge_service_process_state(self, service_id: str, running: bool) -> None:
        host = cast(_ServiceNodeGraphHost, self)
        _ = running
        if str(service_id or "").strip() != self._service_id():
            return
        try:
            host._refresh_inline_command_rows()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            QtCore.QTimer.singleShot(0, host.draw_node)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _service_id(self) -> str:
        host = cast(_ServiceNodeGraphHost, self)
        try:
            return str(host.id or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            return ""
