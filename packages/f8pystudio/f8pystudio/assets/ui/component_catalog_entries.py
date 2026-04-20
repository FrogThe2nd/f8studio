from __future__ import annotations

from f8pysdk.codec import copy_model

from ..components.component_catalog import component_entry_has_cached_content, component_entry_is_installed
from ..components.component_models import F8ComponentEntry, F8ComponentSourceKind, F8ComponentVisibility
from ..components.component_repository import list_component_entries
from .catalog_status import AssetCatalogRowState, build_asset_catalog_row_state


class ComponentCatalogEntriesMixin:
    LINKED_DRAFT_LABEL: str

    def _entries_for_current_tab(self) -> list[F8ComponentEntry]:
        current_tab = self._scope_tabs.currentIndex()
        service = self._sync_client._catalog_service
        normalized_query = self._current_query().lower()
        draft_entries = self._draft_service_for_catalog().list_catalog_entries()
        remote_entries = service._remote_provider.load_entries()
        if current_tab == self._TAB_DRAFTS:
            return sorted(
                [
                    entry
                    for entry in draft_entries
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
        return [
            entry
            for entry in sorted(remote_entries, key=self._entry_sort_key)
            if self._matches_filter(entry) and self._entry_matches_query(entry, normalized_query)
        ]

    def _matches_filter(self, entry: F8ComponentEntry) -> bool:
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
        if not component_entry_has_cached_content(entry):
            return False
        if current_filter == "mine":
            return self._is_owned_remote_entry(entry)
        if current_filter == "subscribed":
            return row_state.subscribed and not self._is_owned_remote_entry(entry)
        return True

    def _build_row_states(self) -> dict[str, AssetCatalogRowState]:
        service = self._sync_client._catalog_service
        local_entries = self._draft_service_for_catalog().list_catalog_entries()
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
            row_states[component_id] = self._component_row_state_for_entries(
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
        return self._component_row_state_for_entries(
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
        return self._is_owned_remote_entry(entry)

    def _is_community_entry(self, entry: F8ComponentEntry) -> bool:
        return entry.source == F8ComponentSourceKind.remote_public and not self._is_owned_remote_entry(entry)

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

    @staticmethod
    def _entry_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
        return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))

    @classmethod
    def _component_row_state_for_entries(
        cls,
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
        if remote_entry is not None:
            visibility = None if remote_entry.visibility is None else remote_entry.visibility.value
            owner_display_name = remote_entry.ownerDisplayName
            subscribed = bool(remote_entry.subscribed)
        if local_entry is not None and local_entry.isLocalDraft and remote_entry is None:
            owner_display_name = cls.LINKED_DRAFT_LABEL if local_entry.draftOriginAssetId else cls.LOCAL_DRAFT_LABEL
        return build_asset_catalog_row_state(
            asset_id=component_id,
            has_local_head=local_entry is not None,
            has_remote_head=remote_entry is not None,
            has_cached_remote_content=cached_remote_content,
            visibility=visibility,
            owner_display_name=owner_display_name,
            subscribed=subscribed,
        )
