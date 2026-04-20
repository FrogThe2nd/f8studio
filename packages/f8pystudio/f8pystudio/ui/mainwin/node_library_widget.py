from __future__ import annotations

import logging
from typing import Any

from qtpy import QtCore, QtWidgets, QtGui
from NodeGraphQt import NodesTreeWidget
from NodeGraphQt.custom_widgets.nodes_tree import TYPE_NODE

from f8pysdk.codec import coerce_bool
from ...assets.common.asset_cache_events import subscribe_asset_cache_changed
from ...assets.common.common import json_string_list_loads, stable_json_dumps
from ...nodegraph.spec_visibility import is_hidden_spec_node_class, typed_spec_template_or_none
from ...assets.variants.variant_ids import is_variant_node_type, parse_variant_node_type
from ...assets.variants.variant_repository import (
    variant_exists,
)
from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from .node_library_tree_build_mixin import NodeLibraryTreeBuildMixin
from .node_library_tree_interaction_mixin import NodeLibraryTreeInteractionMixin
from .node_library_tree_state_mixin import NodeLibraryTreeStateMixin

logger = logging.getLogger(__name__)


class _F8StudioNodesTreeWidget(
    NodeLibraryTreeBuildMixin,
    NodeLibraryTreeStateMixin,
    NodeLibraryTreeInteractionMixin,
    NodesTreeWidget,
):
    _ROLE_NODE_ID = int(QtCore.Qt.ItemDataRole.UserRole) + 1
    _ROLE_NODE_NAME = int(QtCore.Qt.ItemDataRole.UserRole) + 2
    _ROLE_BASE_NODE_ID = int(QtCore.Qt.ItemDataRole.UserRole) + 3
    _ROLE_VARIANT_ID = int(QtCore.Qt.ItemDataRole.UserRole) + 4
    _ROLE_IS_VARIANT = int(QtCore.Qt.ItemDataRole.UserRole) + 5
    _ROLE_VARIANT_NAME = int(QtCore.Qt.ItemDataRole.UserRole) + 6
    _ROLE_CATEGORY_ID = int(QtCore.Qt.ItemDataRole.UserRole) + 7
    _TREE_BUILD_BATCH_SIZE = 48

    def __init__(self, parent: QtWidgets.QWidget | None = None, node_graph: Any | None = None) -> None:
        self._search_text = ""
        self._search_variants_enabled = False
        self._on_open_variant_catalog: Any | None = None
        self._node_graph = node_graph
        self._variant_catalog_dialogs: list[QtWidgets.QDialog] = []
        self._tree_build_activated = False
        self._tree_build_generation = 0
        self._tree_build_rows: list[tuple[str, str, str, str, list[Any]]] = []
        self._tree_build_index = 0
        self._tree_build_pending_refresh = False
        self._saved_expanded_categories: set[str] = set()
        self._saved_expanded_base_nodes: set[str] = set()
        self._saved_category_state_initialized = False
        self._restoring_expanded_state = False
        self._suppress_expansion_persistence = False
        self._on_expansion_state_changed: Any | None = None
        super().__init__(parent=parent, node_graph=node_graph)
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setUniformRowHeights(True)
        self.setDragEnabled(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.setStyleSheet(
            "QTreeView::item {"
            "  margin: 0px;"
            "  padding-top: 0px;"
            "  padding-bottom: 0px;"
            "}"
            "QTreeView::branch {"
            "  margin: 0px;"
            "  padding: 0px;"
            "}"
        )
        self.customContextMenuRequested.connect(self._on_context_menu_requested)  # type: ignore[attr-defined]
        self.itemClicked.connect(self._on_item_clicked)  # type: ignore[attr-defined]
        self.itemExpanded.connect(self._on_item_expanded)  # type: ignore[attr-defined]
        self.itemCollapsed.connect(self._on_item_collapsed)  # type: ignore[attr-defined]

    def activate_tree_build(self) -> None:
        if self._tree_build_activated:
            return
        self._tree_build_activated = True
        self.update()

    def set_search_text(self, text: str) -> None:
        value = str(text or "").strip().lower()
        if value == self._search_text:
            return
        self._search_text = value
        self.update()

    def set_search_variants_enabled(self, enabled: bool) -> None:
        val = bool(enabled)
        if val == self._search_variants_enabled:
            return
        self._search_variants_enabled = val
        self.update()

    def set_open_variant_catalog_callback(self, callback: Any | None) -> None:
        self._on_open_variant_catalog = callback

    def set_saved_expansion_state(
        self,
        *,
        expanded_categories: list[str] | set[str] | tuple[str, ...],
        expanded_base_nodes: list[str] | set[str] | tuple[str, ...],
        categories_initialized: bool,
    ) -> None:
        self._saved_expanded_categories = {
            item for item in (str(value).strip() for value in expanded_categories) if item
        }
        self._saved_expanded_base_nodes = {
            item for item in (str(value).strip() for value in expanded_base_nodes) if item
        }
        self._saved_category_state_initialized = bool(categories_initialized)

    def set_expansion_state_changed_callback(self, callback: Any | None) -> None:
        self._on_expansion_state_changed = callback


class F8StudioNodeLibraryWidget(QtWidgets.QWidget):
    """
    Tree-based nodes browser with keyword search for Studio.
    """
    _SETTINGS_ORGANIZATION = "Feel8"
    _SETTINGS_APPLICATION = "F8PyStudio"
    _SETTINGS_GROUP = "node_library/preferences/v1"
    _SEARCH_VARIANTS_ENABLED_KEY = "search_variants_enabled"
    _CATEGORY_EXPANSION_INITIALIZED_KEY = "category_expansion_initialized"
    _EXPANDED_CATEGORIES_KEY = "expanded_categories"
    _EXPANDED_BASE_NODES_KEY = "expanded_base_nodes"

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        node_graph: Any | None = None,
        *,
        asset_cache_auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Node Library")
        self._node_graph = node_graph
        self._unsubscribe_asset_cache_changed: Any | None = None
        if asset_cache_auto_refresh:
            self._unsubscribe_asset_cache_changed = subscribe_asset_cache_changed(self._on_asset_cache_changed)
        (
            saved_expanded_categories,
            saved_expanded_base_nodes,
            saved_category_state_initialized,
        ) = self._read_saved_tree_expansion_state()

        self._search = QtWidgets.QLineEdit(self)
        self._search.setPlaceholderText("Search nodes (name, tags, description)")
        self._search_variants = QtWidgets.QCheckBox("Search Variants", self)
        self._search_variants.setChecked(self._read_saved_search_variants_enabled())
        self._tree = _F8StudioNodesTreeWidget(self, node_graph=node_graph)
        self._tree.set_saved_expansion_state(
            expanded_categories=saved_expanded_categories,
            expanded_base_nodes=saved_expanded_base_nodes,
            categories_initialized=saved_category_state_initialized,
        )
        self._tree.set_expansion_state_changed_callback(self._on_tree_expansion_state_changed)
        self._tree.set_search_variants_enabled(self._search_variants.isChecked())

        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._search_variants, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(search_row)
        layout.addWidget(self._tree)

        self._search.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_variants.toggled.connect(self._on_search_variants_toggled)  # type: ignore[attr-defined]
        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        if node_graph is not None:
            node_graph.nodes_registered.connect(self._on_nodes_registered)  # type: ignore[attr-defined]
            node_graph.node_placement_changed.connect(self._on_node_placement_changed)  # type: ignore[attr-defined]

    def _on_search_text_changed(self, text: str) -> None:
        self._tree.set_search_text(str(text or ""))

    def _on_search_variants_toggled(self, enabled: bool) -> None:
        enabled_flag = bool(enabled)
        self._tree.set_search_variants_enabled(enabled_flag)
        self._write_saved_search_variants_enabled(enabled=enabled_flag)

    def _on_nodes_registered(self, _nodes: list[Any]) -> None:
        self._tree.update()

    def _on_node_placement_changed(self, active: bool, label: str) -> None:
        _ = label
        if bool(active):
            return
        self._tree.clearSelection()
        self._tree.setCurrentItem(None)

    def _on_tree_expansion_state_changed(
        self,
        expanded_categories: set[str],
        expanded_base_nodes: set[str],
        categories_initialized: bool,
    ) -> None:
        self._write_saved_tree_expansion_state(
            expanded_categories=expanded_categories,
            expanded_base_nodes=expanded_base_nodes,
            categories_initialized=categories_initialized,
        )

    def _clear_asset_cache_changed_subscription(self) -> None:
        unsubscribe = self._unsubscribe_asset_cache_changed
        self._unsubscribe_asset_cache_changed = None
        if unsubscribe is not None:
            unsubscribe()

    def rebuild_asset_search_sources(self) -> None:
        self._tree.update()

    def _on_asset_cache_changed(self) -> None:
        try:
            self.rebuild_asset_search_sources()
            self._cancel_invalid_variant_placement_if_needed()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_asset_cache_changed_subscription()
                return
            raise

    def _cancel_invalid_variant_placement_if_needed(self) -> None:
        graph = self._node_graph
        if graph is None:
            return
        try:
            pending_node_type = graph.pending_node_placement_type()
        except (AttributeError, RuntimeError, TypeError):
            return
        if pending_node_type is None:
            return
        pending_value = str(pending_node_type).strip()
        if not pending_value:
            return
        if not is_variant_node_type(pending_value):
            return
        variant_id = parse_variant_node_type(pending_value)
        if variant_id is None:
            return
        if variant_exists(variant_id):
            return
        try:
            graph.cancel_node_placement()
        except (AttributeError, RuntimeError, TypeError):
            return

    def _on_destroyed(self, _obj: Any) -> None:
        self._clear_asset_cache_changed_subscription()

    def set_category_label(self, category: str, label: str) -> None:
        self._tree.set_category_label(category, label)

    def set_open_variant_catalog_callback(self, callback: Any | None) -> None:
        self._tree.set_open_variant_catalog_callback(callback)

    def update(self) -> None:
        self._tree.update()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._tree.activate_tree_build()

    def _settings(self) -> QtCore.QSettings:
        return QtCore.QSettings(self._SETTINGS_ORGANIZATION, self._SETTINGS_APPLICATION)

    def _read_saved_search_variants_enabled(self) -> bool:
        settings = self._settings()
        settings.beginGroup(self._SETTINGS_GROUP)
        try:
            raw = settings.value(self._SEARCH_VARIANTS_ENABLED_KEY, False)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=False)

    def _write_saved_search_variants_enabled(self, *, enabled: bool) -> None:
        settings = self._settings()
        settings.beginGroup(self._SETTINGS_GROUP)
        try:
            settings.setValue(self._SEARCH_VARIANTS_ENABLED_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _read_saved_tree_expansion_state(self) -> tuple[set[str], set[str], bool]:
        settings = self._settings()
        settings.beginGroup(self._SETTINGS_GROUP)
        try:
            raw_categories_initialized = settings.value(self._CATEGORY_EXPANSION_INITIALIZED_KEY, False)
            raw_expanded_categories = settings.value(self._EXPANDED_CATEGORIES_KEY, "[]")
            raw_expanded_base_nodes = settings.value(self._EXPANDED_BASE_NODES_KEY, "[]")
        finally:
            settings.endGroup()
        expanded_categories = self._read_saved_string_set(
            raw_value=raw_expanded_categories,
            key=self._EXPANDED_CATEGORIES_KEY,
        )
        expanded_base_nodes = self._read_saved_string_set(
            raw_value=raw_expanded_base_nodes,
            key=self._EXPANDED_BASE_NODES_KEY,
        )
        return (
            expanded_categories,
            expanded_base_nodes,
            coerce_bool(raw_categories_initialized, default=False),
        )

    def _read_saved_string_set(self, *, raw_value: object, key: str) -> set[str]:
        try:
            values = json_string_list_loads(raw_value)
        except ValueError:
            logger.exception("Failed to parse node library preference key=%s", key)
            return set()
        return {item for item in (str(value).strip() for value in values) if item}

    def _write_saved_tree_expansion_state(
        self,
        *,
        expanded_categories: set[str],
        expanded_base_nodes: set[str],
        categories_initialized: bool,
    ) -> None:
        settings = self._settings()
        settings.beginGroup(self._SETTINGS_GROUP)
        try:
            settings.setValue(self._CATEGORY_EXPANSION_INITIALIZED_KEY, bool(categories_initialized))
            settings.setValue(self._EXPANDED_CATEGORIES_KEY, stable_json_dumps(sorted(expanded_categories)))
            settings.setValue(self._EXPANDED_BASE_NODES_KEY, stable_json_dumps(sorted(expanded_base_nodes)))
            settings.sync()
        finally:
            settings.endGroup()
