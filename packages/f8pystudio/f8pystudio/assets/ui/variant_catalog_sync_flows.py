from __future__ import annotations

import json

from qtpy import QtWidgets

from f8pysdk.codec import dump_json, validate_as
from f8pysdk.specs import F8VariantRecord

from ..common import new_asset_id
from ..variants.variant_catalog import variant_entry_has_cached_content, variant_entry_is_installed
from ..variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantRemoteRequestError,
    F8VariantSourceKind,
    F8VariantVisibility,
    variant_now_iso,
)
from ...ui.support.ui_notifications import show_info, show_warning


class VariantCatalogSyncFlowsMixin:
    @staticmethod
    def _is_missing_variant_request_error(exc: Exception) -> bool:
        return isinstance(exc, F8VariantRemoteRequestError) and exc.status_code == 404

    def _confirm_create_replacement_variant(
        self,
        *,
        draft_entry: F8VariantEntry,
        missing_variant_id: str,
    ) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Linked cloud variant missing",
            (
                f"The linked cloud variant for draft '{draft_entry.record.name}' was not found.\n\n"
                f"Missing asset: {missing_variant_id}\n\n"
                "Create a new cloud variant and relink this draft to it?"
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _create_remote_variant_for_draft(
        self,
        draft_entry: F8VariantEntry,
        *,
        preferred_visibility: F8VariantVisibility | None = None,
    ) -> F8VariantEntry | None:
        visibility = preferred_visibility or self._choose_visibility()
        if visibility is None:
            return None
        publish_asset_id = new_asset_id()
        upload_record = validate_as(
            F8VariantRecord,
            {
                **dump_json(draft_entry.record, mode="json"),
                "variantId": publish_asset_id,
            },
        )
        source = (
            F8VariantSourceKind.remote_private
            if visibility == F8VariantVisibility.private
            else F8VariantSourceKind.remote_public
        )
        try:
            published = self._sync_client.create_variant(
                F8VariantEntry(
                    record=upload_record,
                    source=source,
                    visibility=visibility,
                    installed=True,
                    hasCachedContent=True,
                )
            )
        except Exception as exc:
            show_warning(self, "Publish failed", str(exc))
            return None
        _ = self._save_variant_draft(
            record=draft_entry.record,
            origin_kind=draft_entry.draftOriginKind or F8VariantDraftOriginKind.new,
            publish_target_asset_id=publish_asset_id,
            publish_base_remote_revision=published.remoteRevision,
            draft_id=str(draft_entry.record.variantId),
        )
        self._rebuild_browser_after_draft_changed(
            preserve_variant_id=str(draft_entry.record.variantId)
        )
        return published

    def _load_selected_remote_variant(self) -> F8VariantEntry | None:
        remote_entry = self._selected_remote_entry()
        if remote_entry is None:
            return None
        try:
            installed = self._sync_client.install_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return None
        self._rebuild_browser_after_installed_state_changed(
            preserve_variant_id=str(installed.record.variantId)
        )
        return installed

    def _offload_selected_variant(
        self,
        *,
        local_entry: F8VariantEntry | None,
        remote_entry: F8VariantEntry | None,
    ) -> bool:
        changed = False
        if remote_entry is not None and variant_entry_is_installed(remote_entry):
            changed = self._sync_client._catalog_service.uninstall_remote_entry(str(remote_entry.record.variantId)) is not None or changed
        if changed:
            preserve_variant_id = ""
            if remote_entry is not None:
                preserve_variant_id = str(remote_entry.record.variantId)
            elif local_entry is not None:
                preserve_variant_id = str(local_entry.record.variantId)
            self._rebuild_browser_after_installed_state_changed(
                preserve_variant_id=preserve_variant_id
            )
        return changed

    def _on_load_or_offload_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None or self._is_local_draft_entry(local_entry):
            return
        if local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry)):
            if self._offload_selected_variant(local_entry=local_entry, remote_entry=remote_entry):
                show_info(
                    self,
                    "Removed from Installed",
                    f"Removed from installed cache:\n{selected_entry.record.name}",
                )
            return
        if remote_entry is None:
            return
        loaded = self._load_selected_remote_variant()
        if loaded is not None:
            show_info(self, "Loaded", f"Loaded variant:\n{loaded.record.name}")

    def _pull_selected_variant(self, *, force_replace_local: bool = False) -> F8VariantEntry | None:
        _ = force_replace_local
        remote_entry = self._selected_remote_entry()
        if remote_entry is None:
            return None
        try:
            updated = self._sync_client.install_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Pull failed", str(exc))
            return None
        self._rebuild_browser_after_installed_state_changed(
            preserve_variant_id=str(updated.record.variantId)
        )
        return updated

    def _on_sync_or_update_clicked(self) -> None:
        if self._scope_tabs.currentIndex() == self._TAB_DRAFTS:
            draft_entry = self._selected_local_entry()
            if draft_entry is None:
                return
            published = self._publish_variant_draft(draft_entry)
            if published is not None:
                show_info(self, "Published", f"Published draft:\n{published.record.name}")
            return
        if self._scope_tabs.currentIndex() == self._TAB_INSTALLED:
            updated = self._pull_selected_variant()
            if updated is not None:
                show_info(self, "Pulled", f"Pulled variant:\n{updated.record.name}")
            return
        return

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
        self._rebuild_browser_after_remote_scope_state_changed(
            preserve_variant_id=str(updated.record.variantId)
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
        self._rebuild_browser_after_remote_asset_changed(
            preserve_variant_id=str(selected_entry.record.variantId)
        )

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

    def _publish_variant_draft(self, draft_entry: F8VariantEntry) -> F8VariantEntry | None:
        target_asset_id = None if draft_entry.draftOriginAssetId is None else str(draft_entry.draftOriginAssetId).strip() or None
        if target_asset_id:
            remote_entry = self._remote_entry_for_variant_id(target_asset_id)
            if remote_entry is None:
                try:
                    remote_entry = self._sync_client.get_variant(target_asset_id)
                except F8VariantRemoteRequestError as exc:
                    if self._is_missing_variant_request_error(exc) and self._confirm_create_replacement_variant(
                        draft_entry=draft_entry,
                        missing_variant_id=target_asset_id,
                    ):
                        return self._create_remote_variant_for_draft(draft_entry)
                    show_warning(self, "Publish failed", str(exc))
                    return None
                except Exception as exc:
                    show_warning(self, "Publish failed", str(exc))
                    return None
            if not variant_entry_has_cached_content(remote_entry):
                try:
                    remote_entry = self._sync_client.cache_variant_content(target_asset_id)
                except F8VariantRemoteRequestError as exc:
                    if self._is_missing_variant_request_error(exc) and self._confirm_create_replacement_variant(
                        draft_entry=draft_entry,
                        missing_variant_id=target_asset_id,
                    ):
                        return self._create_remote_variant_for_draft(
                            draft_entry,
                            preferred_visibility=remote_entry.visibility,
                        )
                    show_warning(self, "Publish failed", str(exc))
                    return None
                except Exception as exc:
                    show_warning(self, "Publish failed", str(exc))
                    return None
            content_changed = json.dumps(remote_entry.record.spec, sort_keys=True, default=str) != json.dumps(
                draft_entry.record.spec,
                sort_keys=True,
                default=str,
            )
            kind_changed = remote_entry.record.kind != draft_entry.record.kind
            base_changed = str(remote_entry.record.baseNodeType) != str(draft_entry.record.baseNodeType)
            service_changed = str(remote_entry.record.serviceClass) != str(draft_entry.record.serviceClass)
            operator_changed = str(remote_entry.record.operatorClass or "") != str(draft_entry.record.operatorClass or "")
            name_changed = str(remote_entry.record.name) != str(draft_entry.record.name)
            description_changed = str(remote_entry.record.description) != str(draft_entry.record.description)
            tags_changed = list(remote_entry.record.tags or []) != list(draft_entry.record.tags or [])
            try:
                if not content_changed and not kind_changed and not base_changed and not service_changed and not operator_changed and (name_changed or description_changed or tags_changed):
                    published = self._sync_client.patch_variant_meta(
                        target_asset_id,
                        name=str(draft_entry.record.name),
                        description=str(draft_entry.record.description),
                        tags=[str(tag) for tag in list(draft_entry.record.tags or [])],
                    )
                else:
                    upload_record = validate_as(
                        F8VariantRecord,
                        {
                            **dump_json(draft_entry.record, mode="json"),
                            "variantId": target_asset_id,
                        },
                    )
                    published = self._sync_client.update_variant(
                        F8VariantEntry(
                            record=upload_record,
                            source=remote_entry.source,
                            visibility=remote_entry.visibility,
                            remoteRevision=remote_entry.remoteRevision,
                            installed=True,
                            hasCachedContent=True,
                        )
                    )
            except F8VariantRemoteRequestError as exc:
                if self._is_missing_variant_request_error(exc) and self._confirm_create_replacement_variant(
                    draft_entry=draft_entry,
                    missing_variant_id=target_asset_id,
                ):
                    return self._create_remote_variant_for_draft(
                        draft_entry,
                        preferred_visibility=remote_entry.visibility,
                    )
                show_warning(self, "Publish failed", str(exc))
                return None
            except Exception as exc:
                show_warning(self, "Publish failed", str(exc))
                return None
            _ = self._save_variant_draft(
                record=draft_entry.record,
                origin_kind=draft_entry.draftOriginKind,
                publish_target_asset_id=target_asset_id,
                publish_base_remote_revision=published.remoteRevision,
                draft_id=str(draft_entry.record.variantId),
            )
            self._rebuild_browser_after_draft_changed(
                preserve_variant_id=str(draft_entry.record.variantId)
            )
            return published
        return self._create_remote_variant_for_draft(draft_entry)
