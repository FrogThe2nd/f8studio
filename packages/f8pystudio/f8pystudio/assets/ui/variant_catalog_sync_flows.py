from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk.codec import copy_model, dump_json, validate_as
from f8pysdk.specs import F8VariantRecord

from ..common import new_asset_id
from ..variants.variant_catalog import variant_entry_is_installed
from ..variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantSourceKind,
    F8VariantSyncState,
    F8VariantVisibility,
    variant_now_iso,
)
from ..variants.variant_repository import ensure_unique_variant_name
from ...ui.support.ui_notifications import show_info, show_warning
from .asset_sync_resolution import AssetSyncDirection, determine_asset_sync_direction


class VariantCatalogSyncFlowsMixin:
    def _load_selected_remote_variant(self) -> F8VariantEntry | None:
        remote_entry = self._selected_remote_entry()
        if remote_entry is None:
            return None
        try:
            installed = self._sync_client.install_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return None
        try:
            installed = self._ensure_owned_remote_variant_has_local_head(installed)
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return None
        self._reload(preserve_variant_id=str(installed.record.variantId))
        return installed

    def _offload_selected_variant(
        self,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> bool:
        changed = False
        if local_entry is not None:
            changed = self._sync_client._catalog_service.delete_local_entry(str(local_entry.record.variantId)) or changed
        if remote_entry is not None and variant_entry_is_installed(remote_entry):
            changed = self._sync_client._catalog_service.uninstall_remote_entry(str(remote_entry.record.variantId)) is not None or changed
        if changed:
            preserve_variant_id = ""
            if remote_entry is not None:
                preserve_variant_id = str(remote_entry.record.variantId)
            elif local_entry is not None:
                preserve_variant_id = str(local_entry.record.variantId)
            self._reload(preserve_variant_id=preserve_variant_id)
        return changed

    def _on_load_or_offload_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None or self._is_local_draft_entry(local_entry):
            return
        if local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry)):
            _ = self._offload_selected_variant(local_entry=local_entry, remote_entry=remote_entry)
            return
        if remote_entry is None:
            return
        loaded = self._load_selected_remote_variant()
        if loaded is not None:
            show_info(self, "Loaded", f"Loaded variant:\n{loaded.record.name}")

    def _variant_sync_decision(
        self,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> AssetSyncDirection:
        return determine_asset_sync_direction(
            has_local_entry=local_entry is not None,
            has_remote_entry=remote_entry is not None,
            local_version_number=None if local_entry is None else local_entry.localVersionNumber,
            remote_version_number=None if remote_entry is None else remote_entry.remoteVersionNumber,
            sync_base_remote_revision=None if local_entry is None else local_entry.syncBaseRemoteRevision,
            sync_base_remote_version_number=None if local_entry is None else local_entry.syncBaseRemoteVersionNumber,
            sync_base_local_version_number=None if local_entry is None else local_entry.syncBaseLocalVersionNumber,
            current_remote_revision=None if remote_entry is None else remote_entry.remoteRevision,
        ).direction

    def _prompt_variant_conflict_resolution(self, *, include_push: bool) -> str:
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

    def _duplicate_local_variant_for_conflict(self, local_entry: F8VariantEntry | None) -> bool:
        if local_entry is None:
            return False
        duplicate_record = copy_model(
            local_entry.record,
            update={
                "variantId": new_asset_id(),
                "name": ensure_unique_variant_name(
                    local_entry.record.baseNodeType,
                    f"{str(local_entry.record.name or '').strip()} (Draft Copy)",
                    existing_records=[entry.record for entry in self._sync_client._catalog_service._local_provider.load_entries()],
                ),
                "updatedAt": variant_now_iso(),
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(
            F8VariantEntry(
                record=duplicate_record,
                source=F8VariantSourceKind.local,
                isLocalDraft=True,
                draftOriginKind=F8VariantDraftOriginKind.copy_local,
                draftOriginAssetId=str(local_entry.record.variantId),
                draftOriginRevision=local_entry.remoteRevision,
            )
        )
        return True

    def _sync_selected_variant(self) -> F8VariantEntry | None:
        local_entry = self._selected_local_entry()
        remote_entry = self._selected_remote_entry()
        if not self._ensure_logged_in():
            return None
        direction = self._variant_sync_decision(local_entry=local_entry, remote_entry=remote_entry)
        if direction == AssetSyncDirection.pull:
            return self._pull_selected_variant()
        if direction == AssetSyncDirection.conflict:
            resolution = self._prompt_variant_conflict_resolution(include_push=True)
            if resolution == "push":
                return self._push_selected_variant(local_entry=local_entry, remote_entry=remote_entry)
            if resolution == "replace":
                return self._pull_selected_variant(force_replace_local=True)
            if resolution == "fork_pull":
                if not self._duplicate_local_variant_for_conflict(local_entry):
                    return None
                return self._pull_selected_variant(force_replace_local=True)
            return None
        if direction == AssetSyncDirection.noop:
            return None
        return self._push_selected_variant(local_entry=local_entry, remote_entry=remote_entry)

    def _push_selected_variant(
        self,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> F8VariantEntry | None:
        if local_entry is None:
            return None
        if remote_entry is not None:
            entry_to_upload = copy_model(
                local_entry,
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
            source = (
                F8VariantSourceKind.remote_private
                if visibility == F8VariantVisibility.private
                else F8VariantSourceKind.remote_public
            )
            entry_to_upload = validate_as(
                F8VariantEntry,
                {
                    **dump_json(local_entry, mode="json"),
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
                "syncState": F8VariantSyncState.synced,
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(saved_local_entry)
        self._reload(preserve_variant_id=str(uploaded.record.variantId))
        return uploaded

    def _pull_selected_variant(self, *, force_replace_local: bool = False) -> F8VariantEntry | None:
        local_entry = self._selected_local_entry()
        remote_entry = self._selected_remote_entry()
        if remote_entry is None:
            return None
        if local_entry is not None and not force_replace_local:
            direction = self._variant_sync_decision(local_entry=local_entry, remote_entry=remote_entry)
            if direction == AssetSyncDirection.conflict:
                resolution = self._prompt_variant_conflict_resolution(include_push=False)
                if resolution == "replace":
                    return self._pull_selected_variant(force_replace_local=True)
                if resolution == "fork_pull":
                    if not self._duplicate_local_variant_for_conflict(local_entry):
                        return None
                    return self._pull_selected_variant(force_replace_local=True)
                return None
        try:
            updated = self._sync_client.install_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Pull failed", str(exc))
            return None
        if local_entry is None:
            try:
                updated = self._ensure_owned_remote_variant_has_local_head(updated)
            except Exception as exc:
                show_warning(self, "Pull failed", str(exc))
                return None
        else:
            replacement_entry = self._local_seed_from_remote_entry(updated, mark_modified=False)
            _ = self._replace_local_variant_head(replacement_entry)
        self._reload(preserve_variant_id=str(updated.record.variantId))
        return updated

    def _on_sync_or_update_clicked(self) -> None:
        if self._scope_tabs.currentIndex() == self._TAB_INSTALLED:
            updated = self._pull_selected_variant()
            if updated is not None:
                show_info(self, "Pulled", f"Pulled variant:\n{updated.record.name}")
            return
        synced = self._sync_selected_variant()
        if synced is not None:
            show_info(self, "Synced", f"Synced variant:\n{synced.record.name}")

    def _on_upload_clicked(self) -> None:
        self._on_sync_or_update_clicked()

    def _on_install_clicked(self) -> None:
        self._on_load_or_offload_clicked()

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
                loaded = self._sync_client.install_variant(str(updated.record.variantId))
                show_info(self, "Subscribed", f"Subscribed and loaded variant:\n{loaded.record.name}")
        except Exception as exc:
            show_warning(self, "Subscription failed", str(exc))
            return
        self._reload()

    def _replace_local_variant_head(self, entry: F8VariantEntry) -> F8VariantEntry:
        variant_id = str(entry.record.variantId or "").strip()
        if variant_id:
            _ = self._sync_client._catalog_service.delete_local_entry(variant_id)
        return self._sync_client._catalog_service.upsert_local_entry(entry)

    @staticmethod
    def _local_seed_from_remote_entry(
        remote_entry: F8VariantEntry,
        *,
        record: F8VariantRecord | None = None,
        mark_modified: bool,
    ) -> F8VariantEntry:
        remote_version_number = None if remote_entry.remoteVersionNumber is None else int(remote_entry.remoteVersionNumber)
        local_version_number: int | None = remote_version_number
        sync_base_local_version_number: int | None = remote_version_number
        if mark_modified:
            local_version_number = 1 if remote_version_number is None else remote_version_number + 1
        return copy_model(
            remote_entry,
            update={
                "record": remote_entry.record if record is None else record,
                "source": F8VariantSourceKind.local,
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

    def _ensure_owned_remote_variant_has_local_head(self, entry: F8VariantEntry) -> F8VariantEntry:
        if not self._is_owned_remote_entry(entry):
            return entry
        existing_local_entry = self._local_entry_for_variant_id(str(entry.record.variantId))
        if existing_local_entry is not None:
            return existing_local_entry
        return self._sync_client._catalog_service.upsert_local_entry(
            self._local_seed_from_remote_entry(entry, mark_modified=False)
        )

    def _on_visibility_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or not self._is_owned_remote_entry(selected_entry):
            return
        try:
            selected_entry = self._sync_client.get_variant(str(selected_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        next_visibility = F8VariantVisibility.public
        prompt = "Make this remote variant public?"
        if selected_entry.visibility == F8VariantVisibility.public:
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
        try:
            self._sync_client.update_variant_visibility(
                str(selected_entry.record.variantId),
                visibility=next_visibility,
                revision=selected_entry.remoteRevision,
            )
        except Exception as exc:
            show_warning(self, "Visibility update failed", str(exc))
            return
        self._reload()

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
