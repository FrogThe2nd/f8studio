from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk.codec import copy_model, dump_json, validate_as

from ..common import new_asset_id
from ..components.component_catalog import component_entry_is_installed
from ..components.component_models import (
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from ...ui.support.ui_notifications import show_warning
from .asset_sync_resolution import AssetSyncDirection, determine_asset_sync_direction


class ComponentCatalogSyncFlowsMixin:
    @staticmethod
    def _component_sync_decision(
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
