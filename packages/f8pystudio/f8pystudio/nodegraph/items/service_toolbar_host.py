from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets


class F8ElideToolButton(QtWidgets.QToolButton):
    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._full_text = ""

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self._apply_elide()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def event(self, event):  # type: ignore[override]
        # Tooltips on embedded widgets inside a QGraphicsProxyWidget can pick up
        # an unexpected palette/style (showing as a black box). Force the
        # tooltip to be shown with the global/default styling by passing
        # widget=None.
        try:
            if event.type() == QtCore.QEvent.ToolTip:
                tip = str(self.toolTip() or "").strip()
                if not tip:
                    return True
                pos = None
                try:
                    pos = event.globalPos()
                except AttributeError:
                    try:
                        pos = event.globalPosition().toPoint()
                    except (AttributeError, RuntimeError, TypeError):
                        pos = None
                if pos is not None:
                    QtWidgets.QToolTip.showText(pos, tip, None)
                    return True
        except (AttributeError, RuntimeError, TypeError):
            pass
        return super().event(event)

    def _apply_elide(self) -> None:
        try:
            fm = QtGui.QFontMetrics(self.font())
            # Leave room for the arrow icon.
            width = max(10, int(self.width() - 24))
            self.setText(fm.elidedText(self._full_text, QtCore.Qt.ElideRight, width))
        except RuntimeError:
            self.setText(self._full_text)


class F8ForceGlobalToolTipFilter(QtCore.QObject):
    """
    Force tooltip display via `QToolTip.showText(..., widget=None)` to avoid
    dark/black tooltip palette issues when widgets are embedded in a
    `QGraphicsProxyWidget`.
    """

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() != QtCore.QEvent.ToolTip:
            return super().eventFilter(watched, event)
        if not isinstance(watched, QtWidgets.QWidget):
            return True
        tip = str(watched.toolTip() or "").strip()
        if not tip:
            return True
        try:
            pos = event.globalPos()  # type: ignore[attr-defined]
        except Exception:
            try:
                pos = event.globalPosition().toPoint()  # type: ignore[attr-defined]
            except Exception:
                return True
        QtWidgets.QToolTip.showText(pos, tip, None)
        return True


def current_service_id(node_item: object) -> str:
    try:
        return str(node_item.id or "").strip()  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError):
        return ""


def ensure_service_toolbar(node_item: object, viewer: object | None) -> None:
    if node_item._svc_toolbar_proxy is not None:  # type: ignore[attr-defined]
        return
    service_id = current_service_id(node_item)
    if not service_id:
        return

    def _resolve_graph() -> object | None:
        try:
            from ..viewer import F8StudioNodeViewer

            if isinstance(viewer, F8StudioNodeViewer) and viewer.f8_graph is not None:
                return viewer.f8_graph
        except (AttributeError, RuntimeError, TypeError):
            pass
        return node_item._graph()  # type: ignore[attr-defined]

    def _get_bridge() -> object | None:
        graph_obj = _resolve_graph()
        try:
            return graph_obj.service_bridge if graph_obj is not None else None
        except (AttributeError, RuntimeError, TypeError):
            return None

    def _get_node() -> object | None:
        graph_obj = _resolve_graph()
        if graph_obj is None:
            return None
        try:
            return graph_obj.get_node_by_id(current_service_id(node_item))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _get_service_class() -> str:
        try:
            node = _get_node() or node_item._backend_node()  # type: ignore[attr-defined]
            if node is None:
                return ""
            spec = node.spec
            return str(spec.serviceClass or "")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _get_compiled_graphs() -> object | None:
        try:
            graph_obj = _resolve_graph() or node_item._graph()  # type: ignore[attr-defined]
            if graph_obj is None:
                return None
            from ..runtime_compiler import compile_runtime_graphs_from_studio

            return compile_runtime_graphs_from_studio(graph_obj)
        except (AttributeError, RuntimeError, TypeError, ImportError):
            return None

    try:
        from ..service_process_toolbar import ServiceProcessToolbar

        widget = ServiceProcessToolbar(
            service_id=service_id,
            get_bridge=_get_bridge,
            get_node=_get_node,
            get_service_class=_get_service_class,
            get_compiled_graphs=_get_compiled_graphs,
        )
        proxy = QtWidgets.QGraphicsProxyWidget(node_item)  # type: ignore[arg-type]
        proxy.setWidget(widget)
        proxy.setZValue(10_000)
        proxy.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        node_item._svc_toolbar_proxy = proxy  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError, ValueError):
        node_item._svc_toolbar_proxy = None  # type: ignore[attr-defined]


def refresh_service_identity_bindings(node_item: object) -> None:
    proxy = node_item._svc_toolbar_proxy  # type: ignore[attr-defined]
    if proxy is None:
        return
    try:
        widget = proxy.widget()
    except (AttributeError, RuntimeError, TypeError):
        widget = None
    try:
        from ..service_process_toolbar import ServiceProcessToolbar

        if isinstance(widget, ServiceProcessToolbar):
            widget.set_service_id(current_service_id(node_item))
    except (AttributeError, RuntimeError, TypeError, ImportError):
        pass
    position_service_toolbar(node_item)


def position_service_toolbar(node_item: object) -> None:
    proxy = node_item._svc_toolbar_proxy  # type: ignore[attr-defined]
    if proxy is None:
        return
    try:
        rect = node_item.boundingRect()  # type: ignore[attr-defined]
        width = float(proxy.size().width() or 0.0)
        height = float(proxy.size().height() or 0.0)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return

    try:
        proxy.setPos(rect.right() - width, rect.top() - height)
    except (AttributeError, RuntimeError, TypeError):
        pass
