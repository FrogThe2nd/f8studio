from __future__ import annotations

import json
import logging
import time
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json

from ...ui.support.qt_lifecycle import qt_runtime_error_is_object_deleted
from ...nodegraph.session_schema import extract_layout
from ...ui.support.ui_icons import StudioIcon, icon_for
from .background_tasks import BackgroundCallWorker
from ..components.component_catalog import (
    component_entry_can_hydrate,
    component_entry_has_cached_content,
    component_entry_is_installed,
)
from ..components.component_models import F8ComponentEntry, F8ComponentSourceKind, F8ComponentVisibility

logger = logging.getLogger(__name__)

AUTO_PREVIEW_NODE_THRESHOLD = 10


class ComponentCatalogSelectionMixin:
    LINKED_DRAFT_LABEL: str

    def _initialize_selection_state(self) -> None:
        self._is_handling_selection_change = False
        self._pending_asset_cache_rebuild = False
        self._pending_asset_cache_rebuild_component_id = ""
        self._active_preview_request_id = 0
        self._preview_worker: BackgroundCallWorker | None = None
        self._active_preview_component_id = ""
        self._active_preview_entry: F8ComponentEntry | None = None
        self._active_preview_started_at = 0.0
        self._current_preview_signature: tuple[object, ...] | None = None

    def _selected_entry(self) -> F8ComponentEntry | None:
        try:
            item = self._list.currentItem()
        except RuntimeError as exc:
            if qt_runtime_error_is_object_deleted(exc):
                return None
            raise
        if item is None:
            return None
        component_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        if not component_id:
            return None
        for entry in self._entries:
            if str(entry.record.componentId) == component_id:
                return entry
        return None

    def _selected_component_id(self) -> str:
        entry = self._selected_entry()
        if entry is None:
            return ""
        return str(entry.record.componentId or "").strip()

    def _selected_local_entry(self) -> F8ComponentEntry | None:
        component_id = self._selected_component_id()
        if not component_id:
            return None
        return self._local_entry_for_component_id(component_id)

    def _selected_remote_entry(self) -> F8ComponentEntry | None:
        component_id = self._selected_component_id()
        if not component_id:
            return None
        return self._remote_entry_for_component_id(component_id)

    def _local_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self._draft_service_for_catalog().list_catalog_entries():
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
        active_entry = self._selected_entry()
        if active_entry is None:
            return None, None, None
        component_id = str(active_entry.record.componentId or "").strip()
        local_entry = self._local_entry_for_component_id(component_id)
        if local_entry is None and active_entry.source == F8ComponentSourceKind.local:
            local_entry = active_entry
        return (
            active_entry,
            local_entry,
            self._remote_entry_for_component_id(component_id),
        )

    def _linked_draft_reference_text(self, entry: F8ComponentEntry) -> str | None:
        if not entry.isLocalDraft:
            return None
        target_asset_id = str(entry.draftOriginAssetId or "").strip()
        if not target_asset_id:
            return None
        remote_entry = self._remote_entry_for_component_id(target_asset_id)
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

    def _linked_draft_reference_tooltip(self, entry: F8ComponentEntry) -> str | None:
        if not entry.isLocalDraft:
            return None
        target_asset_id = str(entry.draftOriginAssetId or "").strip()
        if not target_asset_id:
            return None
        return f"Cloud target asset: {target_asset_id}"

    def _linked_draft_badge_text(self, entry: F8ComponentEntry) -> str | None:
        if entry.source == F8ComponentSourceKind.local:
            return None
        asset_id = str(entry.record.componentId or "").strip()
        if not asset_id:
            return None
        draft_entry = self._draft_service_for_catalog().draft_for_publish_target(asset_id)
        if draft_entry is None:
            return None
        return "draft"

    def _linked_draft_badge_tooltip(self, entry: F8ComponentEntry) -> str | None:
        if entry.source == F8ComponentSourceKind.local:
            return None
        asset_id = str(entry.record.componentId or "").strip()
        if not asset_id:
            return None
        draft_entry = self._draft_service_for_catalog().draft_for_publish_target(asset_id)
        if draft_entry is None:
            return None
        draft_name = str(draft_entry.record.name or "").strip()
        if draft_name:
            return f"Linked local draft exists: {draft_name}\nCloud asset: {asset_id}"
        return f"Linked local draft exists.\nCloud asset: {asset_id}"

    def _refresh_action_buttons(
        self,
        selected_entry_override: F8ComponentEntry | None,
    ) -> None:
        selected, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry_override is not None:
            selected = selected_entry_override
            component_id = str(selected_entry_override.record.componentId or "").strip()
            local_entry = self._local_entry_for_component_id(component_id)
            if local_entry is None and selected_entry_override.source == F8ComponentSourceKind.local:
                local_entry = selected_entry_override
            remote_entry = self._remote_entry_for_component_id(component_id)

        current_tab = self._scope_tabs.currentIndex()
        has_selection = selected is not None
        can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
        load_tooltip = self._load_action_tooltip(
            can_offload=can_offload,
            local_entry=local_entry,
        )
        self._set_button_state(
            self._btn_install,
            visible=has_selection and current_tab in {self._TAB_MINE, self._TAB_INSTALLED} and can_load,
            enabled=can_load,
            tooltip=load_tooltip,
            icon_token=StudioIcon.CLOUD_DOWN,
        )

        self._set_button_state(
            self._btn_upload,
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
            and selected.source == F8ComponentSourceKind.remote_public
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
            visible=has_selection and current_tab != self._TAB_DRAFTS,
            enabled=has_selection and current_tab != self._TAB_MINE or remote_entry is not None or selected is not None,
            tooltip="Copy to Draft" if current_tab != self._TAB_MINE else "Open Draft",
            icon_token=StudioIcon.SAVE_AS,
        )

        self._set_button_state(
            self._btn_delete,
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

        self._set_button_state(
            self._btn_edit,
            visible=has_selection and current_tab == self._TAB_DRAFTS,
            enabled=current_tab == self._TAB_DRAFTS and local_entry is not None,
            tooltip="Edit Draft Metadata",
            icon_token=StudioIcon.EDIT,
        )

        visibility_label = "Make Public"
        if remote_entry is not None and remote_entry.visibility == F8ComponentVisibility.public:
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
            enabled=(current_tab == self._TAB_DRAFTS and bool(local_entry is not None and local_entry.draftOriginAssetId)) or local_entry is not None or remote_entry is not None,
            tooltip="History",
            icon_token=StudioIcon.ARTICLE,
        )

        # 'Create on canvas' (_btn_create) removed per catalog UX alignment.

    def _on_selection_changed(self) -> None:
        if self._is_handling_selection_change:
            return
        self._is_handling_selection_change = True
        pending_reload_component_id = ""
        try:
            pending_reload_component_id = self._refresh_selected_preview()
        finally:
            self._is_handling_selection_change = False
        self._run_pending_reload(pending_reload_component_id=pending_reload_component_id)

    def _refresh_selected_preview(self) -> str:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._current_preview_signature = None
            self._raw.setPlainText("")
            self._preview.clear_preview("Select a component to preview.")
            self._refresh_action_buttons(None)
            return ""

        pending_reload_component_id = str(selected_entry.record.componentId or "").strip()
        preview_signature = self._preview_signature_for_entry(selected_entry)
        if preview_signature != self._current_preview_signature:
            self._show_selection_preview(selected_entry=selected_entry)
        self._refresh_action_buttons(selected_entry)
        return pending_reload_component_id

    def _show_selection_preview(self, *, selected_entry: F8ComponentEntry) -> None:
        if not component_entry_can_hydrate(selected_entry):
            self._show_component_preview(entry=selected_entry)
            return
        if component_entry_has_cached_content(selected_entry):
            self._show_component_preview(entry=selected_entry)
            return
        component_id = str(selected_entry.record.componentId or "").strip()
        self._show_deferred_remote_preview(entry=selected_entry, component_id=component_id)

    def _show_deferred_remote_preview(
        self,
        *,
        entry: F8ComponentEntry,
        component_id: str,
    ) -> None:
        self._current_preview_signature = self._preview_signature_for_entry(entry)
        self._raw.setPlainText(
            json.dumps(
                dump_json(entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self._preview.show_deferred_action(
            message="Remote preview is available on demand.",
            button_text="Load preview",
            callback=lambda component_id=component_id, entry=entry: self._load_remote_preview(
                component_id=component_id,
                entry=entry,
            ),
        )

    def _load_remote_preview(self, *, component_id: str, entry: F8ComponentEntry) -> None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return
        selected_component_id = self._selected_component_id()
        if normalized_component_id != selected_component_id:
            return
        self._active_preview_request_id += 1
        request_id = self._active_preview_request_id
        background_client = self._sync_client.clone_for_background()
        started_at = time.perf_counter()
        self._preview.show_loading_message("Loading remote preview…")
        worker = BackgroundCallWorker(
            request_id=request_id,
            task=lambda: background_client.load_component_preview_entry(entry),
        )
        self._preview_worker = worker
        self._active_preview_component_id = normalized_component_id
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
            component_id=self._active_preview_component_id,
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
            component_id=self._active_preview_component_id,
            entry=fallback_entry,
            exc=exc,
            elapsed_seconds=float(elapsed_seconds),
        )

    def _on_remote_preview_loaded(
        self,
        *,
        finished_request_id: int,
        component_id: str,
        result: object,
        elapsed_seconds: float,
        started_at: float,
    ) -> None:
        if finished_request_id != self._active_preview_request_id:
            return
        self._preview_worker = None
        self._active_preview_entry = None
        if str(self._selected_component_id() or "").strip() != str(component_id):
            return
        if not isinstance(result, F8ComponentEntry):
            self._preview.clear_preview("Failed to preview component.\nUnexpected preview payload.")
            return
        cached_entry = self._sync_client._catalog_service.cache_remote_entry(
            result,
            emit_changed=False,
        )
        logger.info(
            "Component manager remote preview loaded component_id=%s network=%.3fs total=%.3fs",
            component_id,
            elapsed_seconds,
            time.perf_counter() - started_at,
        )
        self._show_component_preview(entry=cached_entry)

    def _on_remote_preview_failed(
        self,
        *,
        finished_request_id: int,
        component_id: str,
        entry: F8ComponentEntry,
        exc: object,
        elapsed_seconds: float,
    ) -> None:
        if finished_request_id != self._active_preview_request_id:
            return
        self._preview_worker = None
        self._active_preview_entry = None
        if str(self._selected_component_id() or "").strip() != str(component_id):
            return
        if isinstance(exc, Exception):
            logger.error(
                "Component manager failed to load remote preview component_id=%s",
                component_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            self._show_hydration_failure(
                selected_entry=entry,
                hydration_error=str(exc),
            )
        else:
            self._preview.clear_preview(f"Failed to preview component.\n{exc}")
        logger.warning(
            "Component manager remote preview failed component_id=%s elapsed=%.3fs error=%s",
            component_id,
            elapsed_seconds,
            str(exc),
        )

    def _show_hydration_failure(
        self,
        *,
        selected_entry: F8ComponentEntry,
        hydration_error: str,
    ) -> None:
        self._current_preview_signature = None
        self._raw.setPlainText(
            json.dumps(
                {
                    "componentId": str(selected_entry.record.componentId),
                    "operation": "load_component_preview_entry",
                    "error": hydration_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self._preview.clear_preview(f"Failed to preview component.\n{hydration_error}")

    def _show_component_preview(self, *, entry: F8ComponentEntry) -> None:
        self._current_preview_signature = self._preview_signature_for_entry(entry)
        self._raw.setPlainText(
            json.dumps(
                dump_json(entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        preview_node_count = component_preview_node_count(entry.record.content)
        if preview_node_count > AUTO_PREVIEW_NODE_THRESHOLD:
            self._preview.show_deferred_component_payload(
                entry.record.content,
                message=f"{preview_node_count} nodes.",
                button_text="Load preview",
            )
            return
        self._preview.show_component_payload(entry.record.content)

    @staticmethod
    def _preview_signature_for_entry(entry: F8ComponentEntry) -> tuple[object, ...]:
        return (
            str(entry.record.componentId or "").strip(),
            str(entry.source.value),
            str(entry.remoteRevision or ""),
            str(entry.downloadedAt or ""),
            bool(component_entry_has_cached_content(entry)),
            bool(entry.installed),
            str(entry.record.updatedAt or ""),
            str(entry.record.createdAt or ""),
            str(entry.record.name or ""),
        )

    def _run_pending_reload(self, *, pending_reload_component_id: str) -> None:
        if not self._pending_asset_cache_rebuild:
            return
        reload_component_id = str(
            self._pending_asset_cache_rebuild_component_id or pending_reload_component_id
        ).strip()
        self._pending_asset_cache_rebuild = False
        self._pending_asset_cache_rebuild_component_id = ""
        self._rebuild_browser_after_installed_state_changed(
            preserve_component_id=reload_component_id
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

    @classmethod
    def _load_action_tooltip(cls, *, can_offload: bool, local_entry: F8ComponentEntry | None) -> str:
        if local_entry is not None and local_entry.isLocalDraft:
            return cls.LOCAL_DRAFT_LOAD_TOOLTIP
        if can_offload:
            return "Offload"
        return "Load"

def component_preview_node_count(payload: Any) -> int:
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
