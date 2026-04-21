from __future__ import annotations

import json
import logging

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json, validate_as

from ..common import new_asset_id
from ..components.component_catalog import component_entry_has_cached_content, component_entry_is_installed
from ..components.component_models import (
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRemoteRequestError,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from ..components.component_repository import (
    export_component_to_json,
    import_component_from_json,
    upsert_component,
)
from ...nodegraph.component_publish_payload import (
    collect_component_selected_node_ids,
    trim_component_publish_payload_to_selected_nodes,
)
from ...ui.support.ui_notifications import show_info, show_warning
from .project_asset_dialogs import AssetOverwriteChoice, AssetOverwriteMetaDialog, ProjectAssetMetaDialog


logger = logging.getLogger(__name__)


class ComponentCatalogActionsMixin:
    @staticmethod
    def _is_missing_component_request_error(exc: Exception) -> bool:
        return isinstance(exc, F8ComponentRemoteRequestError) and exc.status_code == 404

    def _confirm_create_replacement_component(
        self,
        *,
        draft_entry: F8ComponentEntry,
        missing_component_id: str,
    ) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Linked cloud component missing",
            (
                f"The linked cloud component for draft '{draft_entry.record.name}' was not found.\n\n"
                f"Missing asset: {missing_component_id}\n\n"
                "Create a new cloud component and relink this draft to it?"
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _create_remote_component_for_draft(
        self,
        draft_entry: F8ComponentEntry,
        *,
        preferred_visibility: F8ComponentVisibility | None = None,
    ) -> F8ComponentEntry | None:
        visibility = preferred_visibility or self._choose_visibility()
        if visibility is None:
            return None
        publish_asset_id = new_asset_id()
        upload_record = validate_as(
            F8ComponentRecord,
            {
                **dump_json(draft_entry.record, mode="json"),
                "componentId": publish_asset_id,
            },
        )
        source = (
            F8ComponentSourceKind.remote_private
            if visibility == F8ComponentVisibility.private
            else F8ComponentSourceKind.remote_public
        )
        try:
            published = self._sync_client.create_component(
                F8ComponentEntry(
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
        _ = self._draft_service_for_catalog().create_draft_from_record(
            draft_entry.record,
            origin_kind=draft_entry.draftOriginKind or F8ComponentDraftOriginKind.new,
            publish_target_asset_id=publish_asset_id,
            publish_base_remote_revision=published.remoteRevision,
            draft_id=str(draft_entry.record.componentId),
        )
        self._rebuild_browser_after_draft_changed(
            preserve_component_id=str(draft_entry.record.componentId)
        )
        return published

    def _on_list_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is not None:
            self._list.setCurrentItem(item)
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        menu = self._build_list_context_menu(
            current_tab=current_tab,
            selected_entry=selected_entry,
            local_entry=local_entry,
            remote_entry=remote_entry,
        )
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _build_list_context_menu(
        self,
        *,
        current_tab: int,
        selected_entry: F8ComponentEntry,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> QtWidgets.QMenu:
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
            duplicate_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
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
            open_draft_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
            if can_load:
                load_action = menu.addAction("Load")
                load_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete")
            delete_action.setEnabled(
                remote_entry is not None and self._is_owned_remote_entry(remote_entry)
            )
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
            visibility_label = "Make Public"
            if remote_entry is not None and remote_entry.visibility == F8ComponentVisibility.public:
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
                selected_entry.source == F8ComponentSourceKind.remote_public
                and not self._is_owned_remote_entry(selected_entry)
            )
            subscribe_action.triggered.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        else:
            if local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)):
                offload_action = menu.addAction("Remove from Installed")
                offload_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            pull_action = menu.addAction("Pull")
            pull_action.setEnabled(remote_entry is not None and component_entry_is_installed(remote_entry))
            pull_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        return menu

    def _component_overwrite_choices(self, *, exclude_component_id: str | None = None) -> list[AssetOverwriteChoice]:
        choices = [
            AssetOverwriteChoice(
                asset_id=str(entry.record.componentId),
                label=str(entry.record.name),
                description=str(entry.record.description),
                tags=[str(tag) for tag in list(entry.record.tags or []) if str(tag).strip()],
            )
            for entry in self._draft_service_for_catalog().list_catalog_entries()
            if exclude_component_id is None or str(entry.record.componentId) != exclude_component_id
        ]
        choices.sort(key=lambda choice: choice.label.lower())
        return choices

    def _normalize_component_name(self, name: str) -> str:
        return str(name or "").strip()

    def _draft_entry_by_name(self, name: str, *, exclude_component_id: str | None = None) -> F8ComponentEntry | None:
        normalized_name = self._normalize_component_name(name)
        excluded_id = str(exclude_component_id or "").strip()
        if not normalized_name:
            return None
        for entry in self._draft_service_for_catalog().list_catalog_entries():
            component_id = str(entry.record.componentId or "").strip()
            if excluded_id and component_id == excluded_id:
                continue
            if self._normalize_component_name(entry.record.name) == normalized_name:
                return entry
        return None

    def _validate_edit_component_name(self, candidate: str, component_id: str) -> str | None:
        normalized_name = self._normalize_component_name(candidate)
        if self._draft_entry_by_name(normalized_name, exclude_component_id=component_id) is not None:
            return f"Component name '{normalized_name}' already exists. Please rename."
        return None

    def _validate_save_component_name(self, candidate: str, overwrite_component_id: str | None) -> str | None:
        normalized_name = self._normalize_component_name(candidate)
        overwrite_entry = None if not overwrite_component_id else self._local_entry_for_component_id(str(overwrite_component_id))
        exclude_id = None if overwrite_entry is None else str(overwrite_entry.record.componentId)
        if self._draft_entry_by_name(name=normalized_name, exclude_component_id=exclude_id) is not None:
            return f"Component name '{normalized_name}' already exists. Please choose the existing component to overwrite."
        return None

    def _on_add_clicked(self) -> None:
        graph = self._graph
        if graph is None:
            return
        metadata_dialog = AssetOverwriteMetaDialog(
            parent=self,
            title="Save As Component",
            name="Untitled Component",
            description="",
            tags=[],
            overwrite_choices=self._component_overwrite_choices(),
            overwrite_label="Overwrite Existing Component",
            name_validator=self._validate_save_component_name,
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            selected_nodes = list(graph.selected_nodes() or [])
            selected_node_ids = collect_component_selected_node_ids(selected_nodes)
            if selected_nodes and not selected_node_ids:
                show_warning(self, "Save component failed", "Selected nodes are missing stable ids.")
                return
            payload = graph.serialize_publish_session()
            if selected_node_ids:
                payload = trim_component_publish_payload_to_selected_nodes(
                    payload=payload,
                    selected_node_ids=selected_node_ids,
                )
            name, description, tags, overwrite_component_id = metadata_dialog.values()
            overwrite_entry = None if not overwrite_component_id else self._local_entry_for_component_id(str(overwrite_component_id))
            if overwrite_entry is None:
                overwrite_entry = self._draft_entry_by_name(name)
            record = F8ComponentRecord(
                componentId=new_asset_id() if overwrite_entry is None else str(overwrite_entry.record.componentId),
                name=name,
                description=description,
                tags=tags,
                content=payload,
            )
            upsert_component(record)
        except Exception as exc:
            logger.exception("Component catalog save component failed")
            show_warning(self, "Save component failed", f"Failed to save component.\n\n{exc}")
            return
        action_text = "Updated" if overwrite_entry is not None else "Saved"
        show_info(self, action_text, f"{action_text} component:\n{record.name}")

    def _on_edit_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        if current_tab != self._TAB_DRAFTS:
            draft_entry = self._ensure_component_draft_for_entry(selected_entry)
            if draft_entry is None:
                return
            self._scope_tabs.setCurrentIndex(self._TAB_DRAFTS)
            self._rebuild_browser_after_draft_changed(
                preserve_component_id=str(draft_entry.record.componentId)
            )
            show_info(self, "Draft Ready", f"Opened draft for:\n{draft_entry.record.name}")
            return
        component_id = str(selected_entry.record.componentId or "").strip()
        local_entry = self._local_entry_for_component_id(component_id)
        if local_entry is None:
            return
        record = local_entry.record
        metadata_dialog = AssetOverwriteMetaDialog(
            parent=self,
            title="Edit Draft Metadata",
            name=record.name,
            description=record.description,
            tags=list(record.tags or []),
            overwrite_choices=[],
            overwrite_label="Load Metadata From",
            name_validator=lambda candidate, _selected_id: self._validate_edit_component_name(
                candidate, component_id
            ),
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags, _overwrite_component_id = metadata_dialog.values()
        from f8pysdk.codec import copy_model
        updated_record = validate_as(
            F8ComponentRecord,
            {
                **dump_json(record, mode="json"),
                "name": name,
                "description": description,
                "tags": tags,
                "updatedAt": component_now_iso(),
            },
        )
        try:
            _ = self._draft_service_for_catalog().create_draft_from_record(
                updated_record,
                origin_kind=local_entry.draftOriginKind,
                publish_target_asset_id=local_entry.draftOriginAssetId,
                publish_base_remote_revision=local_entry.draftOriginRevision,
                draft_id=component_id,
            )
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return
        self._rebuild_browser_after_draft_changed(preserve_component_id=component_id)

    def _on_delete_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_DRAFTS:
            if local_entry is None:
                return
            answer = QtWidgets.QMessageBox.question(self, "Delete draft", f"Delete draft '{selected_entry.record.name}'?")
            if answer != QtWidgets.QMessageBox.Yes:
                return
            if not self._draft_service_for_catalog().delete_draft(str(local_entry.record.componentId)):
                show_warning(self, "Delete failed", "Draft was not found.")
                return
            self._rebuild_browser_after_draft_changed()
            return
        if current_tab == self._TAB_INSTALLED:
            can_remove_installed = local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry))
            if not can_remove_installed:
                return
            answer = QtWidgets.QMessageBox.question(
                self,
                "Remove from Installed",
                f"Remove installed cache for '{selected_entry.record.name}'?",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            if self._offload_selected_component(local_entry=local_entry, remote_entry=remote_entry):
                show_info(self, "Removed from Installed", f"Removed from installed cache:\n{selected_entry.record.name}")
            return
        has_owned_remote = remote_entry is not None and self._is_owned_remote_entry(remote_entry)
        if not has_owned_remote:
            return
        title = "Delete component"
        prompt = f"Delete remote component '{selected_entry.record.name}'?"
        answer = QtWidgets.QMessageBox.question(self, title, prompt)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            if has_owned_remote and remote_entry is not None:
                self._sync_client.delete_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Delete failed", str(exc))
            return
        self._rebuild_browser_after_remote_asset_changed()

    def _on_copy_local_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        copied = self._ensure_component_draft_for_entry(
            selected_entry,
            always_duplicate=current_tab != self._TAB_MINE,
        )
        if copied is None:
            return
        self._scope_tabs.setCurrentIndex(self._TAB_DRAFTS)
        self._rebuild_browser_after_draft_changed(
            preserve_component_id=str(copied.record.componentId)
        )
        if current_tab == self._TAB_MINE:
            show_info(self, "Draft Ready", f"Opened draft for:\n{copied.record.name}")
        else:
            show_info(self, "Draft Created", f"Created local draft:\n{copied.record.name}")

    def _on_upload_clicked(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_DRAFTS:
            draft_entry = self._selected_local_entry()
            if draft_entry is None:
                return
            published = self._publish_component_draft(draft_entry)
            if published is not None:
                show_info(self, "Published", f"Published draft:\n{published.record.name}")
            return
        if current_tab == self._TAB_INSTALLED:
            pulled = self._pull_selected_component()
            if pulled is not None:
                show_info(self, "Pulled", f"Pulled component:\n{pulled.record.name}")
            return
        return

    def _on_install_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        if self._is_local_draft_entry(local_entry):
            return
        if local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)):
            offloaded_name = str(selected_entry.record.name or "")
            if self._offload_selected_component(local_entry=local_entry, remote_entry=remote_entry):
                show_info(self, "Removed from Installed", f"Removed from installed cache:\n{offloaded_name}")
            return
        if remote_entry is None:
            return
        try:
            installed = self._sync_client.hydrate_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        show_info(self, "Loaded", f"Loaded component:\n{installed.record.name}")
        self._rebuild_browser_after_installed_state_changed(
            preserve_component_id=str(installed.record.componentId)
        )

    def _on_subscribe_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if not self._ensure_logged_in():
            return
        try:
            if selected_entry.subscribed:
                updated = self._sync_client.unsubscribe_component(str(selected_entry.record.componentId))
                show_info(self, "Unsubscribed", f"Removed subscription:\n{updated.record.name}")
            else:
                updated = self._sync_client.subscribe_component(str(selected_entry.record.componentId))
                show_info(self, "Subscribed", f"Subscribed to component:\n{updated.record.name}")
        except Exception as exc:
            show_warning(self, "Subscription failed", str(exc))
            return
        self._rebuild_browser_after_remote_scope_state_changed(
            preserve_component_id=str(updated.record.componentId)
        )

    def _on_history_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if self._scope_tabs.currentIndex() == self._TAB_DRAFTS:
            if not selected_entry.draftOriginAssetId:
                show_info(self, "Draft History", "Local drafts do not keep local version history.")
                return
            remote_entry = self._remote_entry_for_component_id(str(selected_entry.draftOriginAssetId))
            if remote_entry is None:
                show_warning(self, "History failed", "Linked cloud asset is not available.")
                return
            self._show_remote_history(remote_entry)
            return
        if selected_entry.source == F8ComponentSourceKind.local:
            self._show_local_history(selected_entry)
            return
        self._show_remote_history(selected_entry)

    def _ensure_component_draft_for_entry(
        self,
        entry: F8ComponentEntry,
        *,
        always_duplicate: bool = False,
    ) -> F8ComponentEntry | None:
        if entry.source == F8ComponentSourceKind.local and entry.isLocalDraft and not always_duplicate:
            return entry
        hydrated_entry = self._ensure_component_hydrated(entry, operation_name="Load component")
        if hydrated_entry is None:
            return None
        publish_target_asset_id: str | None = None
        publish_base_remote_revision: str | None = None
        origin_kind = F8ComponentDraftOriginKind.copy_remote
        if entry.source == F8ComponentSourceKind.local and entry.isLocalDraft:
            origin_kind = F8ComponentDraftOriginKind.copy_local
        elif self._is_owned_remote_entry(hydrated_entry):
            publish_target_asset_id = str(hydrated_entry.record.componentId)
            publish_base_remote_revision = hydrated_entry.remoteRevision
            if not always_duplicate:
                existing_draft = self._draft_service_for_catalog().draft_for_publish_target(publish_target_asset_id)
                if existing_draft is not None:
                    return self._local_entry_for_component_id(existing_draft.draftId)
        draft_record = validate_as(
            F8ComponentRecord,
            {
                **dump_json(hydrated_entry.record, mode="json"),
                "componentId": new_asset_id() if always_duplicate or publish_target_asset_id else new_asset_id(),
                "updatedAt": component_now_iso(),
            },
        )
        saved = self._draft_service_for_catalog().create_draft_from_record(
            draft_record,
            origin_kind=origin_kind,
            publish_target_asset_id=publish_target_asset_id,
            publish_base_remote_revision=publish_base_remote_revision,
            draft_id=str(draft_record.componentId),
        )
        return self._local_entry_for_component_id(saved.draftId)

    def _publish_component_draft(self, draft_entry: F8ComponentEntry) -> F8ComponentEntry | None:
        target_asset_id = None if draft_entry.draftOriginAssetId is None else str(draft_entry.draftOriginAssetId).strip() or None
        if target_asset_id:
            remote_entry = self._remote_entry_for_component_id(target_asset_id)
            if remote_entry is None:
                try:
                    remote_entry = self._sync_client.get_component(target_asset_id)
                except F8ComponentRemoteRequestError as exc:
                    if self._is_missing_component_request_error(exc) and self._confirm_create_replacement_component(
                        draft_entry=draft_entry,
                        missing_component_id=target_asset_id,
                    ):
                        return self._create_remote_component_for_draft(draft_entry)
                    show_warning(self, "Publish failed", str(exc))
                    return None
                except Exception as exc:
                    show_warning(self, "Publish failed", str(exc))
                    return None
            elif not component_entry_has_cached_content(remote_entry):
                try:
                    remote_entry = self._sync_client.hydrate_component(target_asset_id)
                except F8ComponentRemoteRequestError as exc:
                    if self._is_missing_component_request_error(exc) and self._confirm_create_replacement_component(
                        draft_entry=draft_entry,
                        missing_component_id=target_asset_id,
                    ):
                        return self._create_remote_component_for_draft(
                            draft_entry,
                            preferred_visibility=remote_entry.visibility,
                        )
                    show_warning(self, "Publish failed", str(exc))
                    return None
                except Exception as exc:
                    show_warning(self, "Publish failed", str(exc))
                    return None
            content_changed = json.dumps(remote_entry.record.content, sort_keys=True, default=str) != json.dumps(
                draft_entry.record.content,
                sort_keys=True,
                default=str,
            )
            schema_changed = str(remote_entry.record.schemaVersion) != str(draft_entry.record.schemaVersion)
            name_changed = str(remote_entry.record.name) != str(draft_entry.record.name)
            description_changed = str(remote_entry.record.description) != str(draft_entry.record.description)
            tags_changed = list(remote_entry.record.tags or []) != list(draft_entry.record.tags or [])
            try:
                if not content_changed and not schema_changed and (name_changed or description_changed or tags_changed):
                    published = self._sync_client.patch_component_meta(
                        target_asset_id,
                        name=str(draft_entry.record.name),
                        description=str(draft_entry.record.description),
                        tags=[str(tag) for tag in list(draft_entry.record.tags or [])],
                    )
                else:
                    upload_record = validate_as(
                        F8ComponentRecord,
                        {
                            **dump_json(draft_entry.record, mode="json"),
                            "componentId": target_asset_id,
                        },
                    )
                    published = self._sync_client.update_component(
                        F8ComponentEntry(
                            record=upload_record,
                            source=remote_entry.source,
                            visibility=remote_entry.visibility,
                            remoteRevision=remote_entry.remoteRevision,
                            installed=True,
                            hasCachedContent=True,
                        )
                    )
            except F8ComponentRemoteRequestError as exc:
                if self._is_missing_component_request_error(exc) and self._confirm_create_replacement_component(
                    draft_entry=draft_entry,
                    missing_component_id=target_asset_id,
                ):
                    return self._create_remote_component_for_draft(
                        draft_entry,
                        preferred_visibility=remote_entry.visibility,
                    )
                show_warning(self, "Publish failed", str(exc))
                return None
            except Exception as exc:
                show_warning(self, "Publish failed", str(exc))
                return None
            _ = self._draft_service_for_catalog().create_draft_from_record(
                draft_entry.record,
                origin_kind=draft_entry.draftOriginKind,
                publish_target_asset_id=target_asset_id,
                publish_base_remote_revision=published.remoteRevision,
                draft_id=str(draft_entry.record.componentId),
            )
            self._rebuild_browser_after_draft_changed(
                preserve_component_id=str(draft_entry.record.componentId)
            )
            return published
        return self._create_remote_component_for_draft(draft_entry)

    def _on_visibility_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or not self._is_owned_remote_entry(selected_entry):
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        next_visibility = F8ComponentVisibility.public
        prompt = "Make this remote component public?"
        if selected_entry.visibility == F8ComponentVisibility.public:
            next_visibility = F8ComponentVisibility.private
            prompt = "Make this remote component private?"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Change visibility",
            prompt,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            self._sync_client.update_component_visibility(
                str(selected_entry.record.componentId),
                visibility=next_visibility,
                revision=selected_entry.remoteRevision,
            )
        except Exception as exc:
            show_warning(self, "Visibility update failed", str(exc))
            return
        self._rebuild_browser_after_remote_asset_changed(
            preserve_component_id=str(selected_entry.record.componentId)
        )

    def _on_insert_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        graph = self._graph
        if graph is None:
            return
        try:
            request = graph.prepare_insert_graph_from_component(
                selected_entry.record.content,
                component_name=selected_entry.record.name,
            )
        except Exception as exc:
            show_warning(self, "Create on canvas failed", str(exc))
            return
        graph.begin_graph_placement(
            request,
            label=f"Component: {selected_entry.record.name}\n{request.node_count} nodes",
        )

    def _on_item_double_clicked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self._on_insert_clicked()

    def _on_import_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Component JSON",
            "",
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        metadata_dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Import Component",
            name="Imported Component",
            description="",
            tags=[],
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = metadata_dialog.values()
        try:
            import_component_from_json(
                selected_path,
                metadata={
                    "componentId": new_asset_id(),
                    "name": name,
                    "description": description,
                    "tags": tags,
                },
            )
        except Exception as exc:
            show_warning(self, "Import failed", str(exc))

    def _on_export_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Component JSON",
            selected_entry.record.name,
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        try:
            out_path = export_component_to_json(selected_entry.record.componentId, selected_path)
        except Exception as exc:
            show_warning(self, "Export failed", str(exc))
            return
        show_info(self, "Exported", f"Saved:\n{out_path}")
