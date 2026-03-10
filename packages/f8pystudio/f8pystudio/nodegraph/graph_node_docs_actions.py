from __future__ import annotations

from typing import Any

from NodeGraphQt import BaseNode

from ..widgets.node_docs_dialog import show_node_docs_dialog
from .spec_visibility import typed_spec_template_or_none


class GraphNodeDocsActionsMixin:
    def install_node_docs_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        nodes_menu = self.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in self._node_docs_menu_node_types:
                continue
            nodes_menu.add_command(
                self.tr("Show Docs"),
                func=self._on_show_node_docs_menu_action,
                node_type=node_type,
            )
            self._node_docs_menu_node_types.add(node_type)

    def _on_show_node_docs_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        if not isinstance(node, BaseNode):
            return
        node_type = str(node.type_ or "").strip()
        if not node_type:
            return
        node_cls = self.node_factory.nodes.get(node_type)
        if node_cls is None:
            return
        spec = typed_spec_template_or_none(node_cls)
        if spec is None:
            return
        node_name = str(node.name() or node.NODE_NAME or node_type).strip()
        if not node_name:
            node_name = node_type
        show_node_docs_dialog(
            parent=self._notification_parent(),
            spec=spec,
            node_id=node_type,
            node_name=node_name,
        )
