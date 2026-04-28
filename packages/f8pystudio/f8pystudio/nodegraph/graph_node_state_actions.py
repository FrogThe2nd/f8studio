from __future__ import annotations

from typing import Any

from NodeGraphQt import BaseNode


class GraphNodeStateActionsMixin:
    def install_node_state_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        nodes_menu = self.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in self._node_state_menu_node_types:
                continue
            nodes_menu.add_command(
                self.tr("Enable Selected"),
                func=self._on_enable_selected_nodes_menu_action,
                node_type=node_type,
            )
            nodes_menu.add_command(
                self.tr("Disable Selected"),
                func=self._on_disable_selected_nodes_menu_action,
                node_type=node_type,
            )
            self._node_state_menu_node_types.add(node_type)

    def _on_enable_selected_nodes_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        self._set_nodes_disabled(self._nodes_for_state_menu_action(node), disabled=False)

    def _on_disable_selected_nodes_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        self._set_nodes_disabled(self._nodes_for_state_menu_action(node), disabled=True)

    def _nodes_for_state_menu_action(self, node: Any) -> list[BaseNode]:
        selected_nodes = [
            candidate
            for candidate in list(self.selected_nodes() or [])
            if isinstance(candidate, BaseNode)
        ]
        if isinstance(node, BaseNode):
            if not selected_nodes:
                return [node]
            if node in selected_nodes:
                return selected_nodes
            return [node]
        return selected_nodes

    def _set_nodes_disabled(self, nodes: list[BaseNode], *, disabled: bool) -> None:
        targets: list[BaseNode] = []
        seen_ids: set[str] = set()
        for node in list(nodes or []):
            node_id = str(node.id or "").strip()
            if not node_id or node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            targets.append(node)
        if not targets:
            return

        target_state = bool(disabled)
        action = self.tr("disable selected nodes") if target_state else self.tr("enable selected nodes")
        self.begin_undo(action)
        try:
            for node in targets:
                node.set_property("disabled", target_state, push_undo=True)
        finally:
            self.end_undo()
