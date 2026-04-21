from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json

from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from ...ui.support.ui_icons import StudioIcon, icon_for
from .background_tasks import BackgroundCallWorker
from ..variants.variant_catalog import variant_entry_has_cached_content, variant_entry_is_installed
from ..variants.variant_models import F8VariantEntry, F8VariantRecord, F8VariantSourceKind, F8VariantVisibility

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _VariantActionButtonState:
    visible: bool
    enabled: bool
    tooltip: str
    icon_token: StudioIcon


@dataclass(frozen=True, slots=True)
class _ResolvedVariantSelection:
    selected_entry: F8VariantEntry | None
    local_entry: F8VariantEntry | None
    remote_entry: F8VariantEntry | None


class VariantCatalogSelectionMixin:
    _TAB_DRAFTS: int
    _TAB_MINE: int
    _TAB_COMMUNITY: int
    _TAB_INSTALLED: int
    LOCAL_DRAFT_LABEL: str
    LINKED_DRAFT_LABEL: str
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
    _render_browser_from_state: Any
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
        self._pending_asset_cache_rebuild = False
        self._pending_asset_cache_rebuild_variant_id = ""
        self._active_preview_request_id = 0
        self._preview_worker: BackgroundCallWorker | None = None
        self._active_preview_variant_id = ""
        self._active_preview_entry: F8VariantEntry | None = None
        self._active_preview_started_at = 0.0
        self._current_preview_signature: tuple[object, ...] | None = None
        self._current_action_button_signature: tuple[object, ...] | None = None
        self._current_raw_preview_text: str | None = None

    def _selected_entry(self) -> F8VariantEntry | None:
        try:
            item = self._list.currentItem()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                return None
            raise
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
        for entry in self._draft_service_for_catalog().list_catalog_entries():
            if str(entry.record.variantId or "").strip() == normalized_variant_id:
                return entry
        return None

    def _remote_entry_for_variant_id(self, variant_id: str) -> F8VariantEntry | None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        return self._sync_client._catalog_service.remote_entry(normalized_variant_id)

    def _resolve_action_entries(
        self,
        selected_entry_override: F8VariantEntry | None = None,
    ) -> _ResolvedVariantSelection:
        active_entry = selected_entry_override
        if active_entry is None:
            active_entry = self._selected_entry()
        if active_entry is None:
            return _ResolvedVariantSelection(None, None, None)
        variant_id = str(active_entry.record.variantId or "").strip()
        local_entry = self._local_entry_for_variant_id(variant_id)
        if local_entry is None and active_entry.source == F8VariantSourceKind.local:
            local_entry = active_entry
        if local_entry is None and selected_entry_override is None:
            fallback_local_entry = self._selected_local_entry()
            if fallback_local_entry is not None and str(fallback_local_entry.record.variantId or "").strip() == variant_id:
                local_entry = fallback_local_entry
        remote_entry = self._remote_entry_for_variant_id(variant_id)
        if remote_entry is None and selected_entry_override is None:
            fallback_remote_entry = self._selected_remote_entry()
            if fallback_remote_entry is not None and str(fallback_remote_entry.record.variantId or "").strip() == variant_id:
                remote_entry = fallback_remote_entry
        return _ResolvedVariantSelection(
            selected_entry=active_entry,
            local_entry=local_entry,
            remote_entry=remote_entry,
        )

    def _selected_action_entries(self) -> tuple[F8VariantEntry | None, F8VariantEntry | None, F8VariantEntry | None]:
        resolved_entries = self._resolve_action_entries()
        return (
            resolved_entries.selected_entry,
            resolved_entries.local_entry,
            resolved_entries.remote_entry,
        )

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
        button.setEnabled(visible and enabled)
        button.setToolTip(tooltip)
        button.setIcon(icon_for(button, icon_token))

    @classmethod
    def _apply_button_state(
        cls,
        button: QtWidgets.QPushButton,
        state: _VariantActionButtonState,
    ) -> None:
        cls._set_button_state(
            button,
            visible=state.visible,
            enabled=state.enabled,
            tooltip=state.tooltip,
            icon_token=state.icon_token,
        )

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
        if owner_text.casefold() == self.LINKED_DRAFT_LABEL.casefold():
            return self.LINKED_DRAFT_LABEL
        return f"by {owner_text}"

    def _linked_draft_reference_text(self, entry: F8VariantEntry) -> str | None:
        if not entry.isLocalDraft:
            return None
        target_asset_id = str(entry.draftOriginAssetId or "").strip()
        if not target_asset_id:
            return None
        remote_entry = self._remote_entry_for_variant_id(target_asset_id)
        if remote_entry is not None:
            owner_name = str(remote_entry.ownerDisplayName or "").strip()
            asset_name = str(remote_entry.record.name or "").strip()
            if owner_name and asset_name:
                return f"linked to {owner_name}.{asset_name}"
            if asset_name:
                return f"linked to {asset_name}"
            if owner_name:
                return f"linked to {owner_name}"
        return f"linked to asset:{target_asset_id[:8]}"

    def _linked_draft_reference_tooltip(self, entry: F8VariantEntry) -> str | None:
        if not entry.isLocalDraft:
            return None
        target_asset_id = str(entry.draftOriginAssetId or "").strip()
        if not target_asset_id:
            return None
        return f"Cloud target asset: {target_asset_id}"

    def _linked_draft_badge_text(self, entry: F8VariantEntry) -> str | None:
        if entry.source == F8VariantSourceKind.local:
            return None
        asset_id = str(entry.record.variantId or "").strip()
        if not asset_id:
            return None
        draft_entry = self._draft_service_for_catalog().draft_for_publish_target(asset_id)
        if draft_entry is None:
            return None
        return "draft"

    def _linked_draft_badge_tooltip(self, entry: F8VariantEntry) -> str | None:
        if entry.source == F8VariantSourceKind.local:
            return None
        asset_id = str(entry.record.variantId or "").strip()
        if not asset_id:
            return None
        draft_entry = self._draft_service_for_catalog().draft_for_publish_target(asset_id)
        if draft_entry is None:
            return None
        draft_name = str(draft_entry.record.name or "").strip()
        if draft_name:
            return f"Linked local draft exists: {draft_name}\nCloud asset: {asset_id}"
        return f"Linked local draft exists.\nCloud asset: {asset_id}"

    def _refresh_action_buttons(self, selected_entry_override: F8VariantEntry | None) -> None:
        resolved_selection = self._resolve_action_entries(selected_entry_override)
        self._refresh_action_buttons_for_resolved_selection(resolved_selection)

    def _refresh_action_buttons_for_resolved_selection(
        self,
        resolved_selection: _ResolvedVariantSelection,
    ) -> None:
        selected = resolved_selection.selected_entry
        local_entry = resolved_selection.local_entry
        remote_entry = resolved_selection.remote_entry
        current_tab = self._scope_tabs.currentIndex()
        has_selection = selected is not None
        can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
        tooltip = self._load_action_tooltip(can_offload=can_offload, local_entry=local_entry)
        install_state = _VariantActionButtonState(
            visible=has_selection and current_tab in {self._TAB_MINE, self._TAB_INSTALLED} and can_load,
            enabled=can_load,
            tooltip=tooltip,
            icon_token=StudioIcon.CLOUD_DOWN,
        )
        upload_state = _VariantActionButtonState(
            visible=has_selection and current_tab in {self._TAB_DRAFTS, self._TAB_INSTALLED},
            enabled=(
                (current_tab == self._TAB_DRAFTS and local_entry is not None)
                or (current_tab == self._TAB_INSTALLED and remote_entry is not None)
            ),
            tooltip="Publish Draft" if current_tab == self._TAB_DRAFTS else "Pull",
            icon_token=StudioIcon.TRANSFER,
        )
        can_subscribe = (
            has_selection
            and selected is not None
            and selected.source == F8VariantSourceKind.remote_public
            and not self._is_owned_remote_entry(selected)
        )
        subscribe_text = "Unsubscribe" if selected is not None and selected.subscribed else "Subscribe"
        subscribe_state = _VariantActionButtonState(
            visible=current_tab == self._TAB_COMMUNITY and can_subscribe,
            enabled=can_subscribe,
            tooltip=subscribe_text,
            icon_token=StudioIcon.HEART_ON if selected is not None and selected.subscribed else StudioIcon.HEART_OFF,
        )
        copy_local_state = _VariantActionButtonState(
            visible=has_selection and current_tab != self._TAB_DRAFTS,
            enabled=has_selection,
            tooltip="Copy to Draft" if current_tab != self._TAB_MINE else "Open Draft",
            icon_token=StudioIcon.SAVE_AS,
        )
        delete_state = _VariantActionButtonState(
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=(
                (current_tab == self._TAB_DRAFTS and local_entry is not None)
                or local_entry is not None
                or (remote_entry is not None and self._is_owned_remote_entry(remote_entry))
                or (current_tab == self._TAB_INSTALLED and can_offload)
            ),
            tooltip="Remove from Installed" if current_tab == self._TAB_INSTALLED else "Delete",
            icon_token=StudioIcon.TRASH,
        )
        edit_state = _VariantActionButtonState(
            visible=has_selection and current_tab == self._TAB_DRAFTS,
            enabled=current_tab == self._TAB_DRAFTS and local_entry is not None,
            tooltip="Edit Draft Metadata",
            icon_token=StudioIcon.EDIT,
        )
        visibility_label = "Make Public"
        if remote_entry is not None and remote_entry.visibility == F8VariantVisibility.public:
            visibility_label = "Make Private"
        visibility_state = _VariantActionButtonState(
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=remote_entry is not None and self._is_owned_remote_entry(remote_entry),
            tooltip=visibility_label,
            icon_token=StudioIcon.PRIVATE if visibility_label == "Make Private" else StudioIcon.PUBLIC,
        )
        history_state = _VariantActionButtonState(
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=((current_tab == self._TAB_DRAFTS) and bool(local_entry is not None and local_entry.draftOriginAssetId)) or local_entry is not None or remote_entry is not None,
            tooltip="History",
            icon_token=StudioIcon.ARTICLE,
        )
        create_state = _VariantActionButtonState(
            visible=False,
            enabled=False,
            tooltip="Create on canvas",
            icon_token=StudioIcon.CIRCLE_PLUS,
        )

        action_button_signature = (
            current_tab,
            install_state,
            upload_state,
            subscribe_state,
            copy_local_state,
            delete_state,
            edit_state,
            visibility_state,
            history_state,
            create_state,
        )
        if action_button_signature == self._current_action_button_signature:
            return
        self._current_action_button_signature = action_button_signature

        self._apply_button_state(self._btn_install, install_state)
        self._apply_button_state(self._btn_upload, upload_state)
        self._apply_button_state(self._btn_subscribe, subscribe_state)
        self._apply_button_state(self._btn_copy_local, copy_local_state)
        self._apply_button_state(self._btn_delete, delete_state)
        self._apply_button_state(self._btn_edit, edit_state)
        self._apply_button_state(self._btn_visibility, visibility_state)
        self._apply_button_state(self._btn_history, history_state)
        self._apply_button_state(self._btn_create, create_state)

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
            self._current_preview_signature = None
            self._set_raw_preview_text("")
            self._preview.clear_preview("Select a variant to preview.")
            self._refresh_action_buttons(None)
            return ""
        pending_reload_variant_id = str(selected_entry.record.variantId or "").strip()
        resolved_selection = self._resolve_action_entries(selected_entry)
        preview_entry = self._selected_preview_entry(
            selected_entry=selected_entry,
            variant_id=pending_reload_variant_id,
            local_entry=resolved_selection.local_entry,
        )
        preview_signature = self._preview_signature_for_entry(preview_entry)
        if preview_signature != self._current_preview_signature:
            self._show_selection_preview(preview_entry=preview_entry)
        self._refresh_action_buttons_for_resolved_selection(resolved_selection)
        return pending_reload_variant_id

    def _selected_preview_entry(
        self,
        *,
        selected_entry: F8VariantEntry,
        variant_id: str,
        local_entry: F8VariantEntry | None,
    ) -> F8VariantEntry:
        del variant_id
        if self._scope_tabs.currentIndex() != self._TAB_DRAFTS or local_entry is None:
            return selected_entry
        return local_entry

    @staticmethod
    def _preview_requires_cache(entry: F8VariantEntry) -> bool:
        return entry.source != F8VariantSourceKind.local and not variant_entry_has_cached_content(entry)

    def _show_selection_preview(self, *, preview_entry: F8VariantEntry) -> None:
        if self._preview_requires_cache(preview_entry):
            self._show_deferred_remote_preview(preview_entry=preview_entry)
            return
        self._show_preview_entry(preview_entry)

    def _show_deferred_remote_preview(self, *, preview_entry: F8VariantEntry) -> None:
        variant_id = str(preview_entry.record.variantId or "").strip()
        self._current_preview_signature = self._preview_signature_for_entry(preview_entry)
        self._set_raw_preview_text(
            json.dumps(
                dump_json(preview_entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self._preview.show_deferred_action(
            message="Remote preview is available on demand.",
            button_text="Load preview",
            callback=lambda variant_id=variant_id, entry=preview_entry: self._load_remote_preview(
                variant_id=variant_id,
                entry=entry,
            ),
        )

    def _load_remote_preview(self, *, variant_id: str, entry: F8VariantEntry) -> None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return
        if normalized_variant_id != self._selected_variant_id():
            return
        self._active_preview_request_id += 1
        request_id = self._active_preview_request_id
        background_client = self._sync_client.clone_for_background()
        started_at = time.perf_counter()
        self._preview.show_loading_message("Loading remote preview…")
        worker = BackgroundCallWorker(
            request_id=request_id,
            task=lambda: background_client.load_variant_preview_entry(entry),
        )
        self._preview_worker = worker
        self._active_preview_variant_id = normalized_variant_id
        self._active_preview_entry = entry
        self._active_preview_started_at = started_at
        worker.succeeded.connect(self._handle_remote_preview_loaded)  # type: ignore[attr-defined]
        worker.failed.connect(self._handle_remote_preview_failed)  # type: ignore[attr-defined]
        worker.start()

    def _handle_remote_preview_loaded(
        self,
        finished_request_id: int,
        result: object,
        elapsed_seconds: float,
    ) -> None:
        self._on_remote_preview_loaded(
            finished_request_id=int(finished_request_id),
            variant_id=self._active_preview_variant_id,
            result=result,
            elapsed_seconds=float(elapsed_seconds),
            started_at=self._active_preview_started_at,
        )

    def _handle_remote_preview_failed(
        self,
        finished_request_id: int,
        exc: object,
        elapsed_seconds: float,
    ) -> None:
        fallback_entry = self._active_preview_entry
        if fallback_entry is None:
            return
        self._on_remote_preview_failed(
            finished_request_id=int(finished_request_id),
            variant_id=self._active_preview_variant_id,
            entry=fallback_entry,
            exc=exc,
            elapsed_seconds=float(elapsed_seconds),
        )

    def _on_remote_preview_loaded(
        self,
        *,
        finished_request_id: int,
        variant_id: str,
        result: object,
        elapsed_seconds: float,
        started_at: float,
    ) -> None:
        if finished_request_id != self._active_preview_request_id:
            return
        self._preview_worker = None
        self._active_preview_entry = None
        if str(self._selected_variant_id() or "").strip() != str(variant_id):
            return
        if not isinstance(result, F8VariantEntry):
            self._preview.clear_preview("Failed to preview variant.\nUnexpected preview payload.")
            return
        cached_entry = self._sync_client._catalog_service.cache_remote_entry(
            result,
            emit_changed=False,
        )
        logger.info(
            "Variant manager remote preview loaded variant_id=%s network=%.3fs total=%.3fs",
            variant_id,
            elapsed_seconds,
            time.perf_counter() - started_at,
        )
        self._show_preview_entry(cached_entry)

    def _on_remote_preview_failed(
        self,
        *,
        finished_request_id: int,
        variant_id: str,
        entry: F8VariantEntry,
        exc: object,
        elapsed_seconds: float,
    ) -> None:
        if finished_request_id != self._active_preview_request_id:
            return
        self._preview_worker = None
        self._active_preview_entry = None
        if str(self._selected_variant_id() or "").strip() != str(variant_id):
            return
        if isinstance(exc, Exception):
            logger.error(
                "Variant manager failed to load remote preview variant_id=%s",
                variant_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            self._show_preview_cache_failure(preview_entry=entry, exc=exc)
        else:
            self._preview.clear_preview(f"Failed to preview variant.\n{exc}")
        logger.warning(
            "Variant manager remote preview failed variant_id=%s elapsed=%.3fs error=%s",
            variant_id,
            elapsed_seconds,
            str(exc),
        )

    def _show_preview_cache_failure(
        self,
        *,
        preview_entry: F8VariantEntry,
        exc: Exception,
    ) -> None:
        self._current_preview_signature = None
        self._set_raw_preview_text(
            json.dumps(
                {
                    "variantId": str(preview_entry.record.variantId),
                    "operation": "load_variant_preview_entry",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self._preview.clear_preview(f"Failed to preview variant.\n{exc}")

    def _show_preview_entry(self, entry: F8VariantEntry) -> None:
        self._current_preview_signature = self._preview_signature_for_entry(entry)
        self._set_raw_preview_text(
            json.dumps(
                dump_json(entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self._preview.show_variant_record(entry.record)

    @staticmethod
    def _preview_signature_for_entry(entry: F8VariantEntry) -> tuple[object, ...]:
        return (
            str(entry.record.variantId or "").strip(),
            str(entry.source.value),
            str(entry.remoteRevision or ""),
            str(entry.downloadedAt or ""),
            bool(variant_entry_has_cached_content(entry)),
            bool(entry.installed),
            str(entry.record.updatedAt or ""),
            str(entry.record.createdAt or ""),
            str(entry.record.name or ""),
        )

    def _run_pending_reload(self, *, pending_reload_variant_id: str) -> None:
        if not self._pending_asset_cache_rebuild:
            return
        reload_variant_id = str(self._pending_asset_cache_rebuild_variant_id or pending_reload_variant_id).strip()
        self._pending_asset_cache_rebuild = False
        self._pending_asset_cache_rebuild_variant_id = ""
        logger.info(
            "Variant manager running deferred browser rebuild after selection handling variant_id=%s",
            reload_variant_id,
        )
        self._rebuild_browser_after_installed_state_changed(
            preserve_variant_id=reload_variant_id
        )

    def _set_raw_preview_text(self, text: str) -> None:
        normalized_text = str(text)
        if normalized_text == self._current_raw_preview_text:
            return
        self._current_raw_preview_text = normalized_text
        self._raw.setPlainText(normalized_text)

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        return

    def _on_list_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is not None:
            self._list.setCurrentItem(item)
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        menu = QtWidgets.QMenu(self)
        if current_tab == self._TAB_DRAFTS:
            edit_action = menu.addAction("Edit Draft Metadata")
            edit_action.setEnabled(local_entry is not None)
            edit_action.triggered.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
            publish_action = menu.addAction("Publish Draft")
            publish_action.setEnabled(local_entry is not None)
            publish_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            duplicate_action = menu.addAction("Copy to Draft")
            duplicate_action.setEnabled(local_entry is not None)
            duplicate_action.triggered.connect(self._on_duplicate_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete Draft")
            delete_action.setEnabled(local_entry is not None)
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(bool(local_entry is not None and local_entry.draftOriginAssetId))
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        elif current_tab == self._TAB_MINE:
            can_load, _can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
            open_draft_action = menu.addAction("Open Draft")
            open_draft_action.setEnabled(selected_entry is not None)
            open_draft_action.triggered.connect(self._on_duplicate_clicked)  # type: ignore[attr-defined]
            if can_load:
                load_action = menu.addAction("Load")
                load_action.triggered.connect(self._on_load_or_offload_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete")
            delete_action.setEnabled(
                remote_entry is not None and self._is_owned_remote_entry(remote_entry)
            )
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
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
            if local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry)):
                offload_action = menu.addAction("Remove from Installed")
                offload_action.triggered.connect(self._on_load_or_offload_clicked)  # type: ignore[attr-defined]
            pull_action = menu.addAction("Pull")
            pull_action.setEnabled(remote_entry is not None and variant_entry_is_installed(remote_entry))
            pull_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        menu.exec(self._list.viewport().mapToGlobal(pos))
