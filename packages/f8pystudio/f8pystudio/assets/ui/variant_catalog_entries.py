from __future__ import annotations

import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from ...ui.support.ui_icons import StudioIcon, icon_for
from ..variants.variant_catalog import variant_entry_is_installed
from ..variants.variant_models import (
    F8VariantEntry,
    F8VariantSourceKind,
    F8VariantVisibility,
)
from ..variants.variant_repository import list_entries_for_base, normalize_variant_name
from .catalog_status import AssetCatalogRowState, build_asset_catalog_row_state

logger = logging.getLogger(__name__)


def variant_row_state_for_entries(
    *,
    variant_id: str,
    local_entry: F8VariantEntry | None,
    remote_entry: F8VariantEntry | None,
    local_draft_label: str = "Local Draft",
    linked_draft_label: str = "Linked Draft",
) -> AssetCatalogRowState:
    cached_remote_content = False
    if remote_entry is not None:
        cached_remote_content = variant_entry_is_installed(remote_entry)
    visibility = None
    owner_display_name = None
    subscribed = False
    if remote_entry is not None:
        visibility = None if remote_entry.visibility is None else remote_entry.visibility.value
        owner_display_name = remote_entry.ownerDisplayName
        subscribed = bool(remote_entry.subscribed)
    if local_entry is not None and local_entry.isLocalDraft and remote_entry is None:
        owner_display_name = linked_draft_label if local_entry.draftOriginAssetId else local_draft_label
    return build_asset_catalog_row_state(
        asset_id=variant_id,
        has_local_head=local_entry is not None,
        has_remote_head=remote_entry is not None,
        has_cached_remote_content=cached_remote_content,
        visibility=visibility,
        owner_display_name=owner_display_name,
        subscribed=subscribed,
    )


class VariantCatalogEntriesMixin:
    _TAB_DRAFTS: int
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    _base_node_type: str
    _is_global_mode: bool
    LOCAL_DRAFT_LABEL: str
    LINKED_DRAFT_LABEL: str
    _sync_client: Any
    _scope_tabs: Any
    _row_states_by_variant_id: dict[str, AssetCatalogRowState]
    _list: QtWidgets.QListWidget
    _current_query: Any
    _get_current_base_node_type: Any
    _current_filter_value: Any
    _entry_matches_query: Any
    _owner_label_text: Any
    _linked_draft_reference_text: Any
    _linked_draft_reference_tooltip: Any
    _linked_draft_badge_text: Any
    _linked_draft_badge_tooltip: Any
    _is_owned_remote_entry: Any
    _is_owned_remote_shared_entry: Any
    _is_mine_entry: Any

    @staticmethod
    def _matches_base_type(entry: F8VariantEntry, *, current_base_type: str) -> bool:
        if not current_base_type:
            return True
        return str(entry.record.baseNodeType or "").strip() == current_base_type

    def _matches_filter(self, entry: F8VariantEntry) -> bool:
        row_state = self._row_state_for_entry(entry)
        current_tab = self._scope_tabs.currentIndex()
        current_filter = self._current_filter_value()
        if current_tab == self._TAB_DRAFTS:
            if not self._is_local_draft_entry(entry):
                return False
            if current_filter == "linked":
                return bool(entry.draftOriginAssetId)
            if current_filter == "unpublished":
                return not bool(entry.draftOriginAssetId)
            return True
        if current_tab == self._TAB_MINE:
            if not self._is_mine_entry(entry):
                return False
            if current_filter == "private":
                return row_state.has_remote_head and row_state.visibility == F8VariantVisibility.private.value
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
            if not variant_entry_is_installed(entry):
                return False
            if current_filter == "mine":
                return self._is_owned_remote_entry(entry)
            if current_filter == "subscribed":
                return row_state.subscribed and not self._is_owned_remote_entry(entry)
            return True
        return False

    def _entries_for_current_tab(self) -> list[F8VariantEntry]:
        current_tab = self._scope_tabs.currentIndex()
        service = self._sync_client._catalog_service
        normalized_query = self._current_query().lower()
        current_base_type = self._get_current_base_node_type()

        local_entries = [
            entry
            for entry in self._draft_service_for_catalog().list_catalog_entries()
            if self._matches_base_type(entry, current_base_type=current_base_type)
        ]
        remote_entries = [
            entry
            for entry in service._remote_provider.load_entries()
            if self._matches_base_type(entry, current_base_type=current_base_type)
        ]
        logger.debug(
            "Variant manager source snapshot tab=%s base_node_type=%s local=%d remote=%d remote_entries=%s",
            self._scope_tabs.tabText(current_tab),
            current_base_type,
            len(local_entries),
            len(remote_entries),
            [
                {
                    "variantId": str(entry.record.variantId),
                    "source": entry.source.value,
                    "visibility": None if entry.visibility is None else entry.visibility.value,
                    "ownerUserId": entry.ownerUserId,
                }
                for entry in remote_entries[:10]
            ],
        )
        if current_tab == self._TAB_DRAFTS:
            return sorted(
                [
                    entry
                    for entry in local_entries
                    if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
                ],
                key=self._entry_sort_key,
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
            return sorted(
                [
                    entry
                    for entry in remote_entries
                    if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
                ],
                key=self._entry_sort_key,
            )
        entries = remote_entries
        return [
            entry
            for entry in entries
            if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
        ]

    def _build_row_states(self) -> dict[str, AssetCatalogRowState]:
        service = self._sync_client._catalog_service
        current_base_type = self._get_current_base_node_type()
        local_entries = [
            entry
            for entry in self._draft_service_for_catalog().list_catalog_entries()
            if self._matches_base_type(entry, current_base_type=current_base_type)
        ]
        remote_entries = [
            entry
            for entry in service._remote_provider.load_entries()
            if self._matches_base_type(entry, current_base_type=current_base_type)
        ]
        local_by_id = {
            str(entry.record.variantId): entry
            for entry in local_entries
            if str(entry.record.variantId).strip()
        }
        remote_by_id = {
            str(entry.record.variantId): entry
            for entry in remote_entries
            if str(entry.record.variantId).strip()
        }
        row_states: dict[str, AssetCatalogRowState] = {}
        for variant_id in sorted(set(local_by_id) | set(remote_by_id)):
            row_states[variant_id] = variant_row_state_for_entries(
                variant_id=variant_id,
                local_entry=local_by_id.get(variant_id),
                remote_entry=remote_by_id.get(variant_id),
                local_draft_label=self.LOCAL_DRAFT_LABEL,
                linked_draft_label=self.LINKED_DRAFT_LABEL,
            )
        return row_states

    def _row_state_for_entry(self, entry: F8VariantEntry) -> AssetCatalogRowState:
        variant_id = str(entry.record.variantId or "").strip()
        if variant_id:
            row_state = self._row_states_by_variant_id.get(variant_id)
            if row_state is not None:
                return row_state
        return variant_row_state_for_entries(
            variant_id=variant_id,
            local_entry=entry if entry.source == F8VariantSourceKind.local else None,
            remote_entry=entry if entry.source != F8VariantSourceKind.local else None,
            local_draft_label=self.LOCAL_DRAFT_LABEL,
            linked_draft_label=self.LINKED_DRAFT_LABEL,
        )

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

    def _mine_entries_for_base(self, *, exclude_variant_id: str | None = None) -> list[F8VariantEntry]:
        excluded_variant_id = str(exclude_variant_id or "").strip()
        entries: list[F8VariantEntry] = []
        for entry in self._sync_client._catalog_service.load_all_entries():
            if str(entry.record.baseNodeType or "").strip() != self._base_node_type:
                continue
            variant_id = str(entry.record.variantId or "").strip()
            if excluded_variant_id and variant_id == excluded_variant_id:
                continue
            if not self._is_mine_entry(entry):
                continue
            entries.append(entry)
        return entries

    def _mine_entry_by_name(self, name: str, *, exclude_variant_id: str | None = None) -> F8VariantEntry | None:
        normalized_name = normalize_variant_name(name)
        if not normalized_name:
            return None
        for entry in self._mine_entries_for_base(exclude_variant_id=exclude_variant_id):
            if normalize_variant_name(entry.record.name) == normalized_name:
                return entry
        return None

    def _is_owned_remote_entry(self, entry: F8VariantEntry) -> bool:
        current_user = self._sync_client.current_user()
        if current_user is None:
            return False
        if entry.source not in {F8VariantSourceKind.remote_public, F8VariantSourceKind.remote_private}:
            return False
        return str(entry.ownerUserId or "") == str(current_user.userId)

    def _is_owned_remote_shared_entry(self, entry: F8VariantEntry) -> bool:
        return self._is_owned_remote_entry(entry) and bool(entry.visibility) and entry.visibility.value == "public"

    def _is_mine_entry(self, entry: F8VariantEntry) -> bool:
        return self._is_owned_remote_entry(entry)

    def _build_list_row(self, entry: F8VariantEntry) -> QtWidgets.QWidget:
        row_state = self._row_state_for_entry(entry)
        linked_reference_text = self._linked_draft_reference_text(entry)
        linked_reference_tooltip = self._linked_draft_reference_tooltip(entry)
        linked_draft_badge_text = self._linked_draft_badge_text(entry)
        linked_draft_badge_tooltip = self._linked_draft_badge_tooltip(entry)
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
        if row_state.subscribed:
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
        owner_label_text = self._owner_label_text(row_state.owner_display_name)
        if owner_label_text is not None:
            owner_label = QtWidgets.QLabel(owner_label_text, container)
            owner_label.setStyleSheet("color: palette(window-text);")
            title_row.addWidget(owner_label, 0)
        root.addLayout(title_row)

        if linked_reference_text is not None:
            linked_label = QtWidgets.QLabel(linked_reference_text, container)
            linked_label.setStyleSheet(
                "QLabel {"
                " color: #dbeafe;"
                " font-size: 12px;"
                " font-weight: 600;"
                " background: #172033;"
                " border: 1px solid #355070;"
                " border-radius: 8px;"
                " padding: 2px 8px;"
                "}"
            )
            if linked_reference_tooltip is not None:
                linked_label.setToolTip(linked_reference_tooltip)
            root.addWidget(linked_label)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        if linked_draft_badge_text is not None:
            linked_draft_badge = self._build_text_badge(container, linked_draft_badge_text)
            linked_draft_badge.setStyleSheet(
                "QLabel {"
                " border: 1px solid #1f7a5a;"
                " border-radius: 9px;"
                " padding: 1px 6px;"
                " color: #dcfce7;"
                " background: #14532d;"
                " font-weight: 600;"
                "}"
            )
            if linked_draft_badge_tooltip is not None:
                linked_draft_badge.setToolTip(linked_draft_badge_tooltip)
            meta_row.addWidget(linked_draft_badge, 0)
        if self._is_global_mode and not self._get_current_base_node_type():
            base_type = str(entry.record.baseNodeType or "").strip()
            if base_type:
                type_badge = self._build_text_badge(container, base_type)
                type_badge.setStyleSheet(
                    "QLabel {"
                    " border: 1px solid palette(highlight);"
                    " border-radius: 9px;"
                    " padding: 1px 6px;"
                    " color: palette(highlighted-text);"
                    " background: palette(highlight);"
                    "}"
                )
                meta_row.addWidget(type_badge, 0)

        visibility_badge = self._build_visibility_badge(container, row_state)
        if visibility_badge is not None:
            meta_row.addWidget(visibility_badge, 0)
        meta_row.addStretch(1)
        root.addLayout(meta_row)

        if entry.record.description:
            description_label = QtWidgets.QLabel(str(entry.record.description), container)
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
