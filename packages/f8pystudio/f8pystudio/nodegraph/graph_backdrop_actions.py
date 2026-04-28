from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from qtpy import QtWidgets

from f8pysdk.specs import F8OperatorSpec

from ..ui.support.ui_notifications import show_warning
from ..render_nodes.backdrop import BackdropRenderNode

BACKDROP_OPERATOR_CLASS = "f8.backdrop"


class _NodeClassProtocol(Protocol):
    SPEC_TEMPLATE: object
    type_: object


class _ContextNodesMenuProtocol(Protocol):
    def add_command(self, label: str, *, func: Callable[..., object], node_type: str) -> object: ...


class _BackdropWrapperProtocol(Protocol):
    def wrap_nodes(
        self,
        nodes: list[object],
        *,
        push_undo: bool = True,
        begin_undo_macro: bool = True,
    ) -> None: ...


class _GraphBackdropHost(Protocol):
    def _notification_parent(self) -> QtWidgets.QWidget | None: ...

    def context_nodes_menu(self) -> _ContextNodesMenuProtocol | None: ...

    def selected_nodes(self) -> list[object]: ...

    def begin_undo(self, name: str) -> None: ...

    def end_undo(self) -> None: ...

    def create_node(
        self,
        node_type: str,
        name: str | None = None,
        selected: bool = True,
        color: object | None = None,
        text_color: object | None = None,
        pos: object | None = None,
        push_undo: bool = True,
        begin_undo_macro: bool = True,
    ) -> object: ...


class GraphBackdropActionsMixin:
    _backdrop_create_menu_node_types: set[str] | None = None
    _backdrop_wrap_menu_node_types: set[str] | None = None
    _backdrop_registered_node_type: str | None = None

    def _backdrop_create_menu_types(self) -> set[str]:
        node_types = self._backdrop_create_menu_node_types
        if node_types is None:
            node_types = set()
            self._backdrop_create_menu_node_types = node_types
        return node_types

    def _backdrop_wrap_menu_types(self) -> set[str]:
        node_types = self._backdrop_wrap_menu_node_types
        if node_types is None:
            node_types = set()
            self._backdrop_wrap_menu_node_types = node_types
        return node_types

    def _registered_backdrop_node_type(self) -> str:
        return str(self._backdrop_registered_node_type or "").strip()

    def _create_backdrop_from_selection(self) -> object | None:
        host = cast(_GraphBackdropHost, cast(object, self))
        selected_nodes = list(host.selected_nodes() or [])
        if not selected_nodes:
            show_warning(
                host._notification_parent(),
                "Create backdrop failed",
                "Select one or more nodes first.",
            )
            return None
        backdrop_node_type = self._registered_backdrop_node_type()
        if not backdrop_node_type:
            show_warning(
                host._notification_parent(),
                "Create backdrop failed",
                "Backdrop node type is not registered in this graph.",
            )
            return None
        host.begin_undo("create backdrop from selection")
        try:
            created = host.create_node(
                backdrop_node_type,
                selected=True,
                push_undo=True,
                begin_undo_macro=False,
            )
            if isinstance(created, BackdropRenderNode):
                created.wrap_nodes(selected_nodes, push_undo=True, begin_undo_macro=False)
                return created
            try:
                cast(_BackdropWrapperProtocol, created).wrap_nodes(
                    selected_nodes,
                    push_undo=True,
                    begin_undo_macro=False,
                )
            except (AttributeError, RuntimeError, TypeError):
                return created
        finally:
            host.end_undo()
        return created

    def _on_create_backdrop_from_selection_action(self, graph: object) -> None:
        _ = graph
        self._create_backdrop_from_selection()

    @staticmethod
    def _is_backdrop_node_class(node_cls: type[_NodeClassProtocol]) -> bool:
        spec_template = node_cls.SPEC_TEMPLATE
        return isinstance(spec_template, F8OperatorSpec) and str(spec_template.operatorClass or "") == BACKDROP_OPERATOR_CLASS

    def _on_wrap_selected_nodes_menu_action(self, graph: object, node: object) -> None:
        _ = graph
        host = cast(_GraphBackdropHost, cast(object, self))
        if not isinstance(node, BackdropRenderNode):
            return
        selected_nodes = [candidate for candidate in list(host.selected_nodes() or []) if candidate is not node]
        if not selected_nodes:
            show_warning(
                host._notification_parent(),
                "Wrap selected nodes failed",
                "Select one or more nodes in addition to the backdrop first.",
            )
            return
        node.wrap_nodes(selected_nodes)

    def install_backdrop_context_menu_for_nodes(self, node_classes: list[type[_NodeClassProtocol]]) -> None:
        host = cast(_GraphBackdropHost, cast(object, self))
        nodes_menu = host.context_nodes_menu()
        if nodes_menu is None:
            return
        backdrop_node_type = ""
        for node_cls in list(node_classes or []):
            if not self._is_backdrop_node_class(node_cls):
                continue
            candidate_type = str(node_cls.type_ or "").strip()
            if not candidate_type:
                continue
            backdrop_node_type = candidate_type
            self._backdrop_registered_node_type = candidate_type
            break
        if not backdrop_node_type:
            return
        backdrop_create_menu_node_types = self._backdrop_create_menu_types()
        backdrop_wrap_menu_node_types = self._backdrop_wrap_menu_types()
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "").strip()
            if not node_type:
                continue
            if node_type not in backdrop_create_menu_node_types:
                _ = nodes_menu.add_command(
                    "Create Backdrop From Selection",
                    func=self._on_create_backdrop_from_selection_action,
                    node_type=node_type,
                )
                backdrop_create_menu_node_types.add(node_type)
            if not self._is_backdrop_node_class(node_cls):
                continue
            if node_type in backdrop_wrap_menu_node_types:
                continue
            _ = nodes_menu.add_command(
                "Wrap Selected Nodes",
                func=self._on_wrap_selected_nodes_menu_action,
                node_type=node_type,
            )
            backdrop_wrap_menu_node_types.add(node_type)
