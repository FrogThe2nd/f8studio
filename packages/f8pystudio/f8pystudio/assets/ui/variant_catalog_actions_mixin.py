from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from f8pysdk.codec import copy_model, dump_json, validate_as
from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, F8VariantRecord

from ..common import new_asset_id
from ..common.asset_file_exchange import read_variant_asset_file
from ..variants.variant_catalog import variant_entry_is_installed
from ..variants.variant_compose import build_variant_record_from_node
from ..variants.variant_ids import build_variant_node_type
from ..variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantSourceKind,
    variant_now_iso,
)
from ..variants.variant_repository import (
    ensure_unique_variant_name,
    export_to_json,
    import_from_json,
    normalize_variant_name,
)
from ...ui.support.ui_notifications import show_info, show_warning
from .project_asset_dialogs import AssetOverwriteChoice, AssetOverwriteMetaDialog


class VariantCatalogActionsMixin:
    def _save_variant_draft(
        self,
        *,
        record: F8VariantRecord,
        origin_kind: F8VariantDraftOriginKind | None,
        publish_target_asset_id: str | None,
        publish_base_remote_version_number: int | None,
        draft_id: str | None = None,
    ) -> F8VariantEntry:
        draft_service = self._draft_service_for_catalog()
        draft_identifier = str(draft_id or record.variantId or "").strip() or new_asset_id()
        normalized_name = ensure_unique_variant_name(
            str(record.baseNodeType or ""),
            str(record.name or ""),
            exclude_variant_id=draft_identifier,
            existing_records=[
                entry.record
                for entry in draft_service.list_catalog_entries()
                if str(entry.record.baseNodeType or "").strip() == str(record.baseNodeType or "").strip()
            ],
        )
        saved = draft_service.create_draft_from_record(
            copy_model(record, update={"name": normalized_name}),
            origin_kind=origin_kind,
            publish_target_asset_id=publish_target_asset_id,
            publish_base_remote_version_number=publish_base_remote_version_number,
            draft_id=draft_identifier,
        )
        saved_entry = self._local_entry_for_variant_id(saved.draftId)
        if saved_entry is None:
            raise ValueError("Failed to save variant draft.")
        return saved_entry

    def _find_selected_base_node(self) -> Any | None:
        graph = self._graph
        if graph is None:
            return None
        current_base_type = self._get_current_base_node_type()
        for node in list(graph.selected_nodes() or []):
            node_type = str(node.type_ or "").strip()
            if not current_base_type or node_type == current_base_type:
                return node
        return None

    def _on_add_clicked(self) -> None:
        node = self._find_selected_base_node()
        if node is None:
            current_base_type = self._get_current_base_node_type()
            message = (
                f"Please select a node of type:\n{current_base_type}\nthen try again."
                if current_base_type
                else "Please select a node in the graph, then try again."
            )
            show_info(self, "No matching selected node", message)
            return
        spec = node.spec
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            show_warning(self, "Unsupported node", "Selected node has no typed spec.")
            return
        node_display_name = ""
        try:
            node_display_name = str(node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            node_display_name = ""
        dlg = AssetOverwriteMetaDialog(
            parent=self,
            title="Save Variant",
            name=str(node_display_name or node.NODE_NAME or spec.label or self._base_node_name),
            description=str(spec.description or ""),
            tags=[str(tag) for tag in list(spec.tags or [])],
            overwrite_choices=self._overwrite_choices_for_base(),
            overwrite_label="Overwrite Existing Variant",
            name_validator=self._validate_save_variant_name,
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags, overwrite_variant_id = dlg.values()
        overwrite_entry = self._resolve_overwrite_target(name=name, overwrite_variant_id=overwrite_variant_id)
        record = build_variant_record_from_node(
            node=node,
            name=name,
            description=description,
            tags=tags,
            variant_id=(None if overwrite_entry is None else str(overwrite_entry.record.variantId)),
        )
        try:
            saved_entry = self._save_variant_record(record=record, overwrite_entry=overwrite_entry)
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return
        action_text = "Updated" if overwrite_entry is not None else "Saved"
        show_info(self, action_text, f"{action_text} variant:\n{saved_entry.record.name}")

    def _on_edit_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if self._scope_tabs.currentIndex() != self._TAB_DRAFTS:
            draft_entry = self._ensure_variant_draft_for_entry(selected_entry)
            if draft_entry is None:
                return
            self._scope_tabs.setCurrentIndex(self._TAB_DRAFTS)
            self._rebuild_browser_after_draft_changed(
                preserve_variant_id=str(draft_entry.record.variantId)
            )
            show_info(self, "Draft Ready", f"Opened draft for:\n{draft_entry.record.name}")
            return
        selected_entry = self._selected_local_entry()
        if selected_entry is None:
            return
        selected = selected_entry.record
        dlg = AssetOverwriteMetaDialog(
            parent=self,
            title="Edit Draft Metadata",
            name=selected.name,
            description=selected.description,
            tags=list(selected.tags or []),
            overwrite_choices=[],
            overwrite_label="Load Metadata From",
            name_validator=lambda candidate, _selected_id: self._validate_edit_variant_name(
                candidate, selected.variantId
            ),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags, _overwrite_variant_id = dlg.values()
        payload = dump_json(selected, mode="json")
        payload["name"] = name
        payload["description"] = description
        payload["tags"] = tags
        payload["updatedAt"] = variant_now_iso()
        new_record = validate_as(F8VariantRecord, payload)
        try:
            _ = self._save_variant_draft(
                record=new_record,
                origin_kind=selected_entry.draftOriginKind,
                publish_target_asset_id=selected_entry.draftOriginAssetId,
                publish_base_remote_version_number=selected_entry.draftOriginVersionNumber,
                draft_id=str(selected_entry.record.variantId),
            )
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return
        self._rebuild_browser_after_draft_changed(
            preserve_variant_id=str(selected.variantId)
        )

    def _overwrite_choices_for_base(self, *, exclude_variant_id: str | None = None) -> list[AssetOverwriteChoice]:
        choices = [
            AssetOverwriteChoice(
                asset_id=str(entry.record.variantId),
                label=str(entry.record.name),
                description=str(entry.record.description),
                tags=[str(tag) for tag in list(entry.record.tags or []) if str(tag).strip()],
            )
            for entry in self._draft_entries_for_base(exclude_variant_id=exclude_variant_id)
        ]
        choices.sort(key=lambda choice: choice.label.lower())
        return choices

    def _draft_entries_for_base(self, *, exclude_variant_id: str | None = None) -> list[F8VariantEntry]:
        excluded_variant_id = str(exclude_variant_id or "").strip()
        current_base_type = self._get_current_base_node_type()
        entries: list[F8VariantEntry] = []
        for entry in self._draft_service_for_catalog().list_catalog_entries():
            if str(entry.record.baseNodeType or "").strip() != current_base_type:
                continue
            variant_id = str(entry.record.variantId or "").strip()
            if excluded_variant_id and variant_id == excluded_variant_id:
                continue
            entries.append(entry)
        return entries

    def _draft_entry_by_name(self, name: str, *, exclude_variant_id: str | None = None) -> F8VariantEntry | None:
        normalized_name = normalize_variant_name(name)
        if not normalized_name:
            return None
        for entry in self._draft_entries_for_base(exclude_variant_id=exclude_variant_id):
            if normalize_variant_name(entry.record.name) == normalized_name:
                return entry
        return None

    def _resolve_overwrite_target(self, *, name: str, overwrite_variant_id: str | None) -> F8VariantEntry | None:
        normalized_name = normalize_variant_name(name)
        overwrite_entry = (
            None
            if overwrite_variant_id is None
            else self._local_entry_for_variant_id(str(overwrite_variant_id))
        )
        if overwrite_entry is not None and overwrite_entry.source == F8VariantSourceKind.local:
            return overwrite_entry
        return self._draft_entry_by_name(normalized_name)

    def _save_variant_record(
        self,
        *,
        record: F8VariantRecord,
        overwrite_entry: F8VariantEntry | None,
    ) -> F8VariantEntry:
        if overwrite_entry is None:
            return self._save_variant_draft(
                record=record,
                origin_kind=F8VariantDraftOriginKind.new,
                publish_target_asset_id=None,
                publish_base_remote_version_number=None,
            )
        if overwrite_entry.source == F8VariantSourceKind.local:
            return self._save_variant_draft(
                record=record,
                origin_kind=overwrite_entry.draftOriginKind,
                publish_target_asset_id=overwrite_entry.draftOriginAssetId,
                publish_base_remote_version_number=overwrite_entry.draftOriginVersionNumber,
                draft_id=str(overwrite_entry.record.variantId),
            )
        draft_entry = self._ensure_variant_draft_for_entry(overwrite_entry, record=record)
        if draft_entry is None:
            raise ValueError("Failed to create linked draft.")
        return draft_entry

    def _validate_save_variant_name(self, candidate: str, overwrite_variant_id: str | None) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        target_entry = self._resolve_overwrite_target(name=normalized_name, overwrite_variant_id=overwrite_variant_id)
        exclude_variant_id = None if target_entry is None else str(target_entry.record.variantId)
        if self._draft_entry_by_name(normalized_name, exclude_variant_id=exclude_variant_id) is not None:
            return f"Variant name '{normalized_name}' already exists. Please choose the existing variant to overwrite."
        return None

    def _validate_edit_variant_name(self, candidate: str, variant_id: str) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        if self._draft_entry_by_name(normalized_name, exclude_variant_id=variant_id) is not None:
            return f"Variant name '{normalized_name}' already exists. Please rename."
        return None

    def _on_delete_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_DRAFTS:
            if local_entry is None:
                return
            if QtWidgets.QMessageBox.question(self, "Delete draft", f"Delete draft '{selected_entry.record.name}'?") != QtWidgets.QMessageBox.Yes:
                return
            if not self._draft_service_for_catalog().delete_draft(str(local_entry.record.variantId)):
                show_warning(self, "Delete failed", "Draft was not found.")
                return
            self._rebuild_browser_after_draft_changed()
            return
        if current_tab == self._TAB_INSTALLED:
            can_remove_installed = local_entry is not None or (remote_entry is not None and variant_entry_is_installed(remote_entry))
            if not can_remove_installed:
                return
            if QtWidgets.QMessageBox.question(
                self,
                "Remove from Installed",
                f"Remove installed cache for '{selected_entry.record.name}'?",
            ) != QtWidgets.QMessageBox.Yes:
                return
            if self._offload_selected_variant(local_entry=local_entry, remote_entry=remote_entry):
                show_info(
                    self,
                    "Removed from Installed",
                    f"Removed from installed cache:\n{selected_entry.record.name}",
                )
            return
        has_owned_remote = remote_entry is not None and self._is_owned_remote_entry(remote_entry)
        if not has_owned_remote:
            return
        title = "Delete variant"
        prompt = f"Delete remote variant '{selected_entry.record.name}'?"
        if QtWidgets.QMessageBox.question(self, title, prompt) != QtWidgets.QMessageBox.Yes:
            return
        try:
            if has_owned_remote and remote_entry is not None:
                self._sync_client.delete_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Delete failed", str(exc))
            return
        self._rebuild_browser_after_remote_asset_changed()

    def _on_delete_local_clicked(self) -> None:
        self._on_delete_clicked()

    def _on_delete_remote_clicked(self) -> None:
        self._on_delete_clicked()

    def _duplicate_selected_variant_as_local(self) -> F8VariantEntry | None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return None
        source_entry = selected_entry
        if source_entry.source != F8VariantSourceKind.local and not source_entry.hasCachedContent:
            try:
                source_entry = self._sync_client.cache_variant_content(str(source_entry.record.variantId))
            except Exception as exc:
                show_warning(self, "Load failed", str(exc))
                return None
        record = source_entry.record
        origin_kind = F8VariantDraftOriginKind.copy_remote
        publish_target_asset_id = None
        publish_base_remote_version_number = None
        if source_entry.source == F8VariantSourceKind.local and source_entry.isLocalDraft:
            origin_kind = F8VariantDraftOriginKind.copy_local
            publish_target_asset_id = source_entry.draftOriginAssetId
            publish_base_remote_version_number = source_entry.draftOriginVersionNumber
        duplicate_name = ensure_unique_variant_name(
            str(record.baseNodeType or ""),
            f"{str(record.name or '').strip() or 'Variant'} Draft",
            existing_records=[
                entry.record for entry in self._draft_service_for_catalog().list_catalog_entries()
            ],
        )
        duplicated_record = validate_as(
            F8VariantRecord,
            {
                **dump_json(record, mode="json"),
                "variantId": new_asset_id(),
                "name": duplicate_name,
                "updatedAt": variant_now_iso(),
            },
        )
        duplicated_entry = self._save_variant_draft(
            record=duplicated_record,
            origin_kind=origin_kind,
            publish_target_asset_id=publish_target_asset_id,
            publish_base_remote_version_number=publish_base_remote_version_number,
        )
        self._rebuild_browser_after_draft_changed(
            preserve_variant_id=str(duplicated_entry.record.variantId)
        )
        return self._local_entry_for_variant_id(str(duplicated_entry.record.variantId))

    def _on_duplicate_clicked(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_MINE:
            selected_entry = self._selected_entry()
            if selected_entry is None:
                return
            draft_entry = self._ensure_variant_draft_for_entry(selected_entry)
            if draft_entry is None:
                return
            self._scope_tabs.setCurrentIndex(self._TAB_DRAFTS)
            self._rebuild_browser_after_draft_changed(
                preserve_variant_id=str(draft_entry.record.variantId)
            )
            show_info(self, "Draft Ready", f"Opened draft for:\n{draft_entry.record.name}")
            return
        duplicated = self._duplicate_selected_variant_as_local()
        if duplicated is None:
            return
        show_info(self, "Draft Created", f"Created local draft:\n{duplicated.record.name}")

    def _ensure_variant_draft_for_entry(
        self,
        entry: F8VariantEntry,
        *,
        record: F8VariantRecord | None = None,
        always_duplicate: bool = False,
    ) -> F8VariantEntry | None:
        if entry.source == F8VariantSourceKind.local and entry.isLocalDraft and record is None and not always_duplicate:
            return entry
        hydrated_entry = entry
        if hydrated_entry.source != F8VariantSourceKind.local and record is None and not hydrated_entry.hasCachedContent:
            try:
                hydrated_entry = self._sync_client.cache_variant_content(str(hydrated_entry.record.variantId))
            except Exception as exc:
                show_warning(self, "Load failed", str(exc))
                return None
        publish_target_asset_id: str | None = None
        publish_base_remote_version_number: int | None = None
        origin_kind = F8VariantDraftOriginKind.copy_remote
        if entry.source == F8VariantSourceKind.local and entry.isLocalDraft:
            origin_kind = F8VariantDraftOriginKind.copy_local
        elif self._is_owned_remote_entry(entry):
            publish_target_asset_id = str(entry.record.variantId)
            publish_base_remote_version_number = entry.remoteVersionNumber
            if not always_duplicate:
                existing_draft = self._draft_service_for_catalog().draft_for_publish_target(publish_target_asset_id)
                if existing_draft is not None:
                    return self._local_entry_for_variant_id(existing_draft.draftId)
        draft_record = validate_as(
            F8VariantRecord,
            {
                **dump_json(hydrated_entry.record if record is None else record, mode="json"),
                "variantId": new_asset_id(),
                "updatedAt": variant_now_iso(),
            },
        )
        saved = self._save_variant_draft(
            record=draft_record,
            origin_kind=origin_kind,
            publish_target_asset_id=publish_target_asset_id,
            publish_base_remote_version_number=publish_base_remote_version_number,
        )
        return self._local_entry_for_variant_id(str(saved.record.variantId))

    def _on_create_clicked(self) -> None:
        selected_entry = self._selected_local_entry() or self._selected_entry()
        if selected_entry is None:
            return
        if selected_entry.source != F8VariantSourceKind.local and not variant_entry_is_installed(selected_entry):
            try:
                selected_entry = self._sync_client.hydrate_variant(str(selected_entry.record.variantId))
            except Exception as exc:
                show_warning(self, "Load failed", str(exc))
                return
        graph = self._graph
        if graph is None:
            return
        selected = selected_entry.record
        graph.begin_node_placement(
            build_variant_node_type(str(selected.variantId)),
            f"{self._base_node_name}\n - {selected.name}",
        )

    def _on_import_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Variant Asset JSON",
            "",
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        try:
            payload = read_variant_asset_file(selected_path)
            current_base_type = str(self._get_current_base_node_type() or "").strip()
            imported_base_type = str(payload.record.baseNodeType or "").strip()
            if current_base_type and imported_base_type != current_base_type:
                raise ValueError(
                    f"Variant base node type mismatch: expected {current_base_type}, got {imported_base_type}."
                )
            imported = import_from_json(selected_path)
        except Exception as exc:
            show_warning(self, "Import failed", str(exc))
            return
        self._rebuild_browser_after_draft_changed(
            preserve_variant_id=str(imported.variantId)
        )
        show_info(self, "Imported", f"Imported variant:\n{imported.name}")

    def _on_export_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if selected_entry.source != F8VariantSourceKind.local and not variant_entry_is_installed(selected_entry):
            try:
                selected_entry = self._sync_client.hydrate_variant(str(selected_entry.record.variantId))
            except Exception as exc:
                show_warning(self, "Export failed", str(exc))
                return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Variant Asset JSON",
            selected_entry.record.name,
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        try:
            out = export_to_json(str(selected_entry.record.variantId), selected_path)
        except Exception as exc:
            show_warning(self, "Export failed", str(exc))
            return
        show_info(self, "Exported", f"Saved:\n{out}")
