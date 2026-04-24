from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import TYPE_CHECKING, Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json, validate_as

from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from ...ui.support.ui_notifications import show_warning
from ..variants.variant_models import F8VariantEntry, F8VariantRemoteAuthError, F8VariantSourceKind
from ..variants.variant_sync import VariantRemoteScopeRefreshRequest, VariantRemoteScopeRefreshResult
from ..variants.variant_events import subscribe_variants_changed
from .catalog_browser_state import (
    DEFAULT_CATALOG_FILTER,
    DEFAULT_REFRESH_ERROR_TITLE,
    INITIAL_REFRESH_ERROR_TITLE,
    REFRESH_LOG_LABEL_INITIAL_OPEN,
    REFRESH_LOG_LABEL_MANUAL_REFRESH,
    REFRESH_LOG_LABEL_SCOPE_REFRESH,
    REMOTE_SCOPE_COMMUNITY,
    REMOTE_SCOPE_MINE,
    CatalogRefreshLogFields,
    QueuedCatalogRefresh,
    catalog_filter_options_for_tab,
    catalog_refresh_log_fields,
    catalog_refresh_page_request,
    create_catalog_browser_state,
    current_catalog_filter,
    current_catalog_query,
    remote_scope_for_catalog_tab,
)
from .catalog_hosts import _VariantCatalogDialogHost
from .catalog_refresh_queue_mixin import CatalogRefreshQueueMixin


if TYPE_CHECKING:
    _VariantCatalogBrowserMixinBase = CatalogRefreshQueueMixin[VariantRemoteScopeRefreshRequest] | _VariantCatalogDialogHost
else:
    _VariantCatalogBrowserMixinBase = CatalogRefreshQueueMixin

logger = logging.getLogger(__name__)


class VariantCatalogBrowserMixin(_VariantCatalogBrowserMixinBase):
    _TAB_DRAFTS: int
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    _base_node_type: str
    _is_global_mode: bool
    _sync_client: Any
    _entries: list[F8VariantEntry]
    _row_states_by_variant_id: dict[str, Any]
    _scope_tabs: Any
    _list: Any
    _account_button: Any
    _search_input: Any
    _filter_combo: Any
    _node_type_combo: Any
    _btn_refresh: Any
    destroyed: Any
    _pending_asset_cache_rebuild: bool
    _pending_asset_cache_rebuild_variant_id: str
    _is_handling_selection_change: bool
    _selected_variant_id: Any
    _build_row_states: Any
    _entries_for_current_tab: Any
    _build_list_row: Any
    _on_selection_changed: Any
    _matches_filter: Any

    def _initialize_browser_state(self) -> None:
        tabs = (self._TAB_DRAFTS, self._TAB_MINE, self._TAB_COMMUNITY, self._TAB_INSTALLED)
        state = create_catalog_browser_state(tabs=tabs)
        self._initial_remote_refresh_done = state.initial_remote_refresh_done
        self._initial_remote_refresh_scheduled = state.initial_remote_refresh_scheduled
        self._initial_cached_render_started_at = state.initial_cached_render_started_at
        self._tab_queries = state.tab_queries
        self._tab_filters = state.tab_filters
        self._remote_next_cursor_by_scope = state.remote_next_cursor_by_scope
        self._remote_loaded_query_by_scope = state.remote_loaded_query_by_scope
        self._remote_loaded_base_by_scope: dict[str, str] = {
            REMOTE_SCOPE_MINE: "",
            REMOTE_SCOPE_COMMUNITY: "",
        }
        self._is_loading_remote_scope = state.is_loading_remote_scope
        self._active_remote_refresh_request_id = state.active_remote_refresh_request_id
        self._remote_refresh_worker = state.remote_refresh_worker
        self._active_remote_refresh_error_title = state.active_remote_refresh_error_title
        self._active_remote_refresh_log_label = state.active_remote_refresh_log_label
        self._active_remote_refresh_started_at = state.active_remote_refresh_started_at
        self._queued_remote_refresh: QueuedCatalogRefresh[VariantRemoteScopeRefreshRequest] | None = None
        self._catalog_local_entries_snapshot = state.catalog_local_entries_snapshot
        self._catalog_remote_entries_snapshot = state.catalog_remote_entries_snapshot
        self._last_list_render_signature = state.last_list_render_signature
        self._scheduled_asset_cache_rebuild_variant_id = ""
        self._asset_cache_rebuild_timer = QtCore.QTimer(self)
        self._asset_cache_rebuild_timer.setSingleShot(True)
        self._asset_cache_rebuild_timer.timeout.connect(self._flush_deferred_asset_cache_rebuild)  # type: ignore[attr-defined]
        self._asset_cache_changed_unsubscribe: Callable[[], None] | None = subscribe_variants_changed(
            self._on_asset_cache_changed
        )
        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]

    def _clear_asset_cache_changed_subscription(self) -> None:
        unsubscribe = self._asset_cache_changed_unsubscribe
        self._asset_cache_changed_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_destroyed(self, _obj: Any) -> None:
        self._clear_asset_cache_changed_subscription()
        self._active_remote_refresh_request_id += 1
        self._queued_remote_refresh = None
        self._remote_refresh_worker = None
        self._scheduled_asset_cache_rebuild_variant_id = ""
        try:
            self._asset_cache_rebuild_timer.stop()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                return
            raise

    def _rebuild_browser_from_asset_cache(self) -> None:
        self._render_browser_from_state()

    def _render_browser_initial_state(self) -> None:
        self._rebuild_browser_from_asset_cache()

    def _refresh_catalog_source_snapshot(self) -> None:
        self._catalog_local_entries_snapshot = self._draft_service_for_catalog().list_catalog_entries()
        self._catalog_remote_entries_snapshot = self._sync_client.load_cached_remote_entries()

    def _local_entries_snapshot(self) -> list[F8VariantEntry]:
        return list(self._catalog_local_entries_snapshot)

    def _remote_entries_snapshot(self) -> list[F8VariantEntry]:
        return list(self._catalog_remote_entries_snapshot)

    def _list_render_signature(self) -> tuple[object, ...]:
        current_tab = int(self._scope_tabs.currentIndex())
        current_base_type = str(self._get_current_base_node_type() or "").strip()
        entry_signatures: list[tuple[object, ...]] = []
        for entry in self._entries:
            row_state = self._row_state_for_entry(entry)
            entry_signatures.append(
                (
                    str(entry.record.variantId or "").strip(),
                    str(entry.record.name or ""),
                    str(entry.record.baseNodeType or ""),
                    str(entry.record.updatedAt or ""),
                    str(entry.source.value),
                    None if entry.visibility is None else str(entry.visibility.value),
                    str(entry.ownerUserId or ""),
                    str(entry.ownerDisplayName or ""),
                    str(entry.remoteVersionNumber or ""),
                    str(entry.downloadedAt or ""),
                    bool(entry.installed),
                    bool(entry.hasCachedContent),
                    bool(entry.subscribed),
                    bool(entry.isLocalDraft),
                    str(entry.draftOriginAssetId or ""),
                    str(entry.draftOriginVersionNumber or ""),
                    row_state.has_local_head,
                    row_state.has_remote_head,
                    row_state.has_cached_remote_content,
                    row_state.visibility,
                    row_state.owner_display_name,
                    row_state.subscribed,
                    str(row_state.presence.value),
                    self._linked_draft_reference_text(entry),
                    self._linked_draft_badge_text(entry),
                )
            )
        return (current_tab, current_base_type, tuple(entry_signatures))

    def _rebuild_browser_after_draft_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_auth_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_remote_scope_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_installed_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_remote_asset_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_tab_ui_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_query_ui_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_filter_ui_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _rebuild_browser_after_node_type_ui_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        self._rebuild_browser_from_asset_cache_for_change(
            preserve_variant_id=preserve_variant_id
        )

    def _on_scope_tab_changed(self, _index: int) -> None:
        self._rebuild_browser_after_tab_ui_state_changed(
            preserve_variant_id=self._selected_variant_id()
        )

    def _on_asset_cache_changed(self) -> None:
        try:
            selected_variant_id = self._selected_variant_id()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_asset_cache_changed_subscription()
                return
            raise
        if self._is_handling_selection_change:
            logger.info(
                "Variant manager deferred asset-cache browser rebuild while handling selection variant_id=%s",
                selected_variant_id,
            )
            self._pending_asset_cache_rebuild = True
            if selected_variant_id:
                self._pending_asset_cache_rebuild_variant_id = selected_variant_id
            return
        self._schedule_asset_cache_rebuild(
            preserve_variant_id=selected_variant_id
        )

    def _schedule_asset_cache_rebuild(
        self,
        *,
        preserve_variant_id: str | None,
    ) -> None:
        normalized_variant_id = str(preserve_variant_id or "").strip()
        if normalized_variant_id:
            self._scheduled_asset_cache_rebuild_variant_id = normalized_variant_id
        try:
            if self._asset_cache_rebuild_timer.isActive():
                return
            self._asset_cache_rebuild_timer.start(0)
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_asset_cache_changed_subscription()
                return
            raise

    def _flush_deferred_asset_cache_rebuild(self) -> None:
        preserve_variant_id = str(self._scheduled_asset_cache_rebuild_variant_id or "").strip()
        self._scheduled_asset_cache_rebuild_variant_id = ""
        try:
            self._rebuild_browser_after_installed_state_changed(
                preserve_variant_id=preserve_variant_id
            )
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_asset_cache_changed_subscription()
                return
            raise

    def _rebuild_browser_from_asset_cache_preserving_selection(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        normalized_variant_id = str(
            self._selected_variant_id() if preserve_variant_id is None else preserve_variant_id or ""
        ).strip()
        if not normalized_variant_id:
            self._rebuild_browser_from_asset_cache()
            return
        self._render_browser_from_state(preserve_variant_id=normalized_variant_id)

    def _rebuild_browser_from_asset_cache_for_change(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None:
        normalized_variant_id = str(preserve_variant_id or "").strip()
        if not normalized_variant_id:
            self._rebuild_browser_from_asset_cache()
            return
        self._rebuild_browser_from_asset_cache_preserving_selection(
            preserve_variant_id=normalized_variant_id
        )

    def _record_remote_scope_refresh(
        self,
        *,
        scope: str,
        query: str,
        base_node_type: str,
        next_cursor: str | None,
    ) -> None:
        self._remote_next_cursor_by_scope[scope] = next_cursor
        self._remote_loaded_query_by_scope[scope] = query
        self._remote_loaded_base_by_scope[scope] = base_node_type

    def _render_browser_from_state(
        self,
        *_args: Any,
        preserve_variant_id: str | None = None,
    ) -> None:
        render_started_at = time.perf_counter()
        if self._initial_cached_render_started_at <= 0.0:
            self._initial_cached_render_started_at = render_started_at
        selected_variant_id = str(
            self._selected_variant_id() if preserve_variant_id is None else preserve_variant_id or ""
        ).strip()
        logger.debug(
            "Variant manager render requested tab=%s preserve_variant_id=%s",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            selected_variant_id,
        )
        self._schedule_initial_remote_refresh_if_needed()
        self._refresh_catalog_source_snapshot()
        self._sync_node_type_combo_ui()
        self._row_states_by_variant_id = self._build_row_states()
        self._entries = self._entries_for_current_tab()
        list_render_signature = self._list_render_signature()
        logger.debug(
            "Variant manager render tab=%s base_node_type=%s count=%d entries=%s",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            self._base_node_type,
            len(self._entries),
            [
                {
                    "variantId": str(entry.record.variantId),
                    "source": entry.source.value,
                    "visibility": None if entry.visibility is None else entry.visibility.value,
                    "subscribed": bool(entry.subscribed),
                    "installed": bool(entry.installed),
                }
                for entry in self._entries[:10]
            ],
        )
        should_rebuild_list = list_render_signature != self._last_list_render_signature
        self._list.blockSignals(True)
        try:
            if should_rebuild_list:
                self._list.clear()
                for entry in self._entries:
                    record = entry.record
                    list_item = QtWidgets.QListWidgetItem()
                    list_item.setToolTip(record.description or record.name)
                    list_item.setData(QtCore.Qt.ItemDataRole.UserRole, record.variantId)
                    row_widget = self._build_list_row(entry)
                    list_item.setSizeHint(row_widget.sizeHint())
                    self._list.addItem(list_item)
                    self._list.setItemWidget(list_item, row_widget)
                self._last_list_render_signature = list_render_signature
            if selected_variant_id:
                self._restore_selection(selected_variant_id)
        finally:
            self._list.blockSignals(False)
        self._account_button.setToolTip(self._account_button_text())
        self._search_input.blockSignals(True)
        self._search_input.setText(self._current_query())
        self._search_input.blockSignals(False)
        self._sync_filter_combo_ui()
        self._sync_auth_controls_ui()
        self._on_selection_changed()
        self._schedule_auto_load_more_if_needed()
        logger.info(
            "Variant manager cached render tab=%s base=%s count=%d rebuilt_list=%s elapsed=%.3fs",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            self._base_node_type,
            len(self._entries),
            should_rebuild_list,
            time.perf_counter() - render_started_at,
        )

    def _restore_selection(self, variant_id: str) -> None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is None:
                continue
            item_variant_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip()
            if item_variant_id != normalized_variant_id:
                continue
            self._list.setCurrentItem(item)
            return

    def _refresh_remote_catalog_if_needed(self) -> None:
        if self._initial_remote_refresh_done:
            return
        self._initial_remote_refresh_done = True
        self._request_catalog_refresh(
            requests=self._full_catalog_refresh_requests(),
            error_title=INITIAL_REFRESH_ERROR_TITLE,
            log_label=REFRESH_LOG_LABEL_INITIAL_OPEN,
        )

    def _schedule_initial_remote_refresh_if_needed(self) -> None:
        if self._initial_remote_refresh_done or self._initial_remote_refresh_scheduled:
            return
        self._initial_remote_refresh_scheduled = True
        QtCore.QTimer.singleShot(0, self._run_initial_remote_refresh)

    def _run_initial_remote_refresh(self) -> None:
        self._initial_remote_refresh_scheduled = False
        if self._initial_remote_refresh_done:
            return
        self._refresh_remote_catalog_if_needed()

    def _on_list_scrolled(self, _value: int) -> None:
        self._schedule_auto_load_more_if_needed()

    def _schedule_auto_load_more_if_needed(self) -> None:
        if not self._should_auto_load_more():
            return
        QtCore.QTimer.singleShot(0, self._auto_load_more_if_needed)

    def _auto_load_more_if_needed(self) -> None:
        if not self._should_auto_load_more():
            return
        self._refresh_current_remote_scope(reset=False)

    def _should_auto_load_more(self) -> bool:
        if self._is_loading_remote_scope:
            return False
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            return False
        if not self._remote_next_cursor_by_scope.get(remote_scope):
            return False
        scroll_bar = self._list.verticalScrollBar()
        max_value = int(scroll_bar.maximum())
        if max_value <= 0:
            return True
        return int(scroll_bar.value()) >= max_value - 8

    def _current_query(self) -> str:
        return current_catalog_query(
            tab_queries=self._tab_queries,
            current_tab=self._scope_tabs.currentIndex(),
        )

    def _current_filter_value(self) -> str:
        return current_catalog_filter(
            tab_filters=self._tab_filters,
            current_tab=self._scope_tabs.currentIndex(),
        )

    def _sync_filter_combo_ui(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        items = catalog_filter_options_for_tab(
            current_tab=current_tab,
            drafts_tab=self._TAB_DRAFTS,
            mine_tab=self._TAB_MINE,
            community_tab=self._TAB_COMMUNITY,
            installed_mine_label="My Variants",
        )
        selected_value = self._current_filter_value()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        selected_index = 0
        for index, (label, value) in enumerate(items):
            self._filter_combo.addItem(label, value)
            if value == selected_value:
                selected_index = index
        self._filter_combo.setCurrentIndex(selected_index)
        self._filter_combo.blockSignals(False)

    def _remote_scope_for_current_tab(self) -> str | None:
        return remote_scope_for_catalog_tab(
            current_tab=self._scope_tabs.currentIndex(),
            mine_tab=self._TAB_MINE,
            community_tab=self._TAB_COMMUNITY,
        )

    @staticmethod
    def _entry_matches_query(entry: F8VariantEntry, normalized_query: str) -> bool:
        if not normalized_query:
            return True
        haystack = " ".join(
            [
                str(entry.record.name or ""),
                str(entry.record.description or ""),
                " ".join(str(tag) for tag in list(entry.record.tags or [])),
                str(entry.record.baseNodeType or ""),
                str(entry.ownerDisplayName or ""),
            ]
        ).lower()
        return normalized_query in haystack

    def _apply_signed_out_auth_state(self) -> None:
        self._sanitize_remote_entries_for_signed_out_user()
        self._remote_next_cursor_by_scope[REMOTE_SCOPE_MINE] = None
        self._remote_loaded_query_by_scope[REMOTE_SCOPE_MINE] = ""
        self._remote_loaded_base_by_scope[REMOTE_SCOPE_MINE] = ""

    def _sanitize_remote_entries_for_signed_out_user(self) -> None:
        sanitized_remote_entries: list[F8VariantEntry] = []
        for entry in self._sync_client.load_cached_remote_entries():
            if entry.source == F8VariantSourceKind.remote_private:
                continue
            if not entry.subscribed:
                sanitized_remote_entries.append(entry)
                continue
            sanitized_remote_entries.append(
                validate_as(
                    F8VariantEntry,
                    {
                        **dump_json(entry, mode="json"),
                        "subscribed": False,
                    },
                )
            )
        self._sync_client.replace_cached_remote_entries(
            sanitized_remote_entries,
            emit_changed=False,
        )

    def _selected_asset_id_for_auth_refresh(self) -> str:
        return self._selected_variant_id()

    def _rebuild_browser_after_auth_state_changed_for_id(self, selected_asset_id: str) -> None:
        self._rebuild_browser_after_auth_state_changed(preserve_variant_id=selected_asset_id)

    def _signed_out_catalog_refresh_requests(self) -> list[VariantRemoteScopeRefreshRequest]:
        return [
            VariantRemoteScopeRefreshRequest(
                scope=REMOTE_SCOPE_COMMUNITY,
                base_node_type=self._base_node_type,
                query=self._tab_queries[self._TAB_COMMUNITY],
            )
        ]

    def _account_menu_anchor_point(self) -> QtCore.QPoint:
        return QtCore.QPoint(0, self._account_button.height())

    def _on_search_submitted(self) -> None:
        selected_variant_id = self._selected_variant_id()
        current_tab = self._scope_tabs.currentIndex()
        query = str(self._search_input.text() or "").strip()
        if self._tab_queries.get(current_tab, "") == query:
            return
        self._tab_queries[current_tab] = query
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._rebuild_browser_after_query_ui_state_changed(
                preserve_variant_id=selected_variant_id
            )
            return
        self._refresh_current_remote_scope(reset=True)

    def _on_search_text_changed(self, text: str) -> None:
        if str(text or "").strip():
            return
        if not self._current_query():
            return
        self._on_search_submitted()

    def _on_filter_changed(self) -> None:
        selected_variant_id = self._selected_variant_id()
        current_tab = self._scope_tabs.currentIndex()
        filter_value = str(self._filter_combo.currentData() or DEFAULT_CATALOG_FILTER).strip() or DEFAULT_CATALOG_FILTER
        if self._tab_filters.get(current_tab, DEFAULT_CATALOG_FILTER) == filter_value:
            return
        self._tab_filters[current_tab] = filter_value
        self._rebuild_browser_after_filter_ui_state_changed(
            preserve_variant_id=selected_variant_id
        )

    def _on_node_type_filter_changed(self) -> None:
        if not self._is_global_mode:
            return
        selected_variant_id = self._selected_variant_id()
        self._base_node_type = str(self._node_type_combo.currentData() or "").strip()
        self._rebuild_browser_after_node_type_ui_state_changed(
            preserve_variant_id=selected_variant_id
        )

    def _sync_node_type_combo_ui(self) -> None:
        if not self._is_global_mode:
            return
        current_tab = self._scope_tabs.currentIndex()
        normalized_query = self._current_query().lower()
        local_entries = self._local_entries_snapshot()
        remote_entries = self._remote_entries_snapshot()
        if current_tab == self._TAB_DRAFTS:
            candidate_entries = local_entries
        elif current_tab in {self._TAB_MINE, self._TAB_COMMUNITY, self._TAB_INSTALLED}:
            candidate_entries = remote_entries
        else:
            candidate_entries = []
        selected_node_type = self._base_node_type
        node_types: set[str] = set()
        for entry in candidate_entries:
            if not self._matches_filter(entry):
                continue
            if not self._entry_matches_query(entry, normalized_query):
                continue
            base_type = str(entry.record.baseNodeType or "").strip()
            if base_type:
                node_types.add(base_type)
        if selected_node_type and selected_node_type not in node_types:
            selected_node_type = ""
            self._base_node_type = ""
        self._node_type_combo.blockSignals(True)
        self._node_type_combo.clear()
        self._node_type_combo.addItem("All Types", "")
        selected_index = 0
        for index, node_type in enumerate(sorted(node_types), start=1):
            self._node_type_combo.addItem(node_type, node_type)
            if node_type == selected_node_type:
                selected_index = index
        self._node_type_combo.setCurrentIndex(selected_index)
        self._node_type_combo.blockSignals(False)

    def _get_current_base_node_type(self) -> str:
        return self._base_node_type

    def _refresh_current_remote_scope(self, *, reset: bool) -> None:
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._rebuild_browser_after_remote_scope_state_changed(
                preserve_variant_id=self._selected_variant_id()
            )
            return
        current_query = self._current_query()
        current_base = self._get_current_base_node_type()
        if remote_scope == REMOTE_SCOPE_MINE and not self._ensure_logged_in():
            return
        page_request = catalog_refresh_page_request(
            reset=reset,
            remote_scope=remote_scope,
            remote_next_cursor_by_scope=self._remote_next_cursor_by_scope,
        )
        if page_request is None:
            return
        self._request_catalog_refresh(
            requests=[
                VariantRemoteScopeRefreshRequest(
                    scope=remote_scope,
                    base_node_type=current_base,
                    query=current_query,
                    cursor=page_request.cursor,
                    append=page_request.append,
                )
            ],
            error_title=DEFAULT_REFRESH_ERROR_TITLE,
            log_label=REFRESH_LOG_LABEL_SCOPE_REFRESH,
        )

    def _on_refresh_clicked(self) -> None:
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._request_catalog_refresh(
                requests=self._full_catalog_refresh_requests(),
                error_title=DEFAULT_REFRESH_ERROR_TITLE,
                log_label=REFRESH_LOG_LABEL_MANUAL_REFRESH,
            )
            return
        self._refresh_current_remote_scope(reset=True)

    def _full_catalog_refresh_requests(self) -> list[VariantRemoteScopeRefreshRequest]:
        current_base = self._base_node_type
        requests = [
            VariantRemoteScopeRefreshRequest(
                scope=REMOTE_SCOPE_COMMUNITY,
                base_node_type=current_base,
                query=self._tab_queries[self._TAB_COMMUNITY],
                cursor="",
                append=False,
            )
        ]
        if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
            requests.append(
                VariantRemoteScopeRefreshRequest(
                    scope=REMOTE_SCOPE_MINE,
                    base_node_type=current_base,
                    query=self._tab_queries[self._TAB_MINE],
                    cursor="",
                    append=False,
                )
            )
        return requests

    def _catalog_refresh_task(
        self,
        *,
        requests: list[VariantRemoteScopeRefreshRequest],
    ) -> Callable[[], object]:
        background_client = self._sync_client.clone_for_background()
        return lambda: background_client.collect_remote_scope_refreshes(requests, retry_on_auth_failure=False)

    def _log_catalog_refresh_queued(self, *, log_fields: CatalogRefreshLogFields) -> None:
        logger.info(
            "Variant manager queued background refresh request_id=%s label=%s scopes=%s base=%s",
            log_fields.request_id,
            log_fields.log_label,
            log_fields.scopes,
            log_fields.base_node_type,
        )

    def _catalog_refresh_log_fields(
        self,
        *,
        request_id: int,
        log_label: str,
        requests: list[VariantRemoteScopeRefreshRequest],
    ) -> CatalogRefreshLogFields:
        return catalog_refresh_log_fields(
            request_id=request_id,
            log_label=log_label,
            requests=requests,
            base_node_type=self._base_node_type,
        )

    def _on_catalog_refresh_succeeded(
        self,
        *,
        finished_request_id: int,
        result: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
        started_at: float,
    ) -> None:
        if finished_request_id != self._active_remote_refresh_request_id:
            return
        self._remote_refresh_worker = None
        self._is_loading_remote_scope = False
        self._sync_auth_controls_ui()
        if not isinstance(result, VariantRemoteScopeRefreshResult):
            show_warning(self, error_title, "Unexpected variant refresh result type.")
            self._start_queued_catalog_refresh_if_any()
            return
        for request in result.requests:
            page = result.pages_by_scope.get(request.scope)
            if page is None:
                continue
            self._record_remote_scope_refresh(
                scope=request.scope,
                query=request.query,
                base_node_type=request.base_node_type,
                next_cursor=page.nextCursor,
            )
        self._sync_client.apply_remote_entries(result.remote_entries)
        logger.info(
            "Variant manager refresh applied label=%s request_id=%s network=%.3fs total=%.3fs",
            log_label,
            finished_request_id,
            elapsed_seconds,
            time.perf_counter() - started_at,
        )
        if log_label == REFRESH_LOG_LABEL_INITIAL_OPEN and self._initial_cached_render_started_at > 0.0:
            logger.info(
                "Variant manager initial open fully refreshed elapsed=%.3fs",
                time.perf_counter() - self._initial_cached_render_started_at,
            )
        self._start_queued_catalog_refresh_if_any()

    def _on_catalog_refresh_failed(
        self,
        *,
        finished_request_id: int,
        exc: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
    ) -> None:
        if finished_request_id != self._active_remote_refresh_request_id:
            return
        self._remote_refresh_worker = None
        self._is_loading_remote_scope = False
        self._sync_auth_controls_ui()
        if isinstance(exc, F8VariantRemoteAuthError):
            current_account_id = str(self._sync_client.current_account_id() or "").strip()
            if current_account_id:
                self._sync_client.clear_saved_session(current_account_id)
            self._apply_signed_out_auth_state()
            self._rebuild_browser_after_auth_state_changed(
                preserve_variant_id=self._selected_variant_id()
            )
        logger.warning(
            "Variant manager refresh failed label=%s request_id=%s elapsed=%.3fs error=%s",
            log_label,
            finished_request_id,
            elapsed_seconds,
            str(exc),
        )
        if isinstance(exc, Exception):
            show_warning(self, error_title, str(exc))
        self._start_queued_catalog_refresh_if_any()

    def _auth_status_text(self) -> str:
        user = self._sync_client.current_user()
        if user is None:
            return "Signed out"
        return str(user.name or user.email or "Signed in")
