from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import copy_model, dump_json, validate_as

from ..common import new_asset_id
from ..components.component_events import subscribe_components_changed
from ..components.component_models import (
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentLocalVersionSummary,
    component_now_iso,
    F8ComponentRecord,
    F8ComponentRemoteVersionEntry,
    F8ComponentSourceKind,
    F8ComponentVisibility,
)
from ..components.component_repository import (
    export_component_to_json,
    import_component_from_json,
    list_component_entries,
    upsert_component,
)
from ..components.component_sync import ComponentSyncClient
from ..components.component_catalog import component_entry_can_hydrate, component_entry_has_cached_content, component_entry_is_installed
from ...nodegraph.component_publish_payload import (
    collect_component_selected_node_ids,
    trim_component_publish_payload_to_selected_nodes,
)
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.ui_notifications import show_info, show_warning
from ...nodegraph.session_schema import extract_layout
from .project_asset_dialogs import (
    AssetVersionBrowserAction,
    AssetVersionBrowserDialog,
    AssetVersionBrowserItem,
    ProjectAssetMetaDialog,
)
from .asset_graph_preview import AssetGraphPreviewPane
from .asset_sync_resolution import AssetSyncDirection, determine_asset_sync_direction
from .catalog_status import AssetCatalogRowState, build_asset_catalog_row_state
from ...ui.support.json_text_editor import attach_json_enhancements
from .asset_cloud_account_menu import build_asset_account_menu, prompt_asset_cloud_sign_in

logger = logging.getLogger(__name__)

_AUTO_PREVIEW_NODE_THRESHOLD = 10
_LOCAL_DRAFT_LABEL = "Local Draft"
_LOCAL_DRAFT_LOAD_TOOLTIP = "Not available for Local Draft"


class ComponentCatalogDialog(QtWidgets.QDialog):
    _TAB_MINE = 0
    _TAB_COMMUNITY = 1
    _TAB_INSTALLED = 2

    def __init__(self, *, parent: QtWidgets.QWidget | None, node_graph: Any) -> None:
        super().__init__(parent)
        self._graph = node_graph
        self._entries: list[F8ComponentEntry] = []
        self._row_states_by_component_id: dict[str, AssetCatalogRowState] = {}
        self._sync_client = ComponentSyncClient()
        self._initial_remote_refresh_done = False
        self._initial_remote_refresh_scheduled = False
        self._tab_queries: dict[int, str] = {
            self._TAB_MINE: "",
            self._TAB_COMMUNITY: "",
            self._TAB_INSTALLED: "",
        }
        self._tab_filters: dict[int, str] = {
            self._TAB_MINE: "all",
            self._TAB_COMMUNITY: "all",
            self._TAB_INSTALLED: "all",
        }
        self._remote_next_cursor_by_scope: dict[str, str | None] = {"mine": None, "community": None}
        self._remote_loaded_query_by_scope: dict[str, str] = {"mine": "", "community": ""}
        self._is_loading_remote_scope = False
        self._components_changed_unsubscribe: Callable[[], None] | None = subscribe_components_changed(
            self._on_components_changed
        )
        self.setWindowTitle("Components")
        self.resize(1180, 760)

        self._scope_tabs = QtWidgets.QTabBar(self)
        self._scope_tabs.addTab("Mine")
        self._scope_tabs.addTab("Community")
        self._scope_tabs.addTab("Installed")
        self._scope_tabs.currentChanged.connect(self._reload)  # type: ignore[attr-defined]

        self._account_button = QtWidgets.QToolButton(self)
        self._account_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER))
        self._account_button.setToolTip("Accounts")
        self._account_button.clicked.connect(self._on_accounts_clicked)  # type: ignore[attr-defined]

        self._search_input = QtWidgets.QLineEdit(self)
        self._search_input.setPlaceholderText("Search components")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(320)
        self._search_input.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_input.returnPressed.connect(self._on_search_submitted)  # type: ignore[attr-defined]
        self._search_btn = QtWidgets.QPushButton(self)
        self._search_btn.setIcon(icon_for(self._search_btn, StudioIcon.CLOUD_SEARCH))
        self._search_btn.clicked.connect(self._on_search_submitted)  # type: ignore[attr-defined]
        self._search_btn.setToolTip("Search current list")
        self._search_btn.setFixedWidth(30)
        self._filter_combo = QtWidgets.QComboBox(self)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)  # type: ignore[attr-defined]

        self._toolbar = QtWidgets.QToolBar("Components", self)
        self._toolbar.setMovable(False)
        self._toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._toolbar.setIconSize(QtCore.QSize(16, 16))
        self._toolbar.addWidget(self._scope_tabs)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._search_input)
        self._toolbar.addWidget(self._search_btn)
        self._toolbar.addWidget(self._filter_combo)
        self._toolbar.addSeparator()

        btn_add = QtWidgets.QPushButton(self)
        btn_refresh = QtWidgets.QPushButton(self)
        btn_import = QtWidgets.QPushButton(self)
        btn_export = QtWidgets.QPushButton(self)

        button_specs = [
            (btn_add, StudioIcon.CIRCLE_PLUS, "Save As Component"),
            (btn_refresh, StudioIcon.REFRESH, "Refresh current list"),
            (btn_import, StudioIcon.PACKAGE_IMPORT, "Import JSON"),
            (btn_export, StudioIcon.PACKAGE_EXPORT, "Export JSON"),
        ]
        for button, icon_token, tooltip in button_specs:
            button.setIcon(icon_for(button, icon_token))
            button.setToolTip(tooltip)
            button.setText("")
            button.setFixedWidth(30)

        btn_add.clicked.connect(self._on_add_clicked)  # type: ignore[attr-defined]
        btn_refresh.clicked.connect(self._on_refresh_clicked)  # type: ignore[attr-defined]
        btn_import.clicked.connect(self._on_import_clicked)  # type: ignore[attr-defined]
        btn_export.clicked.connect(self._on_export_clicked)  # type: ignore[attr-defined]

        self._toolbar.addWidget(btn_add)
        self._toolbar.addWidget(btn_import)
        self._toolbar.addWidget(btn_export)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(btn_refresh)
        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.addWidget(self._toolbar)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._account_button)

        self._list = QtWidgets.QListWidget(self)
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._list.setSpacing(3)
        self._list.setStyleSheet("QListWidget::item { border: 0; padding: 0; }")
        self._list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)  # type: ignore[attr-defined]
        self._list.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)  # type: ignore[attr-defined]
        self._list.customContextMenuRequested.connect(self._on_list_context_menu_requested)  # type: ignore[attr-defined]
        self._preview = AssetGraphPreviewPane(parent=self, host_graph=node_graph)
        self._raw = QtWidgets.QPlainTextEdit(self)
        self._raw.setReadOnly(True)
        self._raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        attach_json_enhancements(self._raw, read_only=True)
        self._detail_tabs = QtWidgets.QTabWidget(self)
        self._detail_tabs.addTab(self._preview, "Preview")
        self._detail_tabs.addTab(self._raw, "Raw")

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._detail_tabs)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(toolbar_row)
        layout.addWidget(split, 1)
        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        self._reload()

    @staticmethod
    def _set_button_state(
        button: QtWidgets.QPushButton,
        *,
        visible: bool,
        enabled: bool,
        tooltip: str,
        icon_token: StudioIcon,
    ) -> None:
        button.setVisible(visible)
        button.setEnabled(enabled)
        button.setToolTip(tooltip)
        button.setIcon(icon_for(button, icon_token))

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
        self._schedule_initial_remote_refresh_if_needed()
        self._row_states_by_component_id = self._build_row_states()
        self._entries = self._entries_for_current_tab()
        logger.debug(
            "Component manager reload tab=%s count=%d entries=%s",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            len(self._entries),
            [
                {
                    "componentId": str(entry.record.componentId),
                    "source": entry.source.value,
                    "visibility": None if entry.visibility is None else entry.visibility.value,
                    "installed": bool(entry.installed),
                    "subscribed": bool(entry.subscribed),
                }
                for entry in self._entries[:10]
            ],
        )
        self._list.clear()
        for entry in self._entries:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, entry.record.componentId)
            row_widget = self._build_list_row(entry)
            item.setSizeHint(row_widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row_widget)
        self._account_button.setToolTip(self._account_button_text())
        self._search_input.blockSignals(True)
        self._search_input.setText(self._current_query())
        self._search_input.blockSignals(False)
        self._reload_filter_combo()
        self._refresh_auth_controls()
        self._on_selection_changed()
        self._schedule_auto_load_more_if_needed()

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
        self._reload()

    def _entries_for_current_tab(self) -> list[F8ComponentEntry]:
        current_tab = self._scope_tabs.currentIndex()
        service = self._sync_client._catalog_service
        normalized_query = self._current_query().lower()
        local_entries = service._local_provider.load_entries()
        remote_entries = service._remote_provider.load_entries()
        if current_tab == self._TAB_COMMUNITY:
            return sorted(
                [
                    entry
                    for entry in remote_entries
                    if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
                ],
                key=self._entry_sort_key,
            )
        if current_tab == self._TAB_MINE:
            merged: dict[str, F8ComponentEntry] = {
                str(entry.record.componentId): entry
                for entry in local_entries
                if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
            }
            for entry in remote_entries:
                if self._is_owned_remote_entry(entry) and self._entry_matches_query(entry, normalized_query):
                    component_id = str(entry.record.componentId)
                    existing_entry = merged.get(component_id)
                    if existing_entry is None:
                        merged[component_id] = entry
                    else:
                        merged[component_id] = self._merge_entries_for_mine_tab(existing_entry, entry)
            return sorted(merged.values(), key=self._entry_sort_key)
        return [
            entry for entry in sorted(list_component_entries(include_uninstalled=True), key=self._entry_sort_key)
            if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
        ]

    @staticmethod
    def _merge_entries_for_mine_tab(existing_entry: F8ComponentEntry, incoming_entry: F8ComponentEntry) -> F8ComponentEntry:
        if incoming_entry.source != F8ComponentSourceKind.local:
            preferred_entry = incoming_entry
            fallback_entry = existing_entry
        elif existing_entry.source != F8ComponentSourceKind.local:
            preferred_entry = existing_entry
            fallback_entry = incoming_entry
        else:
            preferred_entry = incoming_entry
            fallback_entry = existing_entry
        if component_entry_has_cached_content(preferred_entry):
            return preferred_entry
        if not component_entry_has_cached_content(fallback_entry):
            return preferred_entry
        merged_record = copy_model(
            preferred_entry.record,
            update={
                "content": fallback_entry.record.content,
            },
        )
        return copy_model(
            preferred_entry,
            update={
                "record": merged_record,
                "installed": True,
                "downloadedAt": preferred_entry.downloadedAt or fallback_entry.downloadedAt,
            },
        )

    def _matches_filter(self, entry: F8ComponentEntry) -> bool:
        row_state = self._row_state_for_entry(entry)
        current_tab = self._scope_tabs.currentIndex()
        current_filter = self._current_filter_value()
        if current_tab == self._TAB_MINE:
            if not self._is_mine_entry(entry):
                return False
            if current_filter == "local":
                return row_state.has_local_head
            if current_filter == "private":
                return row_state.has_remote_head and row_state.visibility == F8ComponentVisibility.private.value
            if current_filter == "shared":
                return self._is_owned_remote_shared_entry(entry)
            return True
        if current_tab == self._TAB_COMMUNITY:
            if not self._is_community_entry(entry):
                return False
            if current_filter == "subscribed":
                return bool(entry.subscribed)
            if current_filter == "not_subscribed":
                return not bool(entry.subscribed)
            return True
        if not row_state.has_local_presence:
            return False
        if current_filter == "mine":
            return row_state.has_local_head or self._is_owned_remote_entry(entry)
        if current_filter == "subscribed":
            return row_state.subscribed and not self._is_owned_remote_entry(entry)
        return True

    @staticmethod
    def _entry_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
        return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))

    def _build_list_row(self, entry: F8ComponentEntry) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self._list)
        container.setObjectName("catalogRowCard")
        container.setStyleSheet(
            "QWidget#catalogRowCard {"
            " border: 1px solid #4b5563;"
            " border-radius: 10px;"
            " background: #20252c;"
            "}"
        )
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        row_state = self._row_state_for_entry(entry)
        if row_state.subscribed:
            icon_label = QtWidgets.QLabel(container)
            icon_label.setPixmap(icon_for(container, StudioIcon.HEART_ON).pixmap(14, 14))
            icon_label.setToolTip("Subscribed")
            title_row.addWidget(icon_label)
        name_label = QtWidgets.QLabel(str(entry.record.name or ""), container)
        font = name_label.font()
        font.setBold(True)
        name_label.setFont(font)
        name_label.setStyleSheet("color: palette(window-text);")
        title_row.addWidget(name_label, 1)
        owner_label_text = self._owner_label_text(row_state.owner_display_name)
        if owner_label_text is not None:
            owner_label = QtWidgets.QLabel(owner_label_text, container)
            owner_label.setStyleSheet("color: palette(window-text);")
            title_row.addWidget(owner_label, 0)
        root.addLayout(title_row)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        visibility_badge = self._build_visibility_badge(container, row_state)
        if visibility_badge is not None:
            meta_row.addWidget(visibility_badge, 0)
        sync_badge = self._build_sync_badge(container, row_state)
        if sync_badge is not None:
            meta_row.addWidget(sync_badge, 0)
        version_badge = self._build_version_badge(container, row_state)
        if version_badge is not None:
            meta_row.addWidget(version_badge, 0)
        meta_row.addStretch(1)
        root.addLayout(meta_row)

        if entry.record.description:
            description_label = QtWidgets.QLabel(str(entry.record.description or ""), container)
            description_label.setWordWrap(True)
            description_label.setStyleSheet("color: palette(mid);")
            root.addWidget(description_label)
        return container

    @staticmethod
    def _build_text_badge(parent: QtWidgets.QWidget, text: str) -> QtWidgets.QLabel:
        badge = QtWidgets.QLabel(str(text), parent)
        badge.setStyleSheet(
            "QLabel {"
            " border: 1px solid #596273;"
            " border-radius: 9px;"
            " padding: 1px 6px;"
            " color: #d7dde7;"
            " background: #2a3038;"
            "}"
        )
        return badge

    def _build_version_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        version_text = row_state.compact_version_badge()
        if version_text is None:
            return None
        sync_key = row_state.sync_indicator_key()
        local_color = "#cbd5e1"
        remote_color = "#cbd5e1"
        if sync_key == "push":
            local_color = "#86efac"
            remote_color = "#94a3b8"
        elif sync_key == "pull":
            local_color = "#94a3b8"
            remote_color = "#93c5fd"
        elif sync_key == "conflict":
            local_color = "#fca5a5"
            remote_color = "#fdba74"
        badge = self._build_text_badge(parent, "")
        badge.setTextFormat(QtCore.Qt.TextFormat.RichText)
        parts: list[str] = []
        if row_state.local_version_number is not None:
            parts.append(f"<span style='color:{local_color};font-weight:600;'>L{int(row_state.local_version_number)}</span>")
        if row_state.remote_version_number is not None:
            parts.append(f"<span style='color:{remote_color};font-weight:600;'>R{int(row_state.remote_version_number)}</span>")
        badge.setText(" <span style='color:#64748b;'>|</span> ".join(parts))
        if sync_key == "push":
            badge.setToolTip("Local version is ahead of remote")
        elif sync_key == "pull":
            badge.setToolTip("Remote version is ahead of local")
        elif sync_key == "conflict":
            badge.setToolTip("Local and remote versions diverged")
        else:
            badge.setToolTip(version_text)
        return badge

    @staticmethod
    def _sync_badge_token(row_state: AssetCatalogRowState) -> StudioIcon | None:
        sync_key = row_state.sync_indicator_key()
        if sync_key == "synced":
            return StudioIcon.CHECK
        if sync_key == "push":
            return StudioIcon.CLOUD_UP
        if sync_key == "pull":
            return StudioIcon.CLOUD_DOWN
        if sync_key == "conflict":
            return StudioIcon.X
        return None

    def _build_sync_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        token = self._sync_badge_token(row_state)
        if token is None:
            return None
        badge = self._build_text_badge(parent, "")
        badge.setPixmap(icon_for(parent, token).pixmap(12, 12))
        sync_key = row_state.sync_indicator_key()
        if sync_key == "synced":
            badge.setToolTip("Local and remote are in sync")
        elif sync_key == "push":
            badge.setToolTip("Local is ahead of remote")
        elif sync_key == "pull":
            badge.setToolTip("Remote is ahead of local")
        elif sync_key == "conflict":
            badge.setToolTip("Local and remote changed differently")
        return badge

    def _build_visibility_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        visibility_key = row_state.visibility_icon_key()
        if visibility_key == "public":
            token = StudioIcon.PUBLIC
            tooltip = "Public"
        elif visibility_key == "private":
            token = StudioIcon.PRIVATE
            tooltip = "Private"
        else:
            return None
        badge = self._build_text_badge(parent, "")
        badge.setPixmap(icon_for(parent, token).pixmap(12, 12))
        badge.setToolTip(tooltip)
        return badge

    def _build_row_states(self) -> dict[str, AssetCatalogRowState]:
        service = self._sync_client._catalog_service
        local_entries = service._local_provider.load_entries()
        remote_entries = service._remote_provider.load_entries()
        local_by_id = {
            str(entry.record.componentId): entry
            for entry in local_entries
            if str(entry.record.componentId).strip()
        }
        remote_by_id = {
            str(entry.record.componentId): entry
            for entry in remote_entries
            if str(entry.record.componentId).strip()
        }
        row_states: dict[str, AssetCatalogRowState] = {}
        for component_id in sorted(set(local_by_id) | set(remote_by_id)):
            row_states[component_id] = component_row_state_for_entries(
                component_id=component_id,
                local_entry=local_by_id.get(component_id),
                remote_entry=remote_by_id.get(component_id),
            )
        return row_states

    def _row_state_for_entry(self, entry: F8ComponentEntry) -> AssetCatalogRowState:
        component_id = str(entry.record.componentId or "").strip()
        if component_id:
            row_state = self._row_states_by_component_id.get(component_id)
            if row_state is not None:
                return row_state
        return component_row_state_for_entries(
            component_id=component_id,
            local_entry=entry if entry.source == F8ComponentSourceKind.local else None,
            remote_entry=entry if entry.source != F8ComponentSourceKind.local else None,
        )

    def _source_text(self, entry: F8ComponentEntry) -> str:
        if entry.source == F8ComponentSourceKind.local:
            return "local"
        if entry.source == F8ComponentSourceKind.remote_official:
            return "official"
        if entry.source == F8ComponentSourceKind.remote_private:
            return "mine"
        if self._is_owned_remote_shared_entry(entry):
            return "shared"
        return "community"

    def _refresh_remote_catalog_if_needed(self) -> None:
        if self._initial_remote_refresh_done:
            return
        self._initial_remote_refresh_done = True
        try:
            community_page = self._sync_client.refresh_scope_page(
                scope="community",
                query=self._tab_queries[self._TAB_COMMUNITY],
                cursor="",
                append=False,
            )
            self._remote_next_cursor_by_scope["community"] = community_page.nextCursor
            self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
            if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
                try:
                    self._sync_client.refresh_auth()
                except Exception:
                    logger.exception("Component manager initial auth refresh failed")
                if self._sync_client.current_access_token():
                    mine_page = self._sync_client.refresh_scope_page(
                        scope="mine",
                        query=self._tab_queries[self._TAB_MINE],
                        cursor="",
                        append=False,
                    )
                    self._remote_next_cursor_by_scope["mine"] = mine_page.nextCursor
                    self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
        except Exception:
            logger.exception("Component manager initial remote refresh failed")

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

    def _refresh_action_buttons(self, selected_entry: F8ComponentEntry | None) -> None:
        _ = selected_entry

    def _on_selection_changed(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._raw.setPlainText("")
            self._preview.clear_preview("Select a component to preview.")
            self._refresh_action_buttons(None)
            return
        hydration_error = ""
        if component_entry_can_hydrate(selected_entry) and not component_entry_has_cached_content(selected_entry):
            try:
                selected_entry = self._sync_client.hydrate_component(str(selected_entry.record.componentId))
            except Exception as exc:
                hydration_error = str(exc)
        if hydration_error:
            self._raw.setPlainText(
                json.dumps(
                    {
                        "componentId": str(selected_entry.record.componentId),
                        "operation": "hydrate_component",
                        "error": hydration_error,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            self._preview.clear_preview(f"Failed to preview component.\n{hydration_error}")
        else:
            self._raw.setPlainText(json.dumps(dump_json(selected_entry, mode="json"), ensure_ascii=False, indent=2, default=str))
            preview_node_count = self._component_preview_node_count(selected_entry.record.content)
            if preview_node_count > _AUTO_PREVIEW_NODE_THRESHOLD:
                self._preview.show_deferred_component_payload(
                    selected_entry.record.content,
                    message=(
                        f"This component has {preview_node_count} nodes.\n"
                        "Automatic preview is paused to keep browsing fast."
                    ),
                    button_text="Load preview manually",
                )
            else:
                self._preview.show_component_payload(selected_entry.record.content)
        self._refresh_action_buttons(selected_entry)

    @staticmethod
    def _component_preview_node_count(payload: Any) -> int:
        if not isinstance(payload, dict):
            return 0
        try:
            layout = extract_layout(payload)
        except ValueError:
            return 0
        nodes = layout.get("nodes")
        if not isinstance(nodes, dict):
            return 0
        return len(nodes)

    def _refresh_auth_controls(self) -> None:
        logged_in = self._sync_client.current_user() is not None and bool(self._sync_client.current_access_token())
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

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self._on_insert_clicked()

    def _on_list_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is not None:
            self._list.setCurrentItem(item)
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        menu = self._build_list_context_menu(
            current_tab=current_tab,
            selected_entry=selected_entry,
            local_entry=local_entry,
            remote_entry=remote_entry,
        )
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _build_list_context_menu(
        self,
        *,
        current_tab: int,
        selected_entry: F8ComponentEntry,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)
        if current_tab == self._TAB_MINE:
            can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
            edit_action = menu.addAction("Edit Metadata")
            edit_action.setEnabled(local_entry is not None)
            edit_action.triggered.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
            load_action = menu.addAction("Offload" if can_offload else "Load")
            load_action.setEnabled(can_load or can_offload)
            load_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete")
            delete_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
            sync_action = menu.addAction("Sync")
            sync_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            sync_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            visibility_label = "Make Public"
            if remote_entry is not None and remote_entry.visibility == F8ComponentVisibility.public:
                visibility_label = "Make Private"
            visibility_action = menu.addAction(visibility_label)
            visibility_action.setEnabled(remote_entry is not None and self._is_owned_remote_entry(remote_entry))
            visibility_action.triggered.connect(self._on_visibility_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        elif current_tab == self._TAB_COMMUNITY:
            subscribe_action = menu.addAction("Unsubscribe" if selected_entry.subscribed else "Subscribe")
            subscribe_action.setEnabled(
                selected_entry.source == F8ComponentSourceKind.remote_public and not self._is_owned_remote_entry(selected_entry)
            )
            subscribe_action.triggered.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        else:
            offload_action = menu.addAction("Offload")
            offload_action.setEnabled(local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)))
            offload_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            pull_action = menu.addAction("Pull")
            pull_action.setEnabled(remote_entry is not None and component_entry_is_installed(remote_entry))
            pull_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        if component_entry_is_installed(selected_entry):
            menu.addSeparator()
            insert_action = menu.addAction("Create on canvas")
            insert_action.triggered.connect(self._on_insert_clicked)  # type: ignore[attr-defined]
        return menu

    def _on_add_clicked(self) -> None:
        graph = self._graph
        if graph is None:
            return
        dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Save As Component",
            name="Untitled Component",
            description="",
            tags=[],
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            selected_nodes = list(graph.selected_nodes() or [])
            selected_node_ids = collect_component_selected_node_ids(selected_nodes)
            if selected_nodes and not selected_node_ids:
                show_warning(self, "Save component failed", "Selected nodes are missing stable ids.")
                return
            payload = graph.serialize_publish_session()
            if selected_node_ids:
                payload = trim_component_publish_payload_to_selected_nodes(
                    payload=payload,
                    selected_node_ids=selected_node_ids,
                )
            name, description, tags = dialog.values()
            record = F8ComponentRecord(
                componentId=new_asset_id(),
                name=name,
                description=description,
                tags=tags,
                content=payload,
            )
            upsert_component(record)
        except Exception as exc:
            logger.exception("Component catalog save component failed")
            show_warning(self, "Save component failed", f"Failed to save component.\n\n{exc}")
            return
        show_info(self, "Saved", f"Saved component:\n{record.name}")

    @staticmethod
    def _is_local_draft_entry(entry: F8ComponentEntry | None) -> bool:
        return entry is not None and entry.isLocalDraft

    @classmethod
    def _load_action_availability(
        cls,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> tuple[bool, bool]:
        if cls._is_local_draft_entry(local_entry):
            return False, False
        can_load = remote_entry is not None and not component_entry_is_installed(remote_entry)
        can_offload = local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry))
        return can_load, can_offload

    @staticmethod
    def _load_action_tooltip(*, can_offload: bool, local_entry: F8ComponentEntry | None) -> str:
        if local_entry is not None and local_entry.isLocalDraft:
            return _LOCAL_DRAFT_LOAD_TOOLTIP
        if can_offload:
            return "Offload"
        return "Load"

    @staticmethod
    def _owner_label_text(owner_display_name: str | None) -> str | None:
        if owner_display_name is None:
            return None
        owner_text = str(owner_display_name).strip()
        if not owner_text:
            return None
        if owner_text.casefold() == _LOCAL_DRAFT_LABEL.casefold():
            return _LOCAL_DRAFT_LABEL
        return f"by {owner_text}"

    def _on_edit_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or selected_entry.source != F8ComponentSourceKind.local:
            return
        record = selected_entry.record
        dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Edit Component Metadata",
            name=record.name,
            description=record.description,
            tags=list(record.tags or []),
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = dialog.values()
        updated_record = validate_as(
            F8ComponentRecord,
            {
                **dump_json(record, mode="json"),
                "name": name,
                "description": description,
                "tags": tags,
                "updatedAt": component_now_iso(),
            },
        )
        upsert_component(updated_record)

    def _on_delete_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        has_owned_remote = remote_entry is not None and self._is_owned_remote_entry(remote_entry)
        if local_entry is None and not has_owned_remote:
            return
        title = "Delete component"
        prompt = f"Delete component '{selected_entry.record.name}'?"
        if local_entry is not None and has_owned_remote:
            prompt = f"Delete local and remote component '{selected_entry.record.name}'?"
        elif has_owned_remote:
            prompt = f"Delete remote component '{selected_entry.record.name}'?"
        elif local_entry is not None:
            prompt = f"Delete local component '{selected_entry.record.name}'?"
        answer = QtWidgets.QMessageBox.question(self, title, prompt)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            if local_entry is not None:
                _ = self._sync_client._catalog_service.delete_local_entry(str(local_entry.record.componentId))
            if has_owned_remote and remote_entry is not None:
                self._sync_client.delete_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Delete failed", str(exc))
            return
        self._reload()

    def _on_copy_local_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        copied = validate_as(
            F8ComponentRecord,
            {
                **dump_json(selected_entry.record, mode="json"),
                "componentId": new_asset_id(),
                "updatedAt": component_now_iso(),
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(
            F8ComponentEntry(
                record=copied,
                source=F8ComponentSourceKind.local,
                isLocalDraft=True,
                draftOriginKind=(
                    F8ComponentDraftOriginKind.copy_local
                    if selected_entry.source == F8ComponentSourceKind.local
                    else F8ComponentDraftOriginKind.copy_remote
                ),
                draftOriginAssetId=str(selected_entry.record.componentId),
                draftOriginRevision=selected_entry.remoteRevision,
            )
        )
        show_info(self, "Draft Created", f"Created local draft:\n{copied.name}")

    def _on_upload_clicked(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_INSTALLED:
            pulled = self._pull_selected_component()
            if pulled is not None:
                show_info(self, "Pulled", f"Pulled component:\n{pulled.record.name}")
            return
        synced = self._sync_selected_component()
        if synced is not None:
            show_info(self, "Synced", f"Synced component:\n{synced.record.name}")

    def _on_install_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        if self._is_local_draft_entry(local_entry):
            return
        if local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)):
            offloaded_name = str(selected_entry.record.name or "")
            if self._offload_selected_component(local_entry=local_entry, remote_entry=remote_entry):
                show_info(self, "Offloaded", f"Offloaded component:\n{offloaded_name}")
            return
        if remote_entry is None:
            return
        try:
            installed = self._sync_client.hydrate_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        try:
            installed = self._ensure_owned_remote_component_has_local_head(installed)
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        show_info(self, "Loaded", f"Loaded component:\n{installed.record.name}")
        self._reload()

    def _on_subscribe_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if not self._ensure_logged_in():
            return
        try:
            if selected_entry.subscribed:
                updated = self._sync_client.unsubscribe_component(str(selected_entry.record.componentId))
                show_info(self, "Unsubscribed", f"Removed subscription:\n{updated.record.name}")
            else:
                updated = self._sync_client.subscribe_component(str(selected_entry.record.componentId))
                show_info(self, "Subscribed", f"Subscribed to component:\n{updated.record.name}")
        except Exception as exc:
            show_warning(self, "Subscription failed", str(exc))
            return
        self._reload()

    def _on_history_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if selected_entry.source == F8ComponentSourceKind.local:
            self._show_local_history(selected_entry)
            return
        self._show_remote_history(selected_entry)

    def _on_visibility_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or not self._is_owned_remote_entry(selected_entry):
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        next_visibility = F8ComponentVisibility.public
        prompt = "Make this remote component public?"
        if selected_entry.visibility == F8ComponentVisibility.public:
            next_visibility = F8ComponentVisibility.private
            prompt = "Make this remote component private?"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Change visibility",
            prompt,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            self._sync_client.update_component_visibility(
                str(selected_entry.record.componentId),
                visibility=next_visibility,
                revision=selected_entry.remoteRevision,
            )
        except Exception as exc:
            show_warning(self, "Visibility update failed", str(exc))
            return
        self._reload()

    def _offload_selected_component(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> bool:
        changed = False
        if local_entry is not None:
            changed = self._sync_client._catalog_service.delete_local_entry(str(local_entry.record.componentId)) or changed
        if remote_entry is not None and component_entry_is_installed(remote_entry):
            changed = self._sync_client._catalog_service.uninstall_remote_entry(str(remote_entry.record.componentId)) is not None or changed
        if changed:
            self._reload()
        return changed

    def _replace_local_component_head(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        component_id = str(entry.record.componentId or "").strip()
        if component_id:
            _ = self._sync_client._catalog_service.delete_local_entry(component_id)
        return self._sync_client._catalog_service.upsert_local_entry(entry)

    def _component_sync_decision(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> AssetSyncDirection:
        decision = determine_asset_sync_direction(
            has_local_entry=local_entry is not None,
            has_remote_entry=remote_entry is not None,
            local_version_number=None if local_entry is None else local_entry.localVersionNumber,
            remote_version_number=None if remote_entry is None else remote_entry.remoteVersionNumber,
            sync_base_remote_revision=None if local_entry is None else local_entry.syncBaseRemoteRevision,
            sync_base_remote_version_number=None if local_entry is None else local_entry.syncBaseRemoteVersionNumber,
            sync_base_local_version_number=None if local_entry is None else local_entry.syncBaseLocalVersionNumber,
            current_remote_revision=None if remote_entry is None else remote_entry.remoteRevision,
        )
        return decision.direction

    @staticmethod
    def _local_working_copy_from_remote_entry(
        remote_entry: F8ComponentEntry,
        *,
        record: F8ComponentRecord | None = None,
        mark_modified: bool,
    ) -> F8ComponentEntry:
        remote_version_number = None if remote_entry.remoteVersionNumber is None else int(remote_entry.remoteVersionNumber)
        local_version_number: int | None = remote_version_number
        sync_base_local_version_number: int | None = remote_version_number
        if mark_modified:
            local_version_number = 1 if remote_version_number is None else remote_version_number + 1
        return copy_model(
            remote_entry,
            update={
                "record": remote_entry.record if record is None else record,
                "source": F8ComponentSourceKind.local,
                "installed": True,
                "hasCachedContent": True,
                "localVersionNumber": local_version_number,
                "syncBaseRemoteRevision": remote_entry.remoteRevision,
                "syncBaseRemoteVersionNumber": remote_version_number,
                "syncBaseLocalVersionNumber": sync_base_local_version_number,
                "isLocalDraft": False,
                "draftOriginKind": None,
                "draftOriginAssetId": None,
                "draftOriginRevision": None,
            },
        )

    def _ensure_owned_remote_component_has_local_head(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        if not self._is_owned_remote_entry(entry):
            return entry
        existing_local_entry = self._local_entry_for_component_id(str(entry.record.componentId))
        if existing_local_entry is not None:
            return existing_local_entry
        return self._sync_client._catalog_service.upsert_local_entry(
            self._local_working_copy_from_remote_entry(
                entry,
                mark_modified=False,
            )
        )

    def _sync_selected_component(self) -> F8ComponentEntry | None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return None
        if not self._ensure_logged_in():
            return None
        direction = self._component_sync_decision(local_entry=local_entry, remote_entry=remote_entry)
        if direction == AssetSyncDirection.pull:
            return self._pull_selected_component()
        if direction == AssetSyncDirection.conflict:
            resolution = self._prompt_component_conflict_resolution(include_push=True)
            if resolution == "push":
                return self._push_selected_component(local_entry=local_entry, remote_entry=remote_entry)
            if resolution == "replace":
                return self._pull_selected_component(force_replace_local=True)
            if resolution == "fork_pull":
                if not self._fork_local_component_conflict_copy(local_entry):
                    return None
                return self._pull_selected_component(force_replace_local=True)
            return None
        if direction == AssetSyncDirection.noop:
            return None
        return self._push_selected_component(local_entry=local_entry, remote_entry=remote_entry)

    def _push_selected_component(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> F8ComponentEntry | None:
        if local_entry is None:
            return None
        entry_to_upload = self._ensure_component_hydrated(local_entry, operation_name="Load component")
        if entry_to_upload is None:
            return None
        if remote_entry is not None:
            entry_to_upload = copy_model(
                entry_to_upload,
                update={
                    "source": remote_entry.source,
                    "visibility": remote_entry.visibility,
                    "remoteRevision": remote_entry.remoteRevision,
                    "remoteVersionNumber": remote_entry.remoteVersionNumber,
                    "syncBaseRemoteRevision": remote_entry.remoteRevision,
                    "syncBaseRemoteVersionNumber": remote_entry.remoteVersionNumber,
                    "installed": True,
                    "hasCachedContent": True,
                },
            )
        else:
            visibility = self._choose_visibility()
            if visibility is None:
                return None
            source = F8ComponentSourceKind.remote_private if visibility == F8ComponentVisibility.private else F8ComponentSourceKind.remote_public
            entry_to_upload = validate_as(
                F8ComponentEntry,
                {
                    **dump_json(entry_to_upload, mode="json"),
                    "source": source.value,
                    "visibility": visibility.value,
                    "installed": True,
                },
            )
        try:
            uploaded = self._sync_client.upload_entry(entry_to_upload)
        except Exception as exc:
            show_warning(self, "Sync failed", str(exc))
            return None
        saved_local_entry = copy_model(
            local_entry,
            update={
                "syncBaseRemoteRevision": uploaded.remoteRevision,
                "syncBaseRemoteVersionNumber": uploaded.remoteVersionNumber,
                "syncBaseLocalVersionNumber": local_entry.localVersionNumber,
                "remoteRevision": uploaded.remoteRevision,
                "remoteVersionNumber": uploaded.remoteVersionNumber,
                "syncState": uploaded.syncState,
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(saved_local_entry)
        self._reload()
        return uploaded

    def _pull_selected_component(self, *, force_replace_local: bool = False) -> F8ComponentEntry | None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None or remote_entry is None:
            return None
        if local_entry is not None and not force_replace_local:
            direction = self._component_sync_decision(local_entry=local_entry, remote_entry=remote_entry)
            if direction == AssetSyncDirection.conflict:
                resolution = self._prompt_component_conflict_resolution(include_push=False)
                if resolution == "replace":
                    return self._pull_selected_component(force_replace_local=True)
                if resolution == "fork_pull":
                    if not self._fork_local_component_conflict_copy(local_entry):
                        return None
                    return self._pull_selected_component(force_replace_local=True)
                return None
        try:
            pulled = self._sync_client.hydrate_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Pull failed", str(exc))
            return None
        if local_entry is not None:
            replacement_entry = self._local_working_copy_from_remote_entry(
                pulled,
                record=pulled.record,
                mark_modified=False,
            )
            _ = self._replace_local_component_head(replacement_entry)
        else:
            pulled = self._ensure_owned_remote_component_has_local_head(pulled)
        self._reload()
        return pulled

    def _fork_local_component_conflict_copy(self, local_entry: F8ComponentEntry | None) -> bool:
        if local_entry is None:
            return False
        forked_record = validate_as(
            F8ComponentRecord,
            {
                **dump_json(local_entry.record, mode="json"),
                "componentId": new_asset_id(),
                "name": f"{str(local_entry.record.name or '').strip()} (Draft Copy)",
                "updatedAt": component_now_iso(),
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(
            F8ComponentEntry(
                record=forked_record,
                source=F8ComponentSourceKind.local,
                isLocalDraft=True,
                draftOriginKind=F8ComponentDraftOriginKind.copy_local,
                draftOriginAssetId=str(local_entry.record.componentId),
                draftOriginRevision=local_entry.remoteRevision,
            )
        )
        return True

    def _prompt_component_conflict_resolution(self, *, include_push: bool) -> str:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Sync conflict")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText("Local and remote both changed from different revisions.")
        box.setInformativeText("Choose how to resolve this conflict.")
        push_button = None
        if include_push:
            push_button = box.addButton("Push local as new revision", QtWidgets.QMessageBox.AcceptRole)
        replace_button = box.addButton("Replace local with remote", QtWidgets.QMessageBox.DestructiveRole)
        fork_button = box.addButton("Copy current work to draft and pull remote", QtWidgets.QMessageBox.ActionRole)
        cancel_button = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        clicked = box.clickedButton()
        if include_push and clicked is push_button:
            return "push"
        if clicked is replace_button:
            return "replace"
        if clicked is fork_button:
            return "fork_pull"
        return "cancel"

    def _on_insert_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        graph = self._graph
        if graph is None:
            return
        try:
            request = graph.prepare_insert_graph_from_component(selected_entry.record.content, component_name=selected_entry.record.name)
        except Exception as exc:
            show_warning(self, "Create on canvas failed", str(exc))
            return
        graph.begin_graph_placement(request, label=f"Component: {selected_entry.record.name}\n{request.node_count} nodes")

    def _on_import_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import Component JSON", "", "JSON (*.json);;All Files (*)")
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Import Component",
            name="Imported Component",
            description="",
            tags=[],
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = dialog.values()
        try:
            import_component_from_json(
                selected_path,
                metadata={
                    "componentId": new_asset_id(),
                    "name": name,
                    "description": description,
                    "tags": tags,
                },
            )
        except Exception as exc:
            show_warning(self, "Import failed", str(exc))
            return

    def _on_export_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Component JSON", selected_entry.record.name, "JSON (*.json);;All Files (*)")
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        try:
            out_path = export_component_to_json(selected_entry.record.componentId, selected_path)
        except Exception as exc:
            show_warning(self, "Export failed", str(exc))
            return
        show_info(self, "Exported", f"Saved:\n{out_path}")

    def _local_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self._sync_client._catalog_service._local_provider.load_entries():
            if str(entry.record.componentId or "").strip() == normalized_component_id:
                return entry
        return None

    def _remote_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self._sync_client._catalog_service._remote_provider.load_entries():
            if str(entry.record.componentId or "").strip() == normalized_component_id:
                return entry
        return None

    def _selected_action_entries(self) -> tuple[F8ComponentEntry | None, F8ComponentEntry | None, F8ComponentEntry | None]:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return None, None, None
        component_id = str(selected_entry.record.componentId or "").strip()
        return (
            selected_entry,
            self._local_entry_for_component_id(component_id),
            self._remote_entry_for_component_id(component_id),
        )

    def _on_accounts_clicked(self) -> None:
        menu = build_asset_account_menu(parent=self, sync_client=self._sync_client, on_changed=self._on_account_state_changed)
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

    def _remote_scope_for_current_tab(self) -> str | None:
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_COMMUNITY:
            return "community"
        if current_tab == self._TAB_MINE:
            return "mine"
        return None

    def _entry_matches_query(self, entry: F8ComponentEntry, normalized_query: str) -> bool:
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

    def _is_owned_remote_entry(self, entry: F8ComponentEntry) -> bool:
        current_user = self._sync_client.current_user()
        if current_user is None:
            return False
        if entry.source not in {F8ComponentSourceKind.remote_public, F8ComponentSourceKind.remote_private}:
            return False
        return str(entry.ownerUserId or "") == str(current_user.userId)

    def _is_owned_remote_shared_entry(self, entry: F8ComponentEntry) -> bool:
        return self._is_owned_remote_entry(entry) and entry.visibility == F8ComponentVisibility.public

    def _is_mine_entry(self, entry: F8ComponentEntry) -> bool:
        if entry.source == F8ComponentSourceKind.local:
            return True
        return self._is_owned_remote_entry(entry)

    def _is_community_entry(self, entry: F8ComponentEntry) -> bool:
        return entry.source == F8ComponentSourceKind.remote_public and not self._is_owned_remote_entry(entry)

    def _on_login_clicked(self) -> None:
        if prompt_asset_cloud_sign_in(parent=self, sync_client=self._sync_client):
            self._on_account_state_changed()

    def _on_account_state_changed(self) -> None:
        current_user = self._sync_client.current_user()
        if current_user is None or not self._sync_client.current_access_token():
            sanitized_remote_entries: list[F8ComponentEntry] = []
            for entry in self._sync_client._catalog_service._remote_provider.load_entries():
                if entry.source == F8ComponentSourceKind.remote_private:
                    continue
                if entry.subscribed:
                    sanitized_remote_entries.append(
                        validate_as(F8ComponentEntry, {**dump_json(entry, mode="json"), "subscribed": False})
                    )
                else:
                    sanitized_remote_entries.append(entry)
            self._sync_client._catalog_service._remote_provider.save_entries(sanitized_remote_entries)
            self._remote_next_cursor_by_scope["mine"] = None
            self._remote_loaded_query_by_scope["mine"] = ""
            self._reload()
            return
        try:
            community_page = self._sync_client.refresh_scope_page(scope="community", query=self._tab_queries[self._TAB_COMMUNITY], cursor="", append=False)
            self._remote_next_cursor_by_scope["community"] = community_page.nextCursor
            self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
            mine_page = self._sync_client.refresh_scope_page(scope="mine", query=self._tab_queries[self._TAB_MINE], cursor="", append=False)
            self._remote_next_cursor_by_scope["mine"] = mine_page.nextCursor
            self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
        except Exception:
            logger.exception("Component manager account state refresh failed")
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
                logger.exception("Component catalog remembered account refresh failed")
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

    def _refresh_current_remote_scope(self, *, reset: bool) -> None:
        if self._is_loading_remote_scope:
            return
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._reload()
            return
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
            page = self._sync_client.refresh_scope_page(scope=remote_scope, query=self._current_query(), cursor=cursor, append=append)
        except Exception as exc:
            show_warning(self, "Refresh failed", str(exc))
            return
        finally:
            self._is_loading_remote_scope = False
        self._remote_next_cursor_by_scope[remote_scope] = page.nextCursor
        self._remote_loaded_query_by_scope[remote_scope] = self._current_query()
        self._reload()

    def _on_refresh_clicked(self) -> None:
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            try:
                self._sync_client.refresh_scope(scope="community", query=self._tab_queries[self._TAB_COMMUNITY])
                self._remote_next_cursor_by_scope["community"] = None
                self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
                if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
                    if self._ensure_logged_in():
                        self._sync_client.refresh_scope(scope="mine", query=self._tab_queries[self._TAB_MINE])
                        self._remote_next_cursor_by_scope["mine"] = None
                        self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
            except Exception as exc:
                show_warning(self, "Refresh failed", str(exc))
                return
            self._reload()
            return
        self._refresh_current_remote_scope(reset=True)

    def _show_local_history(self, entry: F8ComponentEntry) -> None:
        versions = self._sync_client._catalog_service.list_local_versions(str(entry.record.componentId))
        if not versions:
            show_info(self, "Component History", "No local history found.")
            return
        dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Component History - {entry.record.name}",
            items=[self._local_version_item(version) for version in versions],
            load_payload=lambda version_number: dump_json(
                self._require_local_version_payload(entry.record.componentId, version_number),
                mode="json",
            ),
        )
        dialog.exec()

    def _show_remote_history(self, entry: F8ComponentEntry) -> None:
        try:
            history = self._sync_client.list_component_versions(str(entry.record.componentId))
        except Exception as exc:
            show_warning(self, "History failed", str(exc))
            return
        if not history.versions:
            show_info(self, "Component History", "No history found.")
            return
        dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Component History - {entry.record.name}",
            items=[self._remote_version_item(version) for version in history.versions],
            load_payload=lambda version_number: dump_json(
                self._sync_client.get_component_version(str(entry.record.componentId), version_number),
                mode="json",
            ),
            actions=[
                AssetVersionBrowserAction(action_key="save_local", label="Save As Local Component"),
                AssetVersionBrowserAction(action_key="fork_remote", label="Fork To My Cloud"),
            ],
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        selected_version_number = dialog.selected_version_number()
        action_key = dialog.selected_action_key()
        if selected_version_number is None or not action_key:
            return
        if action_key == "save_local":
            self._save_remote_version_as_local_component(entry=entry, version_number=int(selected_version_number))
            return
        if action_key == "fork_remote":
            self._fork_remote_version_to_cloud(entry=entry, version_number=int(selected_version_number))

    @staticmethod
    def _local_version_item(version: F8ComponentLocalVersionSummary) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(version_number=int(version.versionNumber), created_at=str(version.createdAt))

    @staticmethod
    def _remote_version_item(version: F8ComponentRemoteVersionEntry) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(
            version_number=int(version.versionNumber),
            created_at=str(version.createdAt),
            revision=str(version.revision),
            change_summary="" if version.changeSummary is None else str(version.changeSummary),
        )

    def _require_local_version_payload(self, component_id: str, version_number: int) -> F8ComponentRecord:
        record = self._sync_client._catalog_service.local_version_record(str(component_id), int(version_number))
        if record is None:
            raise FileNotFoundError(f"Component version not found: {component_id} v{version_number}")
        return record

    def _save_remote_version_as_local_component(self, *, entry: F8ComponentEntry, version_number: int) -> None:
        try:
            historical_entry = self._sync_client.get_component_version(str(entry.record.componentId), int(version_number))
        except Exception as exc:
            show_warning(self, "Load version failed", str(exc))
            return
        dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Save Remote Version As Local Component",
            name=f"{historical_entry.record.name} v{int(version_number)}",
            description=historical_entry.record.description,
            tags=list(historical_entry.record.tags or []),
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = dialog.values()
        local_record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            tags=tags,
            content=historical_entry.record.content,
        )
        upsert_component(local_record)
        show_info(self, "Saved", f"Saved local component from v{int(version_number)}:\n{local_record.name}")
        self._reload()

    def _fork_remote_version_to_cloud(self, *, entry: F8ComponentEntry, version_number: int) -> None:
        if not self._ensure_logged_in():
            return
        try:
            historical_entry = self._sync_client.get_component_version(str(entry.record.componentId), int(version_number))
        except Exception as exc:
            show_warning(self, "Load version failed", str(exc))
            return
        dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Fork Remote Component Version",
            name=f"{historical_entry.record.name} Copy",
            description=historical_entry.record.description,
            tags=list(historical_entry.record.tags or []),
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        visibility = self._choose_visibility()
        if visibility is None:
            return
        name, description, tags = dialog.values()
        forked_record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            tags=tags,
            content=historical_entry.record.content,
        )
        forked_entry = F8ComponentEntry(
            record=forked_record,
            source=F8ComponentSourceKind.local,
            installed=True,
        )
        try:
            created = self._sync_client.fork_component(
                source_component_id=str(entry.record.componentId),
                forked_entry=forked_entry,
                visibility=visibility,
                version_number=int(version_number),
            )
        except Exception as exc:
            show_warning(self, "Fork failed", str(exc))
            return
        show_info(self, "Forked", f"Created remote fork from v{int(version_number)}:\n{created.record.name}")
        self._reload()

    def _choose_visibility(self) -> F8ComponentVisibility | None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Upload visibility",
            "Publish this component publicly?\n\nYes = public\nNo = private",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
        )
        if answer == QtWidgets.QMessageBox.Cancel:
            return None
        if answer == QtWidgets.QMessageBox.Yes:
            return F8ComponentVisibility.public
        return F8ComponentVisibility.private

    def _ensure_component_hydrated(
        self,
        entry: F8ComponentEntry,
        *,
        operation_name: str,
    ) -> F8ComponentEntry | None:
        if entry.source == F8ComponentSourceKind.local or component_entry_has_cached_content(entry):
            return entry
        try:
            return self._sync_client.hydrate_component(str(entry.record.componentId))
        except Exception as exc:
            show_warning(self, f"{operation_name} failed", str(exc))
            return None


def component_row_state_for_entries(
    *,
    component_id: str,
    local_entry: F8ComponentEntry | None,
    remote_entry: F8ComponentEntry | None,
) -> AssetCatalogRowState:
    cached_remote_content = False
    if remote_entry is not None:
        cached_remote_content = component_entry_has_cached_content(remote_entry)
    visibility = None
    owner_display_name = None
    subscribed = False
    remote_sync_state = None
    remote_version_number = None
    if remote_entry is not None:
        visibility = None if remote_entry.visibility is None else remote_entry.visibility.value
        owner_display_name = remote_entry.ownerDisplayName
        subscribed = bool(remote_entry.subscribed)
        remote_sync_state = remote_entry.syncState.value
        remote_version_number = remote_entry.remoteVersionNumber
    local_sync_state = None if local_entry is None else local_entry.syncState.value
    local_version_number = None if local_entry is None else local_entry.localVersionNumber
    if local_entry is not None and remote_entry is not None:
        sync_direction = determine_asset_sync_direction(
            has_local_entry=True,
            has_remote_entry=True,
            local_version_number=local_entry.localVersionNumber,
            remote_version_number=remote_entry.remoteVersionNumber,
            sync_base_remote_revision=local_entry.syncBaseRemoteRevision,
            sync_base_remote_version_number=local_entry.syncBaseRemoteVersionNumber,
            sync_base_local_version_number=local_entry.syncBaseLocalVersionNumber,
            current_remote_revision=remote_entry.remoteRevision,
        ).direction
        if sync_direction == AssetSyncDirection.conflict:
            local_sync_state = "conflict"
            remote_sync_state = "conflict"
        elif sync_direction == AssetSyncDirection.push:
            local_sync_state = "modified_local"
            remote_sync_state = "synced"
        elif sync_direction == AssetSyncDirection.pull:
            local_sync_state = "stale_remote"
            remote_sync_state = "synced"
        else:
            local_sync_state = "synced"
            remote_sync_state = "synced"
    if local_entry is not None and local_entry.isLocalDraft:
        owner_display_name = _LOCAL_DRAFT_LABEL
    return build_asset_catalog_row_state(
        asset_id=component_id,
        has_local_head=local_entry is not None,
        has_remote_head=remote_entry is not None,
        has_cached_remote_content=cached_remote_content,
        visibility=visibility,
        owner_display_name=owner_display_name,
        subscribed=subscribed,
        local_version_number=local_version_number,
        remote_version_number=remote_version_number,
        local_sync_state=local_sync_state,
        remote_sync_state=remote_sync_state,
    )
