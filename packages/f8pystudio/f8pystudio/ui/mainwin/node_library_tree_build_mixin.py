from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from qtpy import QtCore, QtWidgets

from NodeGraphQt.custom_widgets.nodes_tree import _BaseNodeTreeItem, TYPE_CATEGORY, TYPE_NODE

from ...assets.common.asset_display_labels import variant_tree_display_name
from ...assets.variants.variant_ids import build_variant_node_type
from ...assets.variants.variant_repository import list_variant_entries_grouped_by_base
from ...nodegraph.node_roles import node_role_for_palette_category
from ...nodegraph.spec_visibility import is_hidden_spec_node_class, typed_spec_template_or_none
from ...ui.support.node_category_labels import display_node_category_label
from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from f8pysdk.specs import palette_category_from_spec


class NodeLibraryTreeBuildMixin:
    def _show_status_message(self, message: str) -> None:
        host = cast(Any, self)
        host._tree_build_generation += 1
        host.clear()
        item = _BaseNodeTreeItem(host, [str(message or "")], type=TYPE_CATEGORY)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        item.setFirstColumnSpanned(True)
        item.setSizeHint(0, QtCore.QSize(100, 22))
        host.addTopLevelItem(item)

    def _start_tree_build(self) -> None:
        host = cast(Any, self)
        host._tree_build_generation += 1
        generation = host._tree_build_generation
        expanded_categories = set(host._saved_expanded_categories)
        expanded_base_nodes = set(host._saved_expanded_base_nodes)
        categories_initialized = bool(host._saved_category_state_initialized)
        host._suppress_expansion_persistence = True
        host.clear()
        if host._factory is None:
            host._suppress_expansion_persistence = False
            return

        show_variant_children = host._search_variants_enabled
        variants_by_base: dict[str, list[Any]] = {}
        if show_variant_children:
            variants_by_base = {
                base_node_type: list(entries)
                for base_node_type, entries in list_variant_entries_grouped_by_base(include_uninstalled=False).items()
            }

        node_types_by_category: dict[str, list[tuple[str, str, str, list[Any]]]] = defaultdict(list)
        for node_name, node_ids in host._factory.names.items():
            for node_id_any in list(node_ids or []):
                node_id = str(node_id_any)
                node_cls = host._factory.nodes.get(node_id)
                if node_cls is None or is_hidden_spec_node_class(node_cls):
                    continue
                base_match = host._matches_search(node_cls=node_cls, node_name=str(node_name), node_id=node_id)
                matched_variants: list[Any] = []
                if show_variant_children:
                    all_variants = variants_by_base.get(node_id, [])
                    if not host._search_text:
                        matched_variants = list(all_variants)
                    else:
                        for variant in all_variants:
                            if host._variant_matches_search(variant):
                                matched_variants.append(variant)
                if not base_match and not matched_variants:
                    continue
                category = "uncategorized"
                spec_description = ""
                spec = typed_spec_template_or_none(node_cls)
                if spec is not None:
                    category = palette_category_from_spec(spec)
                    spec_description = host._spec_description(spec)
                role_filter = host._node_role_filter
                if role_filter is not None and node_role_for_palette_category(category) != role_filter:
                    continue
                node_types_by_category[category].append((node_id, str(node_name), spec_description, matched_variants))

        host._category_items = {}
        for category in sorted(node_types_by_category.keys()):
            label = str(host._custom_labels.get(category, display_node_category_label(category)))
            cat_item = _BaseNodeTreeItem(host, [label], type=TYPE_CATEGORY)
            cat_item.setFirstColumnSpanned(True)
            cat_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            cat_item.setSizeHint(0, QtCore.QSize(100, 22))
            cat_item.setData(0, host._ROLE_CATEGORY_ID, category)
            host.addTopLevelItem(cat_item)
            host._category_items[category] = cat_item

        host._tree_build_rows = []
        for category, nodes_list in node_types_by_category.items():
            for node_id, node_name, spec_description, matched_variants in nodes_list:
                host._tree_build_rows.append((category, node_id, node_name, spec_description, matched_variants))
        host._tree_build_index = 0
        host._tree_build_pending_refresh = False
        host._append_tree_build_batch(
            generation=generation,
            expanded_base_nodes=expanded_base_nodes,
            categories_initialized=categories_initialized,
            expanded_categories=expanded_categories,
        )

    def _append_tree_build_batch(
        self,
        *,
        generation: int,
        expanded_base_nodes: set[str],
        categories_initialized: bool,
        expanded_categories: set[str],
    ) -> None:
        host = cast(Any, self)
        if generation != host._tree_build_generation or host._factory is None:
            return
        end_index = min(host._tree_build_index + host._TREE_BUILD_BATCH_SIZE, len(host._tree_build_rows))
        while host._tree_build_index < end_index:
            category, node_id, node_name, spec_description, matched_variants = host._tree_build_rows[host._tree_build_index]
            host._tree_build_index += 1
            category_item = host._category_items.get(category)
            if category_item is None:
                continue
            try:
                item = _BaseNodeTreeItem(category_item, [node_name], type=TYPE_NODE)
            except RuntimeError as exc:
                if qt_runtime_error_is_object_deleted(exc):
                    return
                raise
            item.setToolTip(0, node_id)
            item.setSizeHint(0, QtCore.QSize(100, 22))
            item.setData(0, host._ROLE_NODE_ID, node_id)
            item.setData(0, host._ROLE_BASE_NODE_ID, node_id)
            item.setData(0, host._ROLE_NODE_NAME, node_name)
            item.setData(0, host._ROLE_IS_VARIANT, False)
            category_item.addChild(item)

            if spec_description:
                item.setToolTip(0, f"{node_id}\n\n{spec_description}")

            for variant in matched_variants:
                variant_node_type = build_variant_node_type(str(variant.record.variantId))
                variant_text = variant_tree_display_name(variant)
                try:
                    variant_item = _BaseNodeTreeItem(item, [variant_text], type=TYPE_NODE)
                except RuntimeError as exc:
                    if qt_runtime_error_is_object_deleted(exc):
                        return
                    raise
                variant_item.setToolTip(0, variant_node_type)
                variant_item.setSizeHint(0, QtCore.QSize(100, 22))
                variant_item.setData(0, host._ROLE_NODE_ID, variant_node_type)
                variant_item.setData(0, host._ROLE_BASE_NODE_ID, node_id)
                variant_item.setData(0, host._ROLE_NODE_NAME, node_name)
                variant_item.setData(0, host._ROLE_VARIANT_ID, str(variant.record.variantId))
                variant_item.setData(0, host._ROLE_IS_VARIANT, True)
                variant_item.setData(0, host._ROLE_VARIANT_NAME, str(variant.record.name or ""))
                item.addChild(variant_item)
            host._set_item_expanded_from_memory(item, expanded=node_id in expanded_base_nodes)

        if host._tree_build_index < len(host._tree_build_rows):
            QtCore.QTimer.singleShot(
                0,
                lambda: host._append_tree_build_batch(
                    generation=generation,
                    expanded_base_nodes=expanded_base_nodes,
                    categories_initialized=categories_initialized,
                    expanded_categories=expanded_categories,
                ),
            )
            return
        for index in range(host.topLevelItemCount()):
            category_item = host.topLevelItem(index)
            if category_item is None:
                continue
            try:
                category_id = str(category_item.data(0, host._ROLE_CATEGORY_ID) or "").strip()
            except RuntimeError as exc:
                if qt_runtime_error_is_object_deleted(exc):
                    return
                raise
            category_expanded = True
            if categories_initialized:
                category_expanded = category_id in expanded_categories
            host._set_item_expanded_from_memory(category_item, expanded=category_expanded)
        host._suppress_expansion_persistence = False
        if host._tree_build_pending_refresh:
            host._start_tree_build()

    def _build_tree(self) -> None:
        host = cast(Any, self)
        if not host._tree_build_activated:
            host._tree_build_pending_refresh = True
            host._show_status_message("Open the node library to load nodes.")
            return
        host._show_status_message("Loading nodes...")
        QtCore.QTimer.singleShot(0, host._start_tree_build)
