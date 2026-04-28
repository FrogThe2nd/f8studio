from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from qtpy import QtWidgets

from ..components.component_drafts import ComponentDraftService
from ..components.component_models import (
    F8ComponentEntry,
    F8ComponentLocalVersionSummary,
    F8ComponentRecord,
    F8ComponentVisibility,
)
from ..components.component_sync import ComponentSyncClient
from ..variants.variant_drafts import VariantDraftService
from ..variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantLocalVersionSummary,
    F8VariantRecord,
    F8VariantVisibility,
)
from ..variants.variant_sync import VariantSyncClient
from .catalog_status import AssetCatalogRowState


class _ComponentCatalogDialogHost(Protocol):
    _TAB_DRAFTS: int
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    LINKED_DRAFT_LABEL: str
    LOCAL_DRAFT_LABEL: str
    LOCAL_DRAFT_LOAD_TOOLTIP: str
    _graph: Any
    _entries: list[F8ComponentEntry]
    _row_states_by_component_id: dict[str, AssetCatalogRowState]
    _sync_client: ComponentSyncClient
    _list: QtWidgets.QListWidget
    _scope_tabs: QtWidgets.QTabBar
    _search_input: QtWidgets.QLineEdit
    _filter_combo: QtWidgets.QComboBox
    _account_button: QtWidgets.QToolButton
    _btn_refresh: QtWidgets.QAbstractButton
    _btn_install: QtWidgets.QPushButton
    _btn_upload: QtWidgets.QPushButton
    _btn_subscribe: QtWidgets.QPushButton
    _btn_copy_local: QtWidgets.QPushButton
    _btn_delete: QtWidgets.QPushButton
    _btn_edit: QtWidgets.QPushButton
    _btn_visibility: QtWidgets.QPushButton
    _btn_history: QtWidgets.QPushButton
    _raw: QtWidgets.QPlainTextEdit
    _preview: Any
    destroyed: Any

    def setWindowTitle(self, title: str) -> None: ...

    def resize(self, width: int, height: int) -> None: ...

    def _draft_service_for_catalog(self) -> ComponentDraftService: ...

    def _selected_entry(self) -> F8ComponentEntry | None: ...

    def _selected_local_entry(self) -> F8ComponentEntry | None: ...

    def _selected_remote_entry(self) -> F8ComponentEntry | None: ...

    def _selected_action_entries(
        self,
    ) -> tuple[F8ComponentEntry | None, F8ComponentEntry | None, F8ComponentEntry | None]: ...

    def _selected_component_id(self) -> str: ...

    def _local_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None: ...

    def _remote_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None: ...

    def _row_state_for_entry(self, entry: F8ComponentEntry) -> AssetCatalogRowState: ...

    def _linked_draft_reference_text(self, entry: F8ComponentEntry) -> str | None: ...

    def _linked_draft_reference_tooltip(self, entry: F8ComponentEntry) -> str | None: ...

    def _linked_draft_badge_text(self, entry: F8ComponentEntry) -> str | None: ...

    def _linked_draft_badge_tooltip(self, entry: F8ComponentEntry) -> str | None: ...

    def _local_entries_snapshot(self) -> list[F8ComponentEntry]: ...

    def _remote_entries_snapshot(self) -> list[F8ComponentEntry]: ...

    def _matches_filter(self, entry: F8ComponentEntry) -> bool: ...

    def _entry_matches_query(self, entry: F8ComponentEntry, normalized_query: str) -> bool: ...

    def _current_query(self) -> str: ...

    def _current_filter_value(self) -> str: ...

    def _load_action_availability(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> tuple[bool, bool]: ...

    def _is_owned_remote_entry(self, entry: F8ComponentEntry) -> bool: ...

    def _is_owned_remote_shared_entry(self, entry: F8ComponentEntry) -> bool: ...

    def _is_mine_entry(self, entry: F8ComponentEntry) -> bool: ...

    def _is_community_entry(self, entry: F8ComponentEntry) -> bool: ...

    def _is_local_draft_entry(self, entry: F8ComponentEntry | None) -> bool: ...

    def _owner_label_text(self, owner_display_name: str | None) -> str | None: ...

    def _offload_selected_component(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> bool: ...

    def _pull_selected_component(self, *, force_replace_local: bool = False) -> F8ComponentEntry | None: ...

    def _ensure_logged_in(self) -> bool: ...

    def _choose_visibility(self) -> F8ComponentVisibility | None: ...

    def _show_local_history(self, entry: F8ComponentEntry) -> None: ...

    def _show_remote_history(self, entry: F8ComponentEntry) -> None: ...

    def _ensure_component_hydrated(
        self,
        entry: F8ComponentEntry,
        *,
        operation_name: str,
    ) -> F8ComponentEntry | None: ...

    def _rebuild_browser_after_draft_changed(self, *, preserve_component_id: str | None = None) -> None: ...

    def _rebuild_browser_after_installed_state_changed(
        self,
        *,
        preserve_component_id: str | None = None,
    ) -> None: ...

    def _rebuild_browser_after_remote_scope_state_changed(
        self,
        *,
        preserve_component_id: str | None = None,
    ) -> None: ...

    def _rebuild_browser_after_remote_asset_changed(
        self,
        *,
        preserve_component_id: str | None = None,
    ) -> None: ...


class ComponentCatalogActionsHost(_ComponentCatalogDialogHost, Protocol):
    pass


class ComponentCatalogSyncHost(_ComponentCatalogDialogHost, Protocol):
    pass


class ComponentCatalogVersionHost(_ComponentCatalogDialogHost, Protocol):
    pass


class _VariantCatalogDialogHost(Protocol):
    _TAB_DRAFTS: int
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    LOCAL_DRAFT_LABEL: str
    LINKED_DRAFT_LABEL: str
    LOCAL_DRAFT_LOAD_TOOLTIP: str
    _base_node_type: str
    _base_node_name: str
    _is_global_mode: bool
    _graph: Any
    _entries: list[F8VariantEntry]
    _row_states_by_variant_id: dict[str, AssetCatalogRowState]
    _sync_client: VariantSyncClient
    _list: QtWidgets.QListWidget
    _scope_tabs: QtWidgets.QTabBar
    _search_input: QtWidgets.QLineEdit
    _filter_combo: QtWidgets.QComboBox
    _node_type_combo: QtWidgets.QComboBox
    _account_button: QtWidgets.QToolButton
    _btn_refresh: QtWidgets.QAbstractButton
    _btn_install: QtWidgets.QPushButton
    _btn_upload: QtWidgets.QPushButton
    _btn_subscribe: QtWidgets.QPushButton
    _btn_copy_local: QtWidgets.QPushButton
    _btn_delete: QtWidgets.QPushButton
    _btn_edit: QtWidgets.QPushButton
    _btn_visibility: QtWidgets.QPushButton
    _btn_history: QtWidgets.QPushButton
    _btn_create: QtWidgets.QPushButton
    _raw: QtWidgets.QPlainTextEdit
    _preview: Any
    destroyed: Any

    def _draft_service_for_catalog(self) -> VariantDraftService: ...

    def _selected_entry(self) -> F8VariantEntry | None: ...

    def _selected_local_entry(self) -> F8VariantEntry | None: ...

    def _selected_remote_entry(self) -> F8VariantEntry | None: ...

    def _selected_action_entries(
        self,
    ) -> tuple[F8VariantEntry | None, F8VariantEntry | None, F8VariantEntry | None]: ...

    def _selected_variant_id(self) -> str: ...

    def _local_entry_for_variant_id(self, variant_id: str) -> F8VariantEntry | None: ...

    def _remote_entry_for_variant_id(self, variant_id: str) -> F8VariantEntry | None: ...

    def _row_state_for_entry(self, entry: F8VariantEntry) -> AssetCatalogRowState: ...

    def _linked_draft_reference_text(self, entry: F8VariantEntry) -> str | None: ...

    def _linked_draft_badge_text(self, entry: F8VariantEntry) -> str | None: ...

    def _linked_draft_reference_tooltip(self, entry: F8VariantEntry) -> str | None: ...

    def _linked_draft_badge_tooltip(self, entry: F8VariantEntry) -> str | None: ...

    def _owner_label_text(self, owner_display_name: str | None) -> str | None: ...

    def _current_query(self) -> str: ...

    def _current_filter_value(self) -> str: ...

    def _get_current_base_node_type(self) -> str: ...

    def _entry_matches_query(self, entry: F8VariantEntry, normalized_query: str) -> bool: ...

    def _local_entries_snapshot(self) -> list[F8VariantEntry]: ...

    def _remote_entries_snapshot(self) -> list[F8VariantEntry]: ...

    def _is_owned_remote_entry(self, entry: F8VariantEntry) -> bool: ...

    def _is_owned_remote_shared_entry(self, entry: F8VariantEntry) -> bool: ...

    def _is_mine_entry(self, entry: F8VariantEntry) -> bool: ...

    def _is_local_draft_entry(self, entry: F8VariantEntry | None) -> bool: ...

    def _save_variant_draft(
        self,
        *,
        record: F8VariantRecord,
        origin_kind: F8VariantDraftOriginKind | None,
        publish_target_asset_id: str | None,
        publish_base_remote_version_number: int | None,
        draft_id: str | None = None,
    ) -> F8VariantEntry: ...

    def _ensure_logged_in(self) -> bool: ...

    def _offload_selected_variant(
        self,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> bool: ...

    def _rebuild_browser_after_draft_changed(self, *, preserve_variant_id: str | None = None) -> None: ...

    def _rebuild_browser_after_installed_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None: ...

    def _rebuild_browser_after_remote_scope_state_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None: ...

    def _rebuild_browser_after_remote_asset_changed(
        self,
        *,
        preserve_variant_id: str | None = None,
    ) -> None: ...


class VariantCatalogActionsHost(_VariantCatalogDialogHost, Protocol):
    pass


class VariantCatalogSyncHost(_VariantCatalogDialogHost, Protocol):
    pass


class VariantCatalogVersionHost(_VariantCatalogDialogHost, Protocol):
    pass


__all__ = [
    "ComponentCatalogActionsHost",
    "ComponentCatalogSyncHost",
    "ComponentCatalogVersionHost",
    "VariantCatalogActionsHost",
    "VariantCatalogSyncHost",
    "VariantCatalogVersionHost",
    "_ComponentCatalogDialogHost",
    "_VariantCatalogDialogHost",
]
