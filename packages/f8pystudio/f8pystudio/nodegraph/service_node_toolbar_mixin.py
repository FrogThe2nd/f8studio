from __future__ import annotations

from typing import Any, Protocol, cast

from qtpy import QtWidgets

from .items.service_toolbar_host import F8ElideToolButton, F8ForceGlobalToolTipFilter


class _ServiceNodeToolbarGraph(Protocol):
    service_bridge: Any

    def get_node_by_id(self, node_id: str) -> Any: ...


class _ServiceNodeToolbarHost(Protocol):
    _svc_toolbar_proxy: QtWidgets.QGraphicsProxyWidget | None

    def _service_id(self) -> str: ...

    def _graph(self) -> Any | None: ...

    def _backend_node(self) -> Any | None: ...

    def boundingRect(self) -> Any: ...


class _ServiceNodeToolbarBackendNode(Protocol):
    spec: Any


class ServiceNodeToolbarMixin:
    def _ensure_service_toolbar(self, viewer: Any | None) -> None:
        host = cast(_ServiceNodeToolbarHost, self)
        if host._svc_toolbar_proxy is not None:
            return
        service_id = host._service_id()
        if not service_id:
            return

        def _resolve_graph() -> _ServiceNodeToolbarGraph | None:
            try:
                from .viewer import F8StudioNodeViewer

                if isinstance(viewer, F8StudioNodeViewer) and viewer.f8_graph is not None:
                    return cast(_ServiceNodeToolbarGraph, viewer.f8_graph)
            except (AttributeError, RuntimeError, TypeError):
                pass
            return cast(_ServiceNodeToolbarGraph | None, host._graph())

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
                return graph_obj.get_node_by_id(host._service_id())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return None

        def _get_service_class() -> str:
            try:
                node = cast(_ServiceNodeToolbarBackendNode | None, _get_node() or host._backend_node())
                if node is None:
                    return ""
                spec = node.spec
                return str(spec.serviceClass or "")
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return ""

        def _get_compiled_graphs() -> object | None:
            try:
                graph_obj = _resolve_graph() or cast(_ServiceNodeToolbarGraph | None, host._graph())
                if graph_obj is None:
                    return None
                from .runtime_compiler import compile_runtime_graphs_from_studio

                return compile_runtime_graphs_from_studio(graph_obj)
            except (AttributeError, RuntimeError, TypeError, ImportError):
                return None

        try:
            from .service_process_toolbar import ServiceProcessToolbar

            widget = ServiceProcessToolbar(
                service_id=service_id,
                get_bridge=_get_bridge,
                get_node=_get_node,
                get_service_class=_get_service_class,
                get_compiled_graphs=_get_compiled_graphs,
            )
            proxy = QtWidgets.QGraphicsProxyWidget(cast(Any, self))
            proxy.setWidget(widget)
            proxy.setZValue(10_000)
            host._svc_toolbar_proxy = proxy
        except (AttributeError, RuntimeError, TypeError, ValueError):
            host._svc_toolbar_proxy = None

    def refresh_service_identity_bindings(self) -> None:
        host = cast(_ServiceNodeToolbarHost, self)
        proxy = host._svc_toolbar_proxy
        if proxy is None:
            return
        try:
            widget = proxy.widget()
        except (AttributeError, RuntimeError, TypeError):
            widget = None
        try:
            from .service_process_toolbar import ServiceProcessToolbar

            if isinstance(widget, ServiceProcessToolbar):
                widget.set_service_id(host._service_id())
        except (AttributeError, RuntimeError, TypeError, ImportError):
            pass
        self._position_service_toolbar()

    def _position_service_toolbar(self) -> None:
        host = cast(_ServiceNodeToolbarHost, self)
        proxy = host._svc_toolbar_proxy
        if proxy is None:
            return
        try:
            rect = host.boundingRect()
            width = float(proxy.size().width() or 0.0)
            height = float(proxy.size().height() or 0.0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

        try:
            proxy.setPos(rect.right() - width, rect.top() - height)
        except (AttributeError, RuntimeError, TypeError):
            pass


__all__ = ["F8ElideToolButton", "F8ForceGlobalToolTipFilter", "ServiceNodeToolbarMixin"]
