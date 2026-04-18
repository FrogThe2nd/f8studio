from __future__ import annotations

import json
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json

from ...ui.support.ui_icons import StudioIcon, icon_for
from ..variants.variant_catalog import variant_entry_has_cached_content, variant_entry_is_installed
from ..variants.variant_models import F8VariantEntry, F8VariantRecord, F8VariantSourceKind, F8VariantVisibility

logger = logging.getLogger(__name__)


class VariantCatalogSelectionMixin:
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    LOCAL_DRAFT_LABEL: str
    LOCAL_DRAFT_LOAD_TOOLTIP: str
    _entries: list[F8VariantEntry]
    _sync_client: Any
    _list: QtWidgets.QListWidget
    _scope_tabs: Any
    _raw: QtWidgets.QPlainTextEdit
    _preview: Any
    _btn_install: QtWidgets.QPushButton
    _btn_upload: QtWidgets.QPushButton
    _btn_subscribe: QtWidgets.QPushButton
    _btn_copy_local: QtWidgets.QPushButton
    _btn_delete: QtWidgets.QPushButton
    _btn_edit: QtWidgets.QPushButton
    _btn_visibility: QtWidgets.QPushButton
    _btn_history: QtWidgets.QPushButton
    _btn_create: QtWidgets.QPushButton
    _reload: Any
    _on_create_clicked: Any
    _on_edit_clicked: Any
    _on_load_or_offload_clicked: Any
    _on_delete_clicked: Any
    _on_duplicate_clicked: Any
    _on_sync_or_update_clicked: Any
    _on_visibility_clicked: Any
    _on_history_clicked: Any
    _on_subscribe_clicked: Any
    _is_owned_remote_entry: Any

    def _initialize_selection_state(self) -> None:
        self._is_handling_selection_change = False
        self._pending_reload_from_variants_changed = False
        self._pending_reload_variant_id = ""

    def _selected_entry(self) -> F8VariantEntry | None:
        item = self._list.currentItem()
        if item is None:
            return None
        variant_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip()
        if not variant_id:
            return None
        for entry in self._entries:
            if str(entry.record.variantId) == variant_id:
                return entry
        return None

    def _selected_variant_id(self) -> str:
        entry = self._selected_entry()
        if entry is None:
            return ""
        return str(entry.record.variantId or "").strip()

    def _selected_local_entry(self) -> F8VariantEntry | None:
        variant_id = self._selected_variant_id()
        if not variant_id:
            return None
        return self._local_entry_for_variant_id(variant_id)

    def _selected_remote_entry(self) -> F8VariantEntry | None:
        variant_id = self._selected_variant_id()
        if not variant_id:
            return None
        return self._remote_entry_for_variant_id(variant_id)

    def _selected_variant(self) -> F8VariantRecord | None:
        entry = self._selected_entry()
        return None if entry is None else entry.record

    def _local_entry_for_variant_id(self, variant_id: str) -> F8VariantEntry | None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        for entry in self._sync_client._catalog_service._local_provider.load_entries():
            if str(entry.record.variantId or "").strip() == normalized_variant_id:
                return entry
        return None

    def _remote_entry_for_variant_id(self, variant_id: str) -> F8VariantEntry | None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        return self._sync_client._catalog_service.remote_entry(normalized_variant_id)

    def _selected_action_entries(self) -> tuple[F8VariantEntry | None, F8VariantEntry | None, F8VariantEntry | None]:
        active_entry = self._selected_entry()
        if active_entry is None:
            return None, None, None
        variant_id = str(active_entry.record.variantId or "").strip()
        local_entry = self._local_entry_for_variant_id(variant_id)
        if local_entry is None:
            fallback_local_entry = self._selected_local_entry()
            if fallback_local_entry is not None and str(fallback_local_entry.record.variantId or "").strip() == variant_id:
                local_entry = fallback_local_entry
        remote_entry = self._remote_entry_for_variant_id(variant_id)
        if remote_entry is None:
            fallback_remote_entry = self._selected_remote_entry()
            if fallback_remote_entry is not None and str(fallback_remote_entry.record.variantId or "").strip() == variant_id:
                remote_entry = fallback_remote_entry
        return active_entry, local_entry, remote_entry

    def _set_button_state(
        self,
        button: QtWidgets.QPushButton,
        *,
        visible: bool,
        enabled: bool,
        tooltip: str,
        icon_token: StudioIcon,
    ) -> None:
        button.setVisible(visible)
        button.setEnabled(visible and enabled)
        button.setToolTip(tooltip)
        button.setIcon(icon_for(button, icon_token))

    @staticmethod
    def _is_local_draft_entry(entry: F8VariantEntry | None) -> bool:
        return entry is not None and entry.isLocalDraft

    @classmethod
    def _load_action_availability(
        cls,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> tuple[bool, bool]:
        if cls._is_local_draft_entry(local_entry):
            return False, False
        can_load = remote_entry is not None and not variant_entry_is_installed(remote_entry)
        can_offload = local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry))
        return can_load, can_offload

    def _load_action_tooltip(self, *, can_offload: bool, local_entry: F8VariantEntry | None) -> str:
        if local_entry is not None and local_entry.isLocalDraft:
            return self.LOCAL_DRAFT_LOAD_TOOLTIP
        if can_offload:
            return "Offload"
        return "Load"

    def _owner_label_text(self, owner_display_name: str | None) -> str | None:
        if owner_display_name is None:
            return None
        owner_text = str(owner_display_name).strip()
        if not owner_text:
            return None
        if owner_text.casefold() == self.LOCAL_DRAFT_LABEL.casefold():
            return self.LOCAL_DRAFT_LABEL
        return f"by {owner_text}"

    def _refresh_action_buttons(self, selected_entry_override: F8VariantEntry | None) -> None:
        selected, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry_override is not None:
            selected = selected_entry_override
            variant_id = str(selected_entry_override.record.variantId or "").strip()
            local_entry = self._local_entry_for_variant_id(variant_id)
            remote_entry = self._remote_entry_for_variant_id(variant_id)

        current_tab = self._scope_tabs.currentIndex()
        has_selection = selected is not None
        can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
        tooltip = self._load_action_tooltip(can_offload=can_offload, local_entry=local_entry)
        self._set_button_state(
            self._btn_install,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=can_load or can_offload,
            tooltip=tooltip,
            icon_token=StudioIcon.CLOUD_DOWN if not can_offload else StudioIcon.CLOUD_UP,
        )
        self._set_button_state(
            self._btn_upload,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)),
            tooltip="Sync",
            icon_token=StudioIcon.TRANSFER,
        )
        can_subscribe = (
            has_selection
            and selected is not None
            and selected.source == F8VariantSourceKind.remote_public
            and not self._is_owned_remote_entry(selected)
        )
        subscribe_text = "Unsubscribe" if selected is not None and selected.subscribed else "Subscribe"
        self._set_button_state(
            self._btn_subscribe,
            visible=current_tab == self._TAB_COMMUNITY and can_subscribe,
            enabled=can_subscribe,
            tooltip=subscribe_text,
            icon_token=StudioIcon.HEART_ON if selected is not None and selected.subscribed else StudioIcon.HEART_OFF,
        )
        self._set_button_state(
            self._btn_copy_local,
            visible=has_selection and current_tab != self._TAB_INSTALLED,
            enabled=has_selection,
            tooltip="Copy to Draft",
            icon_token=StudioIcon.SAVE_AS,
        )
        self._set_button_state(
            self._btn_delete,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)),
            tooltip="Delete",
            icon_token=StudioIcon.TRASH,
        )
        self._set_button_state(
            self._btn_edit,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=local_entry is not None,
            tooltip="Edit Metadata",
            icon_token=StudioIcon.EDIT,
        )
        visibility_label = "Make Public"
        if remote_entry is not None and remote_entry.visibility == F8VariantVisibility.public:
            visibility_label = "Make Private"
        self._set_button_state(
            self._btn_visibility,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=remote_entry is not None and self._is_owned_remote_entry(remote_entry),
            tooltip=visibility_label,
            icon_token=StudioIcon.PRIVATE if visibility_label == "Make Private" else StudioIcon.PUBLIC,
        )
        self._set_button_state(
            self._btn_history,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=local_entry is not None or remote_entry is not None,
            tooltip="History",
            icon_token=StudioIcon.ARTICLE,
        )
        self._set_button_state(
            self._btn_create,
            visible=has_selection and current_tab == self._TAB_INSTALLED,
            enabled=selected is not None and variant_entry_is_installed(selected),
            tooltip="Create on canvas",
            icon_token=StudioIcon.CIRCLE_PLUS,
        )

    def _on_selection_changed(self) -> None:
        if self._is_handling_selection_change:
            return
        self._is_handling_selection_change = True
        pending_reload_variant_id = ""
        try:
            pending_reload_variant_id = self._refresh_selected_preview()
        finally:
            self._is_handling_selection_change = False
        self._run_pending_reload(pending_reload_variant_id=pending_reload_variant_id)

    def _refresh_selected_preview(self) -> str:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._raw.setPlainText("")
            self._preview.clear_preview("Select a variant to preview.")
            self._refresh_action_buttons(None)
            return ""
        pending_reload_variant_id = str(selected_entry.record.variantId or "").strip()
        preview_entry = self._selected_preview_entry(
            selected_entry=selected_entry,
            variant_id=pending_reload_variant_id,
        )
        if self._preview_requires_cache(preview_entry):
            preview_entry = self._cache_preview_entry(
                preview_entry=preview_entry,
                variant_id=pending_reload_variant_id,
            )
        else:
            self._show_preview_entry(preview_entry)
        self._refresh_action_buttons(selected_entry)
        return pending_reload_variant_id

    def _selected_preview_entry(
        self,
        *,
        selected_entry: F8VariantEntry,
        variant_id: str,
    ) -> F8VariantEntry:
        if self._scope_tabs.currentIndex() != self._TAB_MINE:
            return selected_entry
        local_entry = self._local_entry_for_variant_id(variant_id)
        if local_entry is None:
            return selected_entry
        return local_entry

    @staticmethod
    def _preview_requires_cache(entry: F8VariantEntry) -> bool:
        return entry.source != F8VariantSourceKind.local and not variant_entry_has_cached_content(entry)

    def _cache_preview_entry(
        self,
        *,
        preview_entry: F8VariantEntry,
        variant_id: str,
    ) -> F8VariantEntry:
        logger.warning(
            "Variant manager caching remote selection for preview variant_id=%s source=%s installed=%s",
            variant_id,
            preview_entry.source.value,
            bool(preview_entry.installed),
        )
        try:
            cached_entry = self._sync_client.cache_variant_content(str(preview_entry.record.variantId))
        except Exception as exc:
            logger.exception(
                "Variant manager failed to cache selected variant preview variant_id=%s",
                variant_id,
            )
            self._show_preview_cache_failure(preview_entry=preview_entry, exc=exc)
            return preview_entry

        logger.warning(
            "Variant manager cached selected variant preview variant_id=%s installed=%s has_cached_content=%s",
            str(cached_entry.record.variantId or "").strip(),
            bool(cached_entry.installed),
            bool(cached_entry.hasCachedContent),
        )
        self._show_preview_entry(cached_entry)
        return cached_entry

    def _show_preview_cache_failure(
        self,
        *,
        preview_entry: F8VariantEntry,
        exc: Exception,
    ) -> None:
        self._raw.setPlainText(
            json.dumps(
                {
                    "variantId": str(preview_entry.record.variantId),
                    "operation": "cache_variant_content",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self._preview.clear_preview(f"Failed to preview variant.\n{exc}")

    def _show_preview_entry(self, entry: F8VariantEntry) -> None:
        self._raw.setPlainText(
            json.dumps(
                dump_json(entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self._preview.show_variant_record(entry.record)

    def _run_pending_reload(self, *, pending_reload_variant_id: str) -> None:
        if not self._pending_reload_from_variants_changed:
            return
        reload_variant_id = str(self._pending_reload_variant_id or pending_reload_variant_id).strip()
        self._pending_reload_from_variants_changed = False
        self._pending_reload_variant_id = ""
        logger.warning(
            "Variant manager running deferred reload after selection handling variant_id=%s",
            reload_variant_id,
        )
        self._reload(preserve_variant_id=reload_variant_id)

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self._on_create_clicked()

    def _on_list_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is not None:
            self._list.setCurrentItem(item)
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        menu = QtWidgets.QMenu(self)
        if current_tab == self._TAB_MINE:
            can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
            edit_action = menu.addAction("Edit Metadata")
            edit_action.setEnabled(local_entry is not None)
            edit_action.triggered.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
            load_action = menu.addAction("Offload" if can_offload else "Load")
            load_action.setEnabled(can_load or can_offload)
            load_action.triggered.connect(self._on_load_or_offload_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete")
            delete_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
            duplicate_action = menu.addAction("Copy to Draft")
            duplicate_action.triggered.connect(self._on_duplicate_clicked)  # type: ignore[attr-defined]
            sync_action = menu.addAction("Sync")
            sync_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            sync_action.triggered.connect(self._on_sync_or_update_clicked)  # type: ignore[attr-defined]
            visibility_label = "Make Public"
            if remote_entry is not None and remote_entry.visibility == F8VariantVisibility.public:
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
                selected_entry.source == F8VariantSourceKind.remote_public and not self._is_owned_remote_entry(selected_entry)
            )
            subscribe_action.triggered.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_duplicate_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        else:
            offload_action = menu.addAction("Offload")
            offload_action.setEnabled(local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry)))
            offload_action.triggered.connect(self._on_load_or_offload_clicked)  # type: ignore[attr-defined]
            pull_action = menu.addAction("Pull")
            pull_action.setEnabled(remote_entry is not None and variant_entry_is_installed(remote_entry))
            pull_action.triggered.connect(self._on_sync_or_update_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        if variant_entry_is_installed(selected_entry):
            menu.addSeparator()
            create_action = menu.addAction("Create on canvas")
            create_action.triggered.connect(self._on_create_clicked)  # type: ignore[attr-defined]
        menu.exec(self._list.viewport().mapToGlobal(pos))
