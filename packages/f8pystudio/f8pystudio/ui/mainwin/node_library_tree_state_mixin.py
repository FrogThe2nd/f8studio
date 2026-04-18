from __future__ import annotations

from typing import Any, cast

from NodeGraphQt.custom_widgets.nodes_tree import TYPE_NODE

from ...nodegraph.spec_visibility import is_hidden_spec_node_class, typed_spec_template_or_none
from f8pysdk.specs import palette_category_from_spec


class NodeLibraryTreeStateMixin:
    def _emit_expansion_state_changed(self) -> None:
        host = cast(Any, self)
        callback = host._on_expansion_state_changed
        if not callable(callback):
            return
        callback(
            set(host._saved_expanded_categories),
            set(host._saved_expanded_base_nodes),
            bool(host._saved_category_state_initialized),
        )

    def _set_item_expanded_from_memory(self, item: Any, *, expanded: bool) -> None:
        host = cast(Any, self)
        host._restoring_expanded_state = True
        try:
            item.setExpanded(bool(expanded))
        finally:
            host._restoring_expanded_state = False

    def _all_known_category_ids(self) -> set[str]:
        host = cast(Any, self)
        categories: set[str] = set()
        if host._factory is None:
            return categories
        for node_ids in host._factory.names.values():
            for node_id_any in list(node_ids or []):
                node_id = str(node_id_any)
                node_cls = host._factory.nodes.get(node_id)
                if node_cls is None or is_hidden_spec_node_class(node_cls):
                    continue
                category = "uncategorized"
                spec = typed_spec_template_or_none(node_cls)
                if spec is not None:
                    category = palette_category_from_spec(spec)
                categories.add(category)
        return categories

    def _remember_item_expanded_state(self, item: Any, *, expanded: bool) -> None:
        host = cast(Any, self)
        if host._restoring_expanded_state or host._suppress_expansion_persistence:
            return
        category_id = str(item.data(0, host._ROLE_CATEGORY_ID) or "").strip()
        if category_id:
            if not host._saved_category_state_initialized:
                host._saved_expanded_categories = host._all_known_category_ids()
                host._saved_category_state_initialized = True
            if expanded:
                host._saved_expanded_categories.add(category_id)
            else:
                host._saved_expanded_categories.discard(category_id)
            host._emit_expansion_state_changed()
            return
        if item.type() != TYPE_NODE:
            return
        if bool(item.data(0, host._ROLE_IS_VARIANT)):
            return
        base_node_id = str(item.data(0, host._ROLE_NODE_ID) or "").strip()
        if not base_node_id:
            return
        if expanded:
            host._saved_expanded_base_nodes.add(base_node_id)
        else:
            host._saved_expanded_base_nodes.discard(base_node_id)
        host._emit_expansion_state_changed()

    def _on_item_expanded(self, item: Any) -> None:
        self._remember_item_expanded_state(item, expanded=True)

    def _on_item_collapsed(self, item: Any) -> None:
        self._remember_item_expanded_state(item, expanded=False)
