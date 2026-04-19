from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json, validate_as

from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.ui_notifications import show_warning
from ..variants.variant_events import subscribe_variants_changed
from ..variants.variant_models import F8VariantEntry, F8VariantSourceKind
from .asset_cloud_account_menu import build_asset_account_menu, prompt_asset_cloud_sign_in

logger = logging.getLogger(__name__)


class VariantCatalogBrowserMixin:
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
    _pending_reload_from_variants_changed: bool
    _pending_reload_variant_id: str
    _is_handling_selection_change: bool
    _selected_variant_id: Any
    _build_row_states: Any
    _entries_for_current_tab: Any
    _build_list_row: Any
    _on_selection_changed: Any

    def _initialize_browser_state(self) -> None:
        tabs = (self._TAB_MINE, self._TAB_COMMUNITY, self._TAB_INSTALLED)
        self._initial_remote_refresh_done = False
        self._tab_queries: dict[int, str] = {tab: "" for tab in tabs}
        self._tab_filters: dict[int, str] = {tab: "all" for tab in tabs}
        self._remote_next_cursor_by_scope: dict[str, str | None] = {"mine": None, "community": None}
        self._remote_loaded_query_by_scope: dict[str, str] = {"mine": "", "community": ""}
        self._remote_loaded_base_by_scope: dict[str, str] = {"mine": "", "community": ""}
        self._is_loading_remote_scope = False
        self._variants_changed_unsubscribe: Callable[[], None] | None = subscribe_variants_changed(
            self._on_variants_changed
        )
        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]

    def _clear_variants_changed_subscription(self) -> None:
        unsubscribe = self._variants_changed_unsubscribe
        self._variants_changed_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_destroyed(self, _obj: Any) -> None:
        self._clear_variants_changed_subscription()

    def _on_variants_changed(self) -> None:
        try:
            selected_variant_id = self._selected_variant_id()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_variants_changed_subscription()
                return
            raise
        if self._is_handling_selection_change:
            logger.warning(
                "Variant manager deferred variants_changed reload while handling selection variant_id=%s",
                selected_variant_id,
            )
            self._pending_reload_from_variants_changed = True
            if selected_variant_id:
                self._pending_reload_variant_id = selected_variant_id
            return
        try:
            self._reload(preserve_variant_id=selected_variant_id)
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                self._clear_variants_changed_subscription()
                return
            raise

    def _reload(self, *_args: Any, preserve_variant_id: str | None = None) -> None:
        selected_variant_id = str(
            self._selected_variant_id() if preserve_variant_id is None else preserve_variant_id or ""
        ).strip()
        logger.debug(
            "Variant manager reload requested tab=%s preserve_variant_id=%s",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            selected_variant_id,
        )
        self._refresh_remote_catalog_if_needed()
        self._row_states_by_variant_id = self._build_row_states()
        self._entries = self._entries_for_current_tab()
        logger.debug(
            "Variant manager reload tab=%s base_node_type=%s count=%d entries=%s",
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
                    "syncState": entry.syncState.value,
                }
                for entry in self._entries[:10]
            ],
        )
        self._list.blockSignals(True)
        try:
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
            if selected_variant_id:
                self._restore_selection(selected_variant_id)
        finally:
            self._list.blockSignals(False)
        self._account_button.setToolTip(self._account_button_text())
        self._search_input.blockSignals(True)
        self._search_input.setText(self._current_query())
        self._search_input.blockSignals(False)
        self._reload_filter_combo()
        self._refresh_auth_controls()
        self._on_selection_changed()
        self._schedule_auto_load_more_if_needed()

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
        try:
            community_page = self._sync_client.refresh_scope_page(
                scope="community",
                base_node_type=self._base_node_type,
                query=self._tab_queries[self._TAB_COMMUNITY],
                cursor="",
                append=False,
            )
            self._remote_next_cursor_by_scope["community"] = community_page.nextCursor
            self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
            self._remote_loaded_base_by_scope["community"] = self._base_node_type
            if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
                try:
                    self._sync_client.refresh_auth()
                except Exception:
                    logger.exception("Variant manager initial auth refresh failed")
                if self._sync_client.current_access_token():
                    mine_page = self._sync_client.refresh_scope_page(
                        scope="mine",
                        base_node_type=self._base_node_type,
                        query=self._tab_queries[self._TAB_MINE],
                        cursor="",
                        append=False,
                    )
                    self._remote_next_cursor_by_scope["mine"] = mine_page.nextCursor
                    self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
                    self._remote_loaded_base_by_scope["mine"] = self._base_node_type
        except Exception:
            logger.exception("Variant manager initial remote refresh failed")

    def _refresh_auth_controls(self) -> None:
        logged_in = self._sync_client.current_user() is not None and bool(self._sync_client.current_access_token())
        self._btn_refresh.setEnabled(True)
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER if logged_in else StudioIcon.USER_OFF))

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

    def _on_accounts_clicked(self) -> None:
        menu = build_asset_account_menu(
            parent=self,
            sync_client=self._sync_client,
            on_changed=self._on_account_state_changed,
        )
        menu.exec(self._account_button.mapToGlobal(QtCore.QPoint(0, self._account_button.height())))

    def _account_button_text(self) -> str:
        user = self._sync_client.current_user()
        if user is None:
            return "Accounts"
        return str(user.username or user.displayName or "Accounts")

    def _current_query(self) -> str:
        return str(self._tab_queries.get(self._scope_tabs.currentIndex(), "")).strip()

    def _current_filter_value(self) -> str:
        return str(self._tab_filters.get(self._scope_tabs.currentIndex(), "all")).strip() or "all"

    def _reload_filter_combo(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        items: list[tuple[str, str]]
        if current_tab == self._TAB_MINE:
            items = [
                ("All Mine", "all"),
                ("Local Only", "local"),
                ("Private Cloud", "private"),
                ("Shared Public", "shared"),
            ]
        elif current_tab == self._TAB_COMMUNITY:
            items = [
                ("All Community", "all"),
                ("Subscribed", "subscribed"),
                ("Not Subscribed", "not_subscribed"),
            ]
        else:
            items = [
                ("All Installed", "all"),
                ("My Variants", "mine"),
                ("Subscribed", "subscribed"),
            ]
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
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_COMMUNITY:
            return "community"
        if current_tab == self._TAB_MINE:
            return "mine"
        return None

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

    def _on_login_clicked(self) -> None:
        if prompt_asset_cloud_sign_in(parent=self, sync_client=self._sync_client):
            self._on_account_state_changed()

    def _on_logout_clicked(self) -> None:
        self._on_account_state_changed()

    def _on_account_state_changed(self) -> None:
        current_user = self._sync_client.current_user()
        if current_user is None or not self._sync_client.current_access_token():
            sanitized_remote_entries: list[F8VariantEntry] = []
            for entry in self._sync_client._catalog_service._remote_provider.load_entries():
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
            self._sync_client._catalog_service._remote_provider.save_entries(sanitized_remote_entries)
            self._remote_next_cursor_by_scope["mine"] = None
            self._remote_loaded_query_by_scope["mine"] = ""
            self._remote_loaded_base_by_scope["mine"] = ""
            self._reload()
            return
        try:
            community_page = self._sync_client.refresh_scope_page(
                scope="community",
                base_node_type=self._base_node_type,
                query=self._tab_queries[self._TAB_COMMUNITY],
                cursor="",
                append=False,
            )
            self._remote_next_cursor_by_scope["community"] = community_page.nextCursor
            self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
            self._remote_loaded_base_by_scope["community"] = self._base_node_type
            mine_page = self._sync_client.refresh_scope_page(
                scope="mine",
                base_node_type=self._base_node_type,
                query=self._tab_queries[self._TAB_MINE],
                cursor="",
                append=False,
            )
            self._remote_next_cursor_by_scope["mine"] = mine_page.nextCursor
            self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
            self._remote_loaded_base_by_scope["mine"] = self._base_node_type
        except Exception:
            logger.exception("Variant manager account state refresh failed")
        self._reload()

    def _ensure_logged_in(self) -> bool:
        if self._sync_client.current_user() is not None and self._sync_client.current_access_token():
            return True
        if self._sync_client.current_session() is not None:
            try:
                self._sync_client.refresh_auth()
                self._reload()
                return True
            except Exception:
                logger.exception("Variant manager remembered account refresh failed")
        self._on_login_clicked()
        return self._sync_client.current_user() is not None and bool(self._sync_client.current_access_token())

    def _on_search_submitted(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        query = str(self._search_input.text() or "").strip()
        if self._tab_queries.get(current_tab, "") == query:
            return
        self._tab_queries[current_tab] = query
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._reload()
            return
        self._refresh_current_remote_scope(reset=True)

    def _on_search_text_changed(self, text: str) -> None:
        if str(text or "").strip():
            return
        if not self._current_query():
            return
        self._on_search_submitted()

    def _on_filter_changed(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        filter_value = str(self._filter_combo.currentData() or "all").strip() or "all"
        if self._tab_filters.get(current_tab, "all") == filter_value:
            return
        self._tab_filters[current_tab] = filter_value
        self._reload()

    def _on_node_type_filter_changed(self) -> None:
        if not self._is_global_mode:
            return
        self._base_node_type = str(self._node_type_combo.currentData() or "").strip()
        self._reload()

    def _populate_node_type_combo(self) -> None:
        if not self._is_global_mode:
            return
        self._node_type_combo.blockSignals(True)
        self._node_type_combo.clear()
        self._node_type_combo.addItem("All Types", "")

        service = self._sync_client._catalog_service
        node_types: set[str] = set()
        for entry in service.load_all_entries():
            base_type = str(entry.record.baseNodeType or "").strip()
            if base_type:
                node_types.add(base_type)

        for node_type in sorted(node_types):
            self._node_type_combo.addItem(node_type, node_type)

        self._node_type_combo.blockSignals(False)

    def _get_current_base_node_type(self) -> str:
        return self._base_node_type

    def _refresh_current_remote_scope(self, *, reset: bool) -> None:
        if self._is_loading_remote_scope:
            return
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._reload()
            return
        current_query = self._current_query()
        current_base = self._get_current_base_node_type()
        if remote_scope == "mine" and not self._ensure_logged_in():
            return
        if reset:
            cursor = ""
            append = False
        else:
            cursor = str(self._remote_next_cursor_by_scope.get(remote_scope) or "")
            append = bool(cursor)
            if not append:
                return
        self._is_loading_remote_scope = True
        self._refresh_auth_controls()
        try:
            page = self._sync_client.refresh_scope_page(
                scope=remote_scope,
                base_node_type=current_base,
                query=current_query,
                cursor=cursor,
                append=append,
            )
        except Exception as exc:
            show_warning(self, "Refresh failed", str(exc))
            return
        finally:
            self._is_loading_remote_scope = False
        self._remote_next_cursor_by_scope[remote_scope] = page.nextCursor
        self._remote_loaded_query_by_scope[remote_scope] = current_query
        self._remote_loaded_base_by_scope[remote_scope] = current_base
        logger.debug(
            "Variant manager remote scope refreshed scope=%s reset=%s query=%s cursor=%s fetched=%d next_cursor=%s",
            remote_scope,
            reset,
            current_query,
            cursor,
            len(page.entries),
            page.nextCursor,
        )
        self._reload()

    def _on_refresh_clicked(self) -> None:
        remote_scope = self._remote_scope_for_current_tab()
        current_base = self._get_current_base_node_type()
        if remote_scope is None:
            try:
                self._sync_client.refresh_scope(
                    scope="community",
                    base_node_type=current_base,
                    query=self._tab_queries[self._TAB_COMMUNITY],
                )
                self._remote_next_cursor_by_scope["community"] = None
                self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
                self._remote_loaded_base_by_scope["community"] = current_base
                if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
                    if self._ensure_logged_in():
                        self._sync_client.refresh_scope(
                            scope="mine",
                            base_node_type=current_base,
                            query=self._tab_queries[self._TAB_MINE],
                        )
                        self._remote_next_cursor_by_scope["mine"] = None
                        self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
                        self._remote_loaded_base_by_scope["mine"] = current_base
            except Exception as exc:
                show_warning(self, "Refresh failed", str(exc))
                return
            self._reload()
            return
        self._refresh_current_remote_scope(reset=True)

    def _auth_status_text(self) -> str:
        user = self._sync_client.current_user()
        if user is None:
            return "Signed out"
        return str(user.displayName or user.username or "Signed in")
