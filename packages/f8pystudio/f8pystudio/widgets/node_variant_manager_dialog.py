from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json, validate_as
import json
from collections.abc import Callable
import logging
from typing import Any
from urllib.parse import urlparse

from qtpy import QtCore, QtWidgets

from f8pysdk import F8OperatorSpec, F8ServiceSpec

from ..ui_icons import StudioIcon, icon_for
from ..ui_notifications import show_info, show_warning
from ..variants.variant_compose import build_variant_record_from_node
from ..variants.variant_ids import build_variant_node_type
from f8pysdk import F8VariantRecord
from ..variants.variant_models import F8VariantEntry, F8VariantSourceKind, F8VariantVisibility
from ..variants.variant_repository import (
    delete_variant,
    export_to_json,
    import_from_json,
    is_variant_name_conflict,
    list_entries_for_base,
    normalize_variant_name,
    upsert_variant,
)
from ..variants.variant_events import subscribe_variants_changed
from ..variants.variant_sync import VariantSyncClient
from .json_text_editor import attach_json_enhancements
from .variant_cloud_account_menu import build_variant_cloud_account_menu, prompt_variant_cloud_login

logger = logging.getLogger(__name__)


class _VariantMetaDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: list[str],
        name_validator: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 220)
        self._name_validator = name_validator
        self._name = QtWidgets.QLineEdit(name, self)
        self._description = QtWidgets.QLineEdit(description, self)
        self._tags = QtWidgets.QLineEdit(", ".join(tags), self)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        form.addRow("Tags (comma-separated)", self._tags)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept_clicked(self) -> None:
        name = str(self._name.text() or "").strip()
        if not name:
            show_warning(self, "Invalid name", "Variant name cannot be empty.")
            return
        validator = self._name_validator
        if validator is not None:
            message = validator(name)
            if message:
                show_warning(self, "Invalid name", message)
                return
        self.accept()

    def values(self) -> tuple[str, str, list[str]]:
        tags = [s.strip() for s in str(self._tags.text() or "").split(",")]
        return (
            str(self._name.text() or "").strip(),
            str(self._description.text() or "").strip(),
            [t for t in tags if t],
        )


class NodeVariantManagerDialog(QtWidgets.QDialog):
    _TAB_MINE = 0
    _TAB_COMMUNITY = 1
    _TAB_INSTALLED = 2

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        base_node_type: str,
        base_node_name: str,
        node_graph: Any,
    ) -> None:
        super().__init__(parent)
        self._base_node_type = str(base_node_type or "").strip()
        self._base_node_name = str(base_node_name or "").strip() or self._base_node_type
        self._graph = node_graph
        self._entries: list[F8VariantEntry] = []
        self._sync_client = VariantSyncClient()
        self._initial_remote_refresh_done = False
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
        self._remote_next_cursor_by_scope: dict[str, str | None] = {
            "mine": None,
            "community": None,
        }
        self._remote_loaded_query_by_scope: dict[str, str] = {
            "mine": "",
            "community": "",
        }
        self._remote_loaded_base_by_scope: dict[str, str] = {
            "mine": "",
            "community": "",
        }
        self._variants_changed_unsubscribe: Callable[[], None] | None = subscribe_variants_changed(
            self._on_variants_changed
        )
        self.setWindowTitle(f"Variants - {self._base_node_name}")
        self.resize(1160, 720)

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
        self._search_input.setPlaceholderText("Search variants")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_input.returnPressed.connect(self._on_search_submitted)  # type: ignore[attr-defined]
        self._search_btn = QtWidgets.QPushButton("Search", self)
        self._search_btn.setIcon(icon_for(self._search_btn, StudioIcon.CLOUD_SEARCH))
        self._search_btn.clicked.connect(self._on_search_submitted)  # type: ignore[attr-defined]
        self._filter_combo = QtWidgets.QComboBox(self)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)  # type: ignore[attr-defined]
        self._load_more_btn = QtWidgets.QPushButton("Load More", self)
        self._load_more_btn.setIcon(icon_for(self._load_more_btn, StudioIcon.DOWNLOAD))
        self._load_more_btn.clicked.connect(self._on_load_more_clicked)  # type: ignore[attr-defined]

        self._toolbar = QtWidgets.QToolBar("Variant Cloud", self)
        self._toolbar.setMovable(False)
        self._toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._toolbar.setIconSize(QtCore.QSize(16, 16))
        self._toolbar.addWidget(self._scope_tabs)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._search_input)
        self._toolbar.addWidget(self._search_btn)
        self._toolbar.addWidget(self._filter_combo)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._account_button)

        btn_add = QtWidgets.QPushButton("Save From Selected Node", self)
        btn_edit = QtWidgets.QPushButton("Edit Metadata", self)
        btn_delete = QtWidgets.QPushButton("Delete", self)
        btn_copy_local = QtWidgets.QPushButton("Save As Local Copy", self)
        btn_upload = QtWidgets.QPushButton("Upload", self)
        btn_install = QtWidgets.QPushButton("Download/Install", self)
        btn_subscribe = QtWidgets.QPushButton("Subscribe", self)
        btn_refresh = QtWidgets.QPushButton("Refresh", self)
        btn_history = QtWidgets.QPushButton("History", self)
        btn_visibility = QtWidgets.QPushButton("Visibility", self)
        btn_import = QtWidgets.QPushButton("Import...", self)
        btn_export = QtWidgets.QPushButton("Export...", self)
        btn_create = QtWidgets.QPushButton("Create On Canvas", self)
        btn_close = QtWidgets.QPushButton("Close", self)

        btn_refresh.setIcon(icon_for(btn_refresh, StudioIcon.REFRESH))
        btn_upload.setIcon(icon_for(btn_upload, StudioIcon.CLOUD_UP))
        btn_install.setIcon(icon_for(btn_install, StudioIcon.CLOUD_DOWN))
        btn_subscribe.setIcon(icon_for(btn_subscribe, StudioIcon.HEART_ON))
        btn_history.setIcon(icon_for(btn_history, StudioIcon.ARTICLE))
        btn_visibility.setIcon(icon_for(btn_visibility, StudioIcon.EYE_STAR))
        btn_import.setIcon(icon_for(btn_import, StudioIcon.PACKAGE_IMPORT))
        btn_export.setIcon(icon_for(btn_export, StudioIcon.PACKAGE_EXPORT))
        btn_delete.setIcon(icon_for(btn_delete, StudioIcon.TRASH))
        btn_edit.setIcon(icon_for(btn_edit, StudioIcon.EDIT))
        btn_copy_local.setIcon(icon_for(btn_copy_local, StudioIcon.SAVE))
        btn_create.setIcon(icon_for(btn_create, StudioIcon.SQUARE_PLUS))

        btn_add.clicked.connect(self._on_add_clicked)  # type: ignore[attr-defined]
        btn_edit.clicked.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
        btn_delete.clicked.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
        btn_copy_local.clicked.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
        btn_upload.clicked.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
        btn_install.clicked.connect(self._on_install_clicked)  # type: ignore[attr-defined]
        btn_subscribe.clicked.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
        btn_refresh.clicked.connect(self._on_refresh_clicked)  # type: ignore[attr-defined]
        btn_history.clicked.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        btn_visibility.clicked.connect(self._on_visibility_clicked)  # type: ignore[attr-defined]
        btn_import.clicked.connect(self._on_import_clicked)  # type: ignore[attr-defined]
        btn_export.clicked.connect(self._on_export_clicked)  # type: ignore[attr-defined]
        btn_create.clicked.connect(self._on_create_clicked)  # type: ignore[attr-defined]
        btn_close.clicked.connect(self.accept)  # type: ignore[attr-defined]

        self._configure_icon_button(btn_upload, "Upload")
        self._configure_icon_button(btn_install, "Download/Install")
        self._configure_icon_button(btn_subscribe, "Subscribe / Unsubscribe")
        self._configure_icon_button(btn_refresh, "Refresh current list")
        self._configure_icon_button(btn_history, "History")
        self._configure_icon_button(btn_visibility, "Visibility")
        self._configure_icon_button(btn_import, "Import")
        self._configure_icon_button(btn_export, "Export")
        self._configure_icon_button(btn_delete, "Delete")
        self._configure_icon_button(btn_edit, "Edit Metadata")
        self._configure_icon_button(btn_copy_local, "Save As Local Copy")
        self._configure_icon_button(btn_create, "Create On Canvas")

        self._toolbar.addSeparator()
        self._toolbar.addWidget(btn_refresh)
        self._toolbar.addWidget(self._load_more_btn)
        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.addWidget(self._toolbar)
        toolbar_row.addStretch(1)

        btn_row = QtWidgets.QHBoxLayout()
        for button in [
            btn_add,
            btn_edit,
            btn_delete,
            btn_copy_local,
            btn_upload,
            btn_install,
            btn_subscribe,
            btn_history,
            btn_visibility,
            btn_create,
            btn_import,
            btn_export,
            btn_close,
        ]:
            btn_row.addWidget(button)

        self._list = QtWidgets.QListWidget(self)
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)  # type: ignore[attr-defined]
        self._raw = QtWidgets.QPlainTextEdit(self)
        self._raw.setReadOnly(True)
        self._raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        attach_json_enhancements(self._raw, read_only=True)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._raw)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(toolbar_row)
        layout.addLayout(btn_row)
        layout.addWidget(split, 1)

        self._btn_edit = btn_edit
        self._btn_delete = btn_delete
        self._btn_copy_local = btn_copy_local
        self._btn_upload = btn_upload
        self._btn_install = btn_install
        self._btn_subscribe = btn_subscribe
        self._btn_create = btn_create
        self._btn_refresh = btn_refresh
        self._btn_history = btn_history
        self._btn_visibility = btn_visibility

        self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        self._reload()

    def _clear_variants_changed_subscription(self) -> None:
        unsubscribe = self._variants_changed_unsubscribe
        self._variants_changed_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_destroyed(self, _obj: Any) -> None:
        self._clear_variants_changed_subscription()

    def _on_variants_changed(self) -> None:
        try:
            self._reload()
        except RuntimeError as exc:
            if "already deleted" in str(exc):
                self._clear_variants_changed_subscription()
                return
            raise

    def _matches_filter(self, entry: F8VariantEntry) -> bool:
        current_tab = self._scope_tabs.currentIndex()
        current_filter = self._current_filter_value()
        if current_tab == self._TAB_MINE:
            if not self._is_mine_entry(entry):
                return False
            if current_filter == "local":
                return entry.source == F8VariantSourceKind.local
            if current_filter == "private":
                return entry.source == F8VariantSourceKind.remote_private
            if current_filter == "shared":
                return self._is_owned_remote_shared_entry(entry)
            return True
        if current_tab == self._TAB_COMMUNITY:
            is_community_entry = (
                entry.source == F8VariantSourceKind.remote_public
                and not self._is_owned_remote_entry(entry)
            )
            if not is_community_entry:
                return False
            if current_filter == "subscribed":
                return bool(entry.subscribed)
            if current_filter == "not_subscribed":
                return not bool(entry.subscribed)
            return True
        if current_tab == self._TAB_INSTALLED:
            if not bool(entry.installed):
                return False
            if current_filter == "mine":
                return self._is_mine_entry(entry)
            if current_filter == "subscribed":
                return bool(entry.subscribed) and not self._is_owned_remote_entry(entry)
            return True
        return False

    def _reload(self, *_args: Any) -> None:
        self._refresh_remote_catalog_if_needed()
        self._entries = self._entries_for_current_tab()
        logger.info(
            "Variant manager reload tab=%s base_node_type=%s count=%d entries=%s",
            self._scope_tabs.tabText(self._scope_tabs.currentIndex()),
            self._base_node_type,
            len(self._entries),
            [
                {
                    "variantId": str(entry.record.variantId),
                    "source": entry.source.value,
                    "visibility": (None if entry.visibility is None else entry.visibility.value),
                    "subscribed": bool(entry.subscribed),
                    "installed": bool(entry.installed),
                    "syncState": entry.syncState.value,
                }
                for entry in self._entries[:10]
            ],
        )
        self._list.clear()
        for entry in self._entries:
            record = entry.record
            item = QtWidgets.QListWidgetItem()
            item.setToolTip(record.description or record.name)
            item.setData(QtCore.Qt.UserRole, record.variantId)
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

    def _build_list_row(self, entry: F8VariantEntry) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self._list)
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        if entry.subscribed:
            icon_label = QtWidgets.QLabel(container)
            icon_label.setPixmap(icon_for(container, StudioIcon.HEART_ON).pixmap(14, 14))
            icon_label.setToolTip("Subscribed")
            title_row.addWidget(icon_label)
        name_label = QtWidgets.QLabel(str(entry.record.name or ""), container)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet("color: palette(window-text);")
        title_row.addWidget(name_label, 1)
        if entry.ownerDisplayName:
            owner_label = QtWidgets.QLabel(f"by {entry.ownerDisplayName}", container)
            owner_label.setStyleSheet("color: palette(window-text);")
            title_row.addWidget(owner_label, 0)
        root.addLayout(title_row)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        for badge_text in self._badge_texts_for_entry(entry):
            badge = QtWidgets.QLabel(badge_text, container)
            badge.setStyleSheet(
                "QLabel {"
                " border: 1px solid palette(mid);"
                " border-radius: 4px;"
                " padding: 1px 6px;"
                " color: palette(window-text);"
                " background: palette(base);"
                "}"
            )
            meta_row.addWidget(badge, 0)
        meta_row.addStretch(1)
        root.addLayout(meta_row)

        if entry.record.description:
            description_label = QtWidgets.QLabel(str(entry.record.description), container)
            description_label.setWordWrap(True)
            description_label.setStyleSheet("color: palette(window-text);")
            root.addWidget(description_label)
        return container

    def _configure_icon_button(self, button: QtWidgets.QPushButton, tooltip: str) -> None:
        button.setToolTip(tooltip)
        button.setText("")
        button.setFixedWidth(30)

    def _badge_texts_for_entry(self, entry: F8VariantEntry) -> list[str]:
        badges = [self._source_text(entry)]
        if entry.visibility is not None:
            badges.append(entry.visibility.value)
        badges.append(entry.syncState.value)
        if entry.subscribed and self._sync_client.current_user() is not None:
            badges.append("subscribed")
        if entry.installed:
            badges.append("installed")
        else:
            badges.append("not installed")
        return badges

    def _entries_for_current_tab(self) -> list[F8VariantEntry]:
        current_tab = self._scope_tabs.currentIndex()
        service = self._sync_client._catalog_service
        normalized_query = self._current_query().lower()
        local_entries = [
            entry
            for entry in service._local_provider.load_entries()
            if str(entry.record.baseNodeType or "").strip() == self._base_node_type
        ]
        remote_entries = [
            entry
            for entry in service._remote_provider.load_entries()
            if str(entry.record.baseNodeType or "").strip() == self._base_node_type
        ]
        logger.info(
            "Variant manager source snapshot tab=%s base_node_type=%s local=%d remote=%d remote_entries=%s",
            self._scope_tabs.tabText(current_tab),
            self._base_node_type,
            len(local_entries),
            len(remote_entries),
            [
                {
                    "variantId": str(entry.record.variantId),
                    "source": entry.source.value,
                    "visibility": (None if entry.visibility is None else entry.visibility.value),
                    "ownerUserId": entry.ownerUserId,
                }
                for entry in remote_entries[:10]
            ],
        )
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
            merged: dict[str, F8VariantEntry] = {
                str(entry.record.variantId): entry
                for entry in local_entries
                if self._is_mine_entry(entry) and self._entry_matches_query(entry, normalized_query)
            }
            for entry in remote_entries:
                if self._is_owned_remote_entry(entry) and self._entry_matches_query(entry, normalized_query):
                    merged[str(entry.record.variantId)] = entry
            return sorted(merged.values(), key=self._entry_sort_key)
        return [
            entry
            for entry in list_entries_for_base(self._base_node_type, include_uninstalled=True)
            if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
        ]

    @staticmethod
    def _entry_sort_key(entry: F8VariantEntry) -> tuple[str, str]:
        return (str(entry.record.name or "").lower(), str(entry.record.variantId or ""))

    def _source_text(self, entry: F8VariantEntry) -> str:
        if entry.source == F8VariantSourceKind.local:
            return "local"
        if entry.source == F8VariantSourceKind.remote_official:
            return "official"
        if entry.source == F8VariantSourceKind.remote_private:
            return "mine"
        if self._is_owned_remote_shared_entry(entry):
            return "shared"
        if entry.source == F8VariantSourceKind.remote_public:
            return "community"
        return str(entry.source.value)

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
            return

    def _selected_entry(self) -> F8VariantEntry | None:
        item = self._list.currentItem()
        if item is None:
            return None
        variant_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        if not variant_id:
            return None
        for entry in self._entries:
            if str(entry.record.variantId) == variant_id:
                return entry
        return None

    def _selected_variant(self) -> F8VariantRecord | None:
        entry = self._selected_entry()
        return None if entry is None else entry.record

    def _on_selection_changed(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._raw.setPlainText("")
            self._btn_edit.setEnabled(False)
            self._btn_delete.setEnabled(False)
            self._btn_copy_local.setEnabled(False)
            self._btn_upload.setEnabled(False)
            self._btn_install.setEnabled(False)
            self._btn_subscribe.setEnabled(False)
            self._btn_subscribe.setToolTip("Subscribe")
            self._btn_create.setEnabled(False)
            self._btn_history.setEnabled(False)
            self._btn_visibility.setEnabled(False)
            return
        self._raw.setPlainText(json.dumps(dump_json(selected_entry, mode="json"), ensure_ascii=False, indent=2, default=str))
        is_local = selected_entry.source == F8VariantSourceKind.local
        is_remote = selected_entry.source in {
            F8VariantSourceKind.remote_official,
            F8VariantSourceKind.remote_public,
            F8VariantSourceKind.remote_private,
        }
        self._btn_edit.setEnabled(is_local)
        self._btn_delete.setEnabled(is_local)
        self._btn_copy_local.setEnabled(not is_local)
        self._btn_upload.setEnabled(is_local or is_remote)
        self._btn_install.setEnabled(is_remote and not selected_entry.installed)
        is_community_public = (
            selected_entry.source == F8VariantSourceKind.remote_public
            and not self._is_owned_remote_entry(selected_entry)
        )
        self._btn_subscribe.setEnabled(is_community_public)
        self._btn_subscribe.setToolTip("Unsubscribe" if selected_entry.subscribed else "Subscribe")
        self._btn_subscribe.setIcon(
            icon_for(
                self._btn_subscribe,
                StudioIcon.HEART_OFF if selected_entry.subscribed else StudioIcon.HEART_ON,
            )
        )
        self._btn_create.setEnabled(bool(selected_entry.installed))
        self._btn_history.setEnabled(self._is_owned_remote_entry(selected_entry))
        self._btn_visibility.setEnabled(self._is_owned_remote_entry(selected_entry))

    def _refresh_auth_controls(self) -> None:
        logged_in = self._sync_client.current_user() is not None and bool(self._sync_client.current_access_token())
        self._btn_refresh.setEnabled(True)
        remote_scope = self._remote_scope_for_current_tab()
        self._load_more_btn.setVisible(remote_scope is not None)
        self._load_more_btn.setEnabled(bool(remote_scope and self._remote_next_cursor_by_scope.get(remote_scope)))
        self._account_button.setIcon(
            icon_for(
                self._account_button,
                StudioIcon.USER if logged_in else StudioIcon.USER_OFF,
            )
        )

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self._on_create_clicked()

    def _find_selected_base_node(self) -> Any | None:
        graph = self._graph
        if graph is None:
            return None
        for n in list(graph.selected_nodes() or []):
            if str(n.type_ or "").strip() == self._base_node_type:
                return n
        return None

    def _on_add_clicked(self) -> None:
        node = self._find_selected_base_node()
        if node is None:
            show_info(
                self,
                "No matching selected node",
                f"Please select a node of type:\n{self._base_node_type}\nthen try again.",
            )
            return
        spec = node.spec
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            show_warning(self, "Unsupported node", "Selected node has no typed spec.")
            return
        node_display_name = ""
        try:
            node_display_name = str(node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            node_display_name = ""
        dlg = _VariantMetaDialog(
            parent=self,
            title="Save Variant",
            name=str(node_display_name or node.NODE_NAME or spec.label or self._base_node_name),
            description=str(spec.description or ""),
            tags=[str(t) for t in list(spec.tags or [])],
            name_validator=self._validate_new_variant_name,
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = dlg.values()
        record = build_variant_record_from_node(node=node, name=name, description=description, tags=tags)
        try:
            upsert_variant(record)
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return

    def _on_edit_clicked(self) -> None:
        selected = self._selected_variant()
        if selected is None:
            return
        dlg = _VariantMetaDialog(
            parent=self,
            title="Edit Variant Metadata",
            name=selected.name,
            description=selected.description,
            tags=list(selected.tags or []),
            name_validator=lambda candidate: self._validate_edit_variant_name(candidate, selected.variantId),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = dlg.values()
        payload = dump_json(selected, mode="json")
        payload["name"] = name
        payload["description"] = description
        payload["tags"] = tags
        payload["updatedAt"] = F8VariantRecord.now_iso()
        try:
            upsert_variant(validate_as(F8VariantRecord, payload))
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return

    def _validate_new_variant_name(self, candidate: str) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        if is_variant_name_conflict(self._base_node_type, normalized_name):
            return f"Variant name '{normalized_name}' already exists. Please rename."
        return None

    def _validate_edit_variant_name(self, candidate: str, variant_id: str) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        if is_variant_name_conflict(
            self._base_node_type,
            normalized_name,
            exclude_variant_id=variant_id,
        ):
            return f"Variant name '{normalized_name}' already exists. Please rename."
        return None

    def _on_delete_clicked(self) -> None:
        selected = self._selected_variant()
        if selected is None:
            return
        reply = QtWidgets.QMessageBox.question(self, "Delete variant", f"Delete variant '{selected.name}'?")
        if reply != QtWidgets.QMessageBox.Yes:
            return
        delete_variant(selected.variantId)

    def _on_copy_local_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        record = selected_entry.record
        copied = validate_as(F8VariantRecord, {**dump_json(record, mode="json"), "updatedAt": F8VariantRecord.now_iso()})
        try:
            upsert_variant(copied)
        except ValueError as exc:
            show_warning(self, "Save local copy failed", str(exc))
            return
        show_info(self, "Saved", f"Saved local copy:\n{record.name}")

    def _on_upload_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        try:
            if not self._ensure_logged_in():
                return
            entry_to_upload = selected_entry
            if selected_entry.source == F8VariantSourceKind.local:
                visibility = self._choose_visibility()
                if visibility is None:
                    return
                source = F8VariantSourceKind.remote_private if visibility == F8VariantVisibility.private else F8VariantSourceKind.remote_public
                entry_to_upload = validate_as(
                    F8VariantEntry,
                    {
                        **dump_json(selected_entry, mode="json"),
                        "source": source.value,
                        "visibility": visibility.value,
                        "installed": True,
                    },
                )
            uploaded = self._sync_client.upload_entry(entry_to_upload)
        except Exception as exc:
            show_warning(self, "Upload failed", str(exc))
            return
        show_info(self, "Uploaded", f"Uploaded variant:\n{uploaded.record.name}")
        self._reload()

    def _on_install_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        try:
            if not self._ensure_logged_in():
                return
            installed = self._sync_client.install_variant(str(selected_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Install failed", str(exc))
            return
        show_info(self, "Installed", f"Installed variant:\n{installed.record.name}")
        self._reload()

    def _on_subscribe_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if not self._ensure_logged_in():
            return
        try:
            if selected_entry.subscribed:
                updated = self._sync_client.unsubscribe_variant(str(selected_entry.record.variantId))
                show_info(self, "Unsubscribed", f"Removed subscription:\n{updated.record.name}")
            else:
                updated = self._sync_client.subscribe_variant(str(selected_entry.record.variantId))
                show_info(self, "Subscribed", f"Subscribed to variant:\n{updated.record.name}")
        except Exception as exc:
            show_warning(self, "Subscription failed", str(exc))
            return
        self._reload()

    def _on_create_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if not selected_entry.installed:
            show_warning(self, "Variant unavailable", "Install/download this variant before creating it on the canvas.")
            return
        graph = self._graph
        if graph is None:
            return
        selected = selected_entry.record
        variant_node_type = build_variant_node_type(str(selected.variantId))
        placement_label = f"{self._base_node_name}\n - {selected.name}"
        graph.begin_node_placement(variant_node_type, placement_label)

    def _on_accounts_clicked(self) -> None:
        menu = build_variant_cloud_account_menu(
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

    def _entry_matches_query(self, entry: F8VariantEntry, normalized_query: str) -> bool:
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

    def _is_owned_remote_entry(self, entry: F8VariantEntry) -> bool:
        current_user = self._sync_client.current_user()
        if current_user is None:
            return False
        if entry.source not in {F8VariantSourceKind.remote_public, F8VariantSourceKind.remote_private}:
            return False
        return str(entry.ownerUserId or "") == str(current_user.userId)

    def _is_owned_remote_shared_entry(self, entry: F8VariantEntry) -> bool:
        return self._is_owned_remote_entry(entry) and entry.visibility == F8VariantVisibility.public

    def _is_mine_entry(self, entry: F8VariantEntry) -> bool:
        if entry.source == F8VariantSourceKind.local:
            return True
        return self._is_owned_remote_entry(entry)

    def _on_history_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or not self._is_owned_remote_entry(selected_entry):
            return
        try:
            history = self._sync_client.list_variant_versions(str(selected_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "History failed", str(exc))
            return
        lines = [
            f"v{version.versionNumber}  {version.createdAt}  {version.revision}"
            + (f"  - {version.changeSummary}" if version.changeSummary else "")
            for version in history.versions
        ]
        message = "\n".join(lines) if lines else "No history found."
        show_info(self, "Variant History", message)

    def _on_visibility_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or not self._is_owned_remote_entry(selected_entry):
            return
        current_visibility = selected_entry.visibility
        next_visibility = F8VariantVisibility.public
        prompt = "Make this remote variant public?"
        if current_visibility == F8VariantVisibility.public:
            next_visibility = F8VariantVisibility.private
            prompt = "Make this remote variant private?"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Change visibility",
            prompt,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        payload = validate_as(
            F8VariantEntry,
            {
                **dump_json(selected_entry, mode="json"),
                "visibility": next_visibility.value,
                "source": (
                    F8VariantSourceKind.remote_public.value
                    if next_visibility == F8VariantVisibility.public
                    else F8VariantSourceKind.remote_private.value
                ),
            },
        )
        try:
            self._sync_client.update_variant(payload)
        except Exception as exc:
            show_warning(self, "Visibility update failed", str(exc))
            return
        self._reload()

    def _on_login_clicked(self) -> None:
        if prompt_variant_cloud_login(parent=self, sync_client=self._sync_client):
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

    def _preferred_login_base_url(self) -> str:
        configured_base_url = self._sync_client.base_url()
        if _is_loopback_url(configured_base_url):
            return VariantSyncClient.default_base_url()
        return configured_base_url

    def _ensure_logged_in(self) -> bool:
        if self._sync_client.current_user() is not None and self._sync_client.current_access_token():
            return True
        if self._sync_client.current_session() is not None:
            try:
                self._sync_client.refresh_auth()
                self._reload()
                return True
            except Exception:
                logger.exception("Variant manager remembered-session refresh failed")
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

    def _on_load_more_clicked(self) -> None:
        self._refresh_current_remote_scope(reset=False)

    def _refresh_current_remote_scope(self, *, reset: bool) -> None:
        remote_scope = self._remote_scope_for_current_tab()
        if remote_scope is None:
            self._reload()
            return
        current_query = self._current_query()
        current_base = self._base_node_type
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
        self._remote_next_cursor_by_scope[remote_scope] = page.nextCursor
        self._remote_loaded_query_by_scope[remote_scope] = current_query
        self._remote_loaded_base_by_scope[remote_scope] = current_base
        logger.info(
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
        if remote_scope is None:
            try:
                self._sync_client.refresh_scope(
                    scope="community",
                    base_node_type=self._base_node_type,
                    query=self._tab_queries[self._TAB_COMMUNITY],
                )
                self._remote_next_cursor_by_scope["community"] = None
                self._remote_loaded_query_by_scope["community"] = self._tab_queries[self._TAB_COMMUNITY]
                self._remote_loaded_base_by_scope["community"] = self._base_node_type
                if self._sync_client.current_access_token() or self._sync_client.current_session() is not None:
                    if self._ensure_logged_in():
                        self._sync_client.refresh_scope(
                            scope="mine",
                            base_node_type=self._base_node_type,
                            query=self._tab_queries[self._TAB_MINE],
                        )
                        self._remote_next_cursor_by_scope["mine"] = None
                        self._remote_loaded_query_by_scope["mine"] = self._tab_queries[self._TAB_MINE]
                        self._remote_loaded_base_by_scope["mine"] = self._base_node_type
            except Exception as exc:
                show_warning(self, "Refresh failed", str(exc))
                return
            self._reload()
            return
        self._refresh_current_remote_scope(reset=True)

    def _choose_visibility(self) -> F8VariantVisibility | None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Upload visibility",
            "Publish this variant publicly?\n\nYes = public\nNo = private",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
        )
        if answer == QtWidgets.QMessageBox.Cancel:
            return None
        if answer == QtWidgets.QMessageBox.Yes:
            return F8VariantVisibility.public
        return F8VariantVisibility.private

    def _auth_status_text(self) -> str:
        user = self._sync_client.current_user()
        if user is None:
            return "Signed out"
        return str(user.displayName or user.username or "Signed in")

    def _on_import_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Variant Library JSON",
            "",
            "JSON (*.json);;All Files (*)",
        )
        p = str(path or "").strip()
        if not p:
            return
        mode = QtWidgets.QMessageBox.question(
            self,
            "Import mode",
            "Merge into existing local library?\n\nYes = Merge\nNo = Replace",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
        )
        if mode == QtWidgets.QMessageBox.Cancel:
            return
        try:
            import_from_json(p, mode="merge" if mode == QtWidgets.QMessageBox.Yes else "replace")
        except Exception as exc:
            show_warning(self, "Import failed", str(exc))
            return

    def _on_export_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Variant Library JSON",
            "nodeVariants.json",
            "JSON (*.json);;All Files (*)",
        )
        p = str(path or "").strip()
        if not p:
            return
        try:
            out = export_to_json(p)
        except Exception as exc:
            show_warning(self, "Export failed", str(exc))
            return
        show_info(self, "Exported", f"Saved:\n{out}")


def _is_loopback_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    hostname = str(parsed.hostname or "").strip().lower()
    return hostname in {"127.0.0.1", "localhost", "0.0.0.0"}
