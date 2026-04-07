from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.msgspec_codec import dump_json

from ..components.component_events import subscribe_components_changed
from ..components.component_catalog import component_entry_is_installed
from ..components.component_models import F8ComponentEntry, F8ComponentSourceKind
from ..components.component_repository import list_component_entries
from ..components.component_sync import ComponentSyncClient
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.ui_notifications import show_warning
from ...ui.support.json_text_editor import attach_json_enhancements
from .asset_cloud_account_menu import build_asset_account_menu

logger = logging.getLogger(__name__)


class ComponentInsertDialog(QtWidgets.QDialog):
    _TAB_INSTALLED = 0
    _TAB_COMMUNITY = 1

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        node_graph: Any,
        insert_scene_pos: tuple[float, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self._graph = node_graph
        self._insert_scene_pos = insert_scene_pos
        self._sync_client = ComponentSyncClient()
        self._entries: list[F8ComponentEntry] = []
        self._tab_queries: dict[int, str] = {
            self._TAB_INSTALLED: "",
            self._TAB_COMMUNITY: "",
        }
        self._tab_filters: dict[int, str] = {
            self._TAB_INSTALLED: "all",
            self._TAB_COMMUNITY: "all",
        }
        self._remote_next_cursor: str | None = None
        self._remote_loaded_query = ""
        self._is_loading_remote_scope = False
        self._initial_remote_refresh_done = False
        self._components_changed_unsubscribe: Callable[[], None] | None = subscribe_components_changed(
            self._on_components_changed
        )

        self.setWindowTitle("Insert Component")
        self.resize(980, 640)

        self._scope_tabs = QtWidgets.QTabBar(self)
        self._scope_tabs.addTab("Installed")
        self._scope_tabs.addTab("Community")
        self._scope_tabs.currentChanged.connect(self._reload)  # type: ignore[attr-defined]

        self._search_input = QtWidgets.QLineEdit(self)
        self._search_input.setPlaceholderText("Search components")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_input.returnPressed.connect(self._on_search_submitted)  # type: ignore[attr-defined]

        self._search_btn = QtWidgets.QPushButton(self)
        self._search_btn.setIcon(icon_for(self._search_btn, StudioIcon.CLOUD_SEARCH))
        self._search_btn.setToolTip("Search current list")
        self._search_btn.setFixedWidth(30)
        self._search_btn.clicked.connect(self._on_search_submitted)  # type: ignore[attr-defined]

        self._filter_combo = QtWidgets.QComboBox(self)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)  # type: ignore[attr-defined]

        self._refresh_btn = QtWidgets.QPushButton(self)
        self._refresh_btn.setIcon(icon_for(self._refresh_btn, StudioIcon.REFRESH))
        self._refresh_btn.setToolTip("Refresh current list")
        self._refresh_btn.setFixedWidth(30)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)  # type: ignore[attr-defined]

        self._account_button = QtWidgets.QToolButton(self)
        self._account_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER))
        self._account_button.setToolTip("Accounts")
        self._account_button.clicked.connect(self._on_accounts_clicked)  # type: ignore[attr-defined]

        toolbar = QtWidgets.QToolBar("Insert Components", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        toolbar.setIconSize(QtCore.QSize(16, 16))
        toolbar.addWidget(self._scope_tabs)
        toolbar.addSeparator()
        toolbar.addWidget(self._search_input)
        toolbar.addWidget(self._search_btn)
        toolbar.addWidget(self._filter_combo)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addSeparator()
        toolbar.addWidget(self._account_button)

        self._install_btn = QtWidgets.QPushButton(self)
        self._install_btn.setIcon(icon_for(self._install_btn, StudioIcon.CLOUD_DOWN))
        self._install_btn.setToolTip("Download/Install")
        self._install_btn.setText("")
        self._install_btn.setFixedWidth(30)
        self._install_btn.clicked.connect(self._on_install_clicked)  # type: ignore[attr-defined]

        self._insert_btn = QtWidgets.QPushButton(self)
        self._insert_btn.setIcon(icon_for(self._insert_btn, StudioIcon.PACKAGE_IMPORT))
        self._insert_btn.setToolTip("Insert into graph")
        self._insert_btn.setText("")
        self._insert_btn.setFixedWidth(30)
        self._insert_btn.clicked.connect(self._on_insert_clicked)  # type: ignore[attr-defined]

        self._close_btn = QtWidgets.QPushButton(self)
        self._close_btn.setIcon(icon_for(self._close_btn, StudioIcon.X))
        self._close_btn.setToolTip("Close")
        self._close_btn.setText("")
        self._close_btn.setFixedWidth(30)
        self._close_btn.clicked.connect(self.reject)  # type: ignore[attr-defined]

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self._install_btn)
        button_row.addWidget(self._insert_btn)
        button_row.addWidget(self._close_btn)
        button_row.addStretch(1)

        self._list = QtWidgets.QListWidget(self)
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)  # type: ignore[attr-defined]
        self._list.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)  # type: ignore[attr-defined]

        self._raw = QtWidgets.QPlainTextEdit(self)
        self._raw.setReadOnly(True)
        self._raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        attach_json_enhancements(self._raw, read_only=True)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._raw)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(toolbar)
        layout.addLayout(button_row)
        layout.addWidget(split, 1)

        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        self._reload()

    def _clear_components_changed_subscription(self) -> None:
        unsubscribe = self._components_changed_unsubscribe
        self._components_changed_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_destroyed(self, _obj: Any) -> None:
        self._clear_components_changed_subscription()

    def _on_components_changed(self) -> None:
        try:
            self._reload()
        except RuntimeError as exc:
            if "already deleted" in str(exc):
                self._clear_components_changed_subscription()
                return
            raise

    def _reload(self, *_args: Any) -> None:
        self._refresh_remote_catalog_if_needed()
        self._entries = self._entries_for_current_tab()
        self._list.clear()
        for entry in self._entries:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, entry.record.componentId)
            row_widget = self._build_list_row(entry)
            item.setSizeHint(row_widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row_widget)
        self._search_input.blockSignals(True)
        self._search_input.setText(self._current_query())
        self._search_input.blockSignals(False)
        self._reload_filter_combo()
        self._refresh_auth_controls()
        self._on_selection_changed()
        self._schedule_auto_load_more_if_needed()

    def _entries_for_current_tab(self) -> list[F8ComponentEntry]:
        query = self._current_query().lower()
        if self._scope_tabs.currentIndex() == self._TAB_COMMUNITY:
            remote_entries = self._sync_client._catalog_service._remote_provider.load_entries()
            return community_component_entries(
                remote_entries,
                query=query,
                filter_value=self._current_filter_value(),
                current_user_id=self._current_user_id(),
            )
        installed_entries = list_component_entries(include_uninstalled=False)
        return installed_component_entries(
            installed_entries,
            query=query,
            filter_value=self._current_filter_value(),
            current_user_id=self._current_user_id(),
        )

    def _build_list_row(self, entry: F8ComponentEntry) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self._list)
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        name_label = QtWidgets.QLabel(str(entry.record.name or ""), container)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet("color: palette(window-text);")
        title_row.addWidget(name_label, 1)

        if entry.ownerDisplayName:
            owner_label = QtWidgets.QLabel(f"by {entry.ownerDisplayName}", container)
            owner_label.setStyleSheet("color: palette(window-text);")
            title_row.addWidget(owner_label)
        root.addLayout(title_row)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        for badge_text in component_insert_badges(entry):
            badge = QtWidgets.QLabel(badge_text, container)
            badge.setStyleSheet(
                "QLabel { border: 1px solid palette(mid); border-radius: 4px; padding: 1px 6px; color: palette(window-text); background: palette(base); }"
            )
            meta_row.addWidget(badge)
        meta_row.addStretch(1)
        root.addLayout(meta_row)

        if entry.record.description:
            description_label = QtWidgets.QLabel(str(entry.record.description or ""), container)
            description_label.setWordWrap(True)
            description_label.setStyleSheet("color: palette(window-text);")
            root.addWidget(description_label)
        return container

    def _selected_entry(self) -> F8ComponentEntry | None:
        item = self._list.currentItem()
        if item is None:
            return None
        component_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        if not component_id:
            return None
        for entry in self._entries:
            if str(entry.record.componentId) == component_id:
                return entry
        return None

    def _on_selection_changed(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._raw.setPlainText("")
            self._install_btn.setEnabled(False)
            self._insert_btn.setEnabled(False)
            return
        self._raw.setPlainText(json.dumps(dump_json(selected_entry, mode="json"), ensure_ascii=False, indent=2, default=str))
        can_install = selected_entry.source != F8ComponentSourceKind.local and not component_entry_is_installed(selected_entry)
        self._install_btn.setEnabled(can_install)
        self._insert_btn.setEnabled(True)

    def _refresh_remote_catalog_if_needed(self) -> None:
        if self._initial_remote_refresh_done:
            return
        self._initial_remote_refresh_done = True
        try:
            page = self._sync_client.refresh_scope_page(scope="community", query=self._tab_queries[self._TAB_COMMUNITY], cursor="", append=False)
            self._remote_next_cursor = page.nextCursor
            self._remote_loaded_query = self._tab_queries[self._TAB_COMMUNITY]
        except Exception:
            logger.exception("Insert component dialog initial remote refresh failed")

    def _current_query(self) -> str:
        return str(self._tab_queries.get(self._scope_tabs.currentIndex(), "")).strip()

    def _current_filter_value(self) -> str:
        return str(self._tab_filters.get(self._scope_tabs.currentIndex(), "all")).strip() or "all"

    def _reload_filter_combo(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        items: list[tuple[str, str]]
        if current_tab == self._TAB_COMMUNITY:
            items = [
                ("All Community", "all"),
                ("Subscribed", "subscribed"),
                ("Not Subscribed", "not_subscribed"),
            ]
        else:
            items = [
                ("All Installed", "all"),
                ("My Components", "mine"),
                ("Subscribed", "subscribed"),
            ]
        current_value = self._current_filter_value()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        selected_index = 0
        for index, (label, value) in enumerate(items):
            self._filter_combo.addItem(label, value)
            if value == current_value:
                selected_index = index
        self._filter_combo.setCurrentIndex(selected_index)
        self._filter_combo.blockSignals(False)

    def _current_user_id(self) -> str:
        user = self._sync_client.current_user()
        if user is None:
            return ""
        return str(user.userId or "").strip()

    def _on_search_submitted(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        query = str(self._search_input.text() or "").strip()
        if self._tab_queries.get(current_tab, "") == query:
            return
        self._tab_queries[current_tab] = query
        if current_tab == self._TAB_COMMUNITY:
            self._refresh_community(reset=True)
            return
        self._reload()

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

    def _on_refresh_clicked(self) -> None:
        if self._scope_tabs.currentIndex() == self._TAB_COMMUNITY:
            self._refresh_community(reset=True)
            return
        try:
            page = self._sync_client.refresh_scope_page(scope="community", query=self._tab_queries[self._TAB_COMMUNITY], cursor="", append=False)
        except Exception as exc:
            show_warning(self, "Refresh failed", str(exc))
            return
        self._remote_next_cursor = page.nextCursor
        self._remote_loaded_query = self._tab_queries[self._TAB_COMMUNITY]
        self._reload()

    def _refresh_community(self, *, reset: bool) -> None:
        if self._is_loading_remote_scope:
            return
        cursor = ""
        append = False
        if not reset:
            cursor = str(self._remote_next_cursor or "")
            append = bool(cursor)
            if not append:
                return
        self._is_loading_remote_scope = True
        self._refresh_auth_controls()
        try:
            page = self._sync_client.refresh_scope_page(
                scope="community",
                query=self._tab_queries[self._TAB_COMMUNITY],
                cursor=cursor,
                append=append,
            )
        except Exception as exc:
            show_warning(self, "Refresh failed", str(exc))
            return
        finally:
            self._is_loading_remote_scope = False
        self._remote_next_cursor = page.nextCursor
        self._remote_loaded_query = self._tab_queries[self._TAB_COMMUNITY]
        self._reload()

    def _on_accounts_clicked(self) -> None:
        menu = build_asset_account_menu(parent=self, sync_client=self._sync_client, on_changed=self._on_account_state_changed)
        menu.exec(self._account_button.mapToGlobal(QtCore.QPoint(0, self._account_button.height())))

    def _on_account_state_changed(self) -> None:
        self._initial_remote_refresh_done = False
        self._remote_next_cursor = None
        self._remote_loaded_query = ""
        self._reload()

    def _refresh_auth_controls(self) -> None:
        logged_in = self._sync_client.current_user() is not None and bool(self._sync_client.current_access_token())
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER if logged_in else StudioIcon.USER_OFF))

    def _on_install_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        try:
            self._sync_client.hydrate_component(str(selected_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Install failed", str(exc))
            return
        self._reload()

    def _on_insert_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        entry_to_insert = selected_entry
        if entry_to_insert.source != F8ComponentSourceKind.local and not component_entry_is_installed(entry_to_insert):
            try:
                entry_to_insert = self._sync_client.hydrate_component(str(entry_to_insert.record.componentId))
            except Exception as exc:
                show_warning(self, "Install failed", str(exc))
                return
        graph = self._graph
        if graph is None:
            return
        try:
            request = graph.prepare_insert_graph_from_component(
                entry_to_insert.record.content,
                component_name=entry_to_insert.record.name,
            )
        except Exception as exc:
            show_warning(self, "Insert failed", str(exc))
            return
        if self._insert_scene_pos is None:
            graph.begin_graph_placement(request, label=f"Component: {entry_to_insert.record.name}\n{request.node_count} nodes")
        else:
            anchor_x, anchor_y = self._insert_scene_pos
            graph.apply_insert_graph(request, anchor_x=float(anchor_x), anchor_y=float(anchor_y))
        self.accept()

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self._on_insert_clicked()

    def _on_list_scrolled(self, _value: int) -> None:
        self._schedule_auto_load_more_if_needed()

    def _schedule_auto_load_more_if_needed(self) -> None:
        if not self._should_auto_load_more():
            return
        QtCore.QTimer.singleShot(0, self._auto_load_more_if_needed)

    def _auto_load_more_if_needed(self) -> None:
        if not self._should_auto_load_more():
            return
        self._refresh_community(reset=False)

    def _should_auto_load_more(self) -> bool:
        if self._is_loading_remote_scope:
            return False
        if self._scope_tabs.currentIndex() != self._TAB_COMMUNITY:
            return False
        if not self._remote_next_cursor:
            return False
        if self._remote_loaded_query != self._tab_queries[self._TAB_COMMUNITY]:
            return False
        scroll_bar = self._list.verticalScrollBar()
        max_value = int(scroll_bar.maximum())
        if max_value <= 0:
            return True
        return int(scroll_bar.value()) >= max_value - 8



def component_entry_matches_query(entry: F8ComponentEntry, *, query: str) -> bool:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True
    haystack = " ".join(
        [
            str(entry.record.name or ""),
            str(entry.record.description or ""),
            " ".join(str(tag) for tag in list(entry.record.tags or [])),
            str(entry.ownerDisplayName or ""),
        ]
    ).lower()
    return normalized_query in haystack



def installed_component_entries(
    entries: list[F8ComponentEntry],
    *,
    query: str,
    filter_value: str = "all",
    current_user_id: str = "",
) -> list[F8ComponentEntry]:
    filtered = [
        entry
        for entry in entries
        if component_entry_is_installed(entry)
        and component_entry_matches_query(entry, query=query)
    ]
    if filter_value == "mine":
        filtered = [entry for entry in filtered if _is_my_component(entry, current_user_id=current_user_id)]
    elif filter_value == "subscribed":
        filtered = [entry for entry in filtered if bool(entry.subscribed)]
    filtered.sort(key=_component_entry_sort_key)
    return filtered


def community_component_entries(
    entries: list[F8ComponentEntry],
    *,
    query: str,
    filter_value: str = "all",
    current_user_id: str = "",
) -> list[F8ComponentEntry]:
    filtered = [
        entry
        for entry in entries
        if entry.source == F8ComponentSourceKind.remote_public
        and not _is_my_component(entry, current_user_id=current_user_id)
        and component_entry_matches_query(entry, query=query)
    ]
    if filter_value == "subscribed":
        filtered = [entry for entry in filtered if bool(entry.subscribed)]
    elif filter_value == "not_subscribed":
        filtered = [entry for entry in filtered if not bool(entry.subscribed)]
    filtered.sort(key=_component_entry_sort_key)
    return filtered



def component_insert_badges(entry: F8ComponentEntry) -> list[str]:
    badges: list[str] = []
    if entry.source == F8ComponentSourceKind.local:
        badges.append("local")
    elif entry.source == F8ComponentSourceKind.remote_official:
        badges.append("official")
    else:
        badges.append("cloud")
    if entry.visibility is not None:
        badges.append(entry.visibility.value)
    badges.append("installed" if component_entry_is_installed(entry) else "install on insert")
    if entry.subscribed:
        badges.append("subscribed")
    return badges



def _component_entry_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
    return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))


def _is_my_component(entry: F8ComponentEntry, *, current_user_id: str) -> bool:
    if entry.source == F8ComponentSourceKind.local:
        return True
    normalized_current_user_id = str(current_user_id or "").strip()
    if not normalized_current_user_id:
        return False
    return str(entry.ownerUserId or "").strip() == normalized_current_user_id
