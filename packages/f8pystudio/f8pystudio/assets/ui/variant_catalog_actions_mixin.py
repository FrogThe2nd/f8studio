from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from f8pysdk.codec import copy_model, dump_json, validate_as
from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, F8VariantRecord

from ..common import new_asset_id
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
    upsert_variant_entry,
)
from ...ui.support.ui_notifications import show_info, show_warning
from .project_asset_dialogs import AssetOverwriteChoice, AssetOverwriteMetaDialog


class VariantCatalogActionsMixin:
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
        selected_entry = self._selected_local_entry()
        if selected_entry is None:
            return
        selected = selected_entry.record
        dlg = AssetOverwriteMetaDialog(
            parent=self,
            title="Edit Variant Metadata",
            name=selected.name,
            description=selected.description,
            tags=list(selected.tags or []),
            overwrite_choices=self._overwrite_choices_for_base(exclude_variant_id=str(selected.variantId)),
            overwrite_label="Load Metadata From",
            name_validator=lambda candidate, _selected_id: self._validate_edit_variant_name(candidate, selected.variantId),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags, _overwrite_variant_id = dlg.values()
        payload = dump_json(selected, mode="json")
        payload["name"] = name
        payload["description"] = description
        payload["tags"] = tags
        payload["updatedAt"] = variant_now_iso()
        try:
            _ = upsert_variant_entry(
                copy_model(
                    selected_entry,
                    update={
                        "record": validate_as(F8VariantRecord, payload),
                        "remoteVersionNumber": selected_entry.remoteVersionNumber,
                    },
                )
            )
        except ValueError as exc:
            show_warning(self, "Invalid name", str(exc))
            return

    def _overwrite_choices_for_base(self, *, exclude_variant_id: str | None = None) -> list[AssetOverwriteChoice]:
        choices = [
            AssetOverwriteChoice(
                asset_id=str(entry.record.variantId),
                label=str(entry.record.name),
                description=str(entry.record.description),
                tags=[str(tag) for tag in list(entry.record.tags or []) if str(tag).strip()],
            )
            for entry in self._mine_entries_for_base(exclude_variant_id=exclude_variant_id)
        ]
        choices.sort(key=lambda choice: choice.label.lower())
        return choices

    def _resolve_overwrite_target(self, *, name: str, overwrite_variant_id: str | None) -> F8VariantEntry | None:
        normalized_name = normalize_variant_name(name)
        overwrite_entry = (
            None
            if overwrite_variant_id is None
            else self._sync_client._catalog_service.entry(str(overwrite_variant_id), include_uninstalled=True)
        )
        if overwrite_entry is not None and self._is_mine_entry(overwrite_entry):
            return overwrite_entry
        return self._mine_entry_by_name(normalized_name)

    def _save_variant_record(
        self,
        *,
        record: F8VariantRecord,
        overwrite_entry: F8VariantEntry | None,
    ) -> F8VariantEntry:
        if overwrite_entry is None:
            return self._sync_client._catalog_service.upsert_local_entry(
                F8VariantEntry(
                    record=record,
                    source=F8VariantSourceKind.local,
                    isLocalDraft=True,
                    draftOriginKind=F8VariantDraftOriginKind.new,
                )
            )
        if overwrite_entry.source == F8VariantSourceKind.local:
            return self._sync_client._catalog_service.upsert_local_entry(
                copy_model(overwrite_entry, update={"record": record})
            )
        local_entry = self._local_entry_for_variant_id(str(overwrite_entry.record.variantId))
        if local_entry is not None:
            return self._sync_client._catalog_service.upsert_local_entry(
                copy_model(local_entry, update={"record": record})
            )
        seeded_entry = self._local_seed_from_remote_entry(
            overwrite_entry,
            record=record,
            mark_modified=True,
        )
        return self._replace_local_variant_head(seeded_entry)

    def _validate_save_variant_name(self, candidate: str, overwrite_variant_id: str | None) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        target_entry = self._resolve_overwrite_target(name=normalized_name, overwrite_variant_id=overwrite_variant_id)
        exclude_variant_id = None if target_entry is None else str(target_entry.record.variantId)
        if self._mine_entry_by_name(normalized_name, exclude_variant_id=exclude_variant_id) is not None:
            return f"Variant name '{normalized_name}' already exists. Please choose the existing variant to overwrite."
        return None

    def _validate_edit_variant_name(self, candidate: str, variant_id: str) -> str | None:
        normalized_name = normalize_variant_name(candidate)
        if self._mine_entry_by_name(normalized_name, exclude_variant_id=variant_id) is not None:
            return f"Variant name '{normalized_name}' already exists. Please rename."
        return None

    def _on_delete_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        has_owned_remote = remote_entry is not None and self._is_owned_remote_entry(remote_entry)
        if local_entry is None and not has_owned_remote:
            return
        title = "Delete variant"
        prompt = f"Delete variant '{selected_entry.record.name}'?"
        if local_entry is not None and has_owned_remote:
            prompt = f"Delete local and remote variant '{selected_entry.record.name}'?"
        elif has_owned_remote:
            prompt = f"Delete remote variant '{selected_entry.record.name}'?"
        elif local_entry is not None:
            prompt = f"Delete local variant '{selected_entry.record.name}'?"
        if QtWidgets.QMessageBox.question(self, title, prompt) != QtWidgets.QMessageBox.Yes:
            return
        try:
            if local_entry is not None:
                _ = self._sync_client._catalog_service.delete_local_entry(str(local_entry.record.variantId))
            if has_owned_remote and remote_entry is not None:
                self._sync_client.delete_variant(str(remote_entry.record.variantId))
        except Exception as exc:
            show_warning(self, "Delete failed", str(exc))
            return
        self._reload()

    def _on_delete_local_clicked(self) -> None:
        self._on_delete_clicked()

    def _on_delete_remote_clicked(self) -> None:
        self._on_delete_clicked()

    def _duplicate_selected_variant_as_local(self) -> F8VariantEntry | None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return None
        if selected_entry.source != F8VariantSourceKind.local:
            try:
                selected_entry = self._sync_client.cache_variant_content(str(selected_entry.record.variantId))
            except Exception as exc:
                show_warning(self, "Load failed", str(exc))
                return None
        record = selected_entry.record
        duplicate_name = ensure_unique_variant_name(
            str(record.baseNodeType or ""),
            f"{str(record.name or '').strip() or 'Variant'} Draft",
            existing_records=[entry.record for entry in self._sync_client._catalog_service._local_provider.load_entries()],
        )
        duplicate_record = validate_as(
            F8VariantRecord,
            {
                **dump_json(record, mode="json"),
                "variantId": new_asset_id(),
                "name": duplicate_name,
                "updatedAt": variant_now_iso(),
            },
        )
        try:
            duplicated_entry = self._sync_client._catalog_service.upsert_local_entry(
                F8VariantEntry(
                    record=duplicate_record,
                    source=F8VariantSourceKind.local,
                    isLocalDraft=True,
                    draftOriginKind=(
                        F8VariantDraftOriginKind.copy_local
                        if selected_entry.source == F8VariantSourceKind.local
                        else F8VariantDraftOriginKind.copy_remote
                    ),
                    draftOriginAssetId=str(record.variantId),
                    draftOriginRevision=selected_entry.remoteRevision,
                )
            )
        except ValueError as exc:
            show_warning(self, "Copy to Draft failed", str(exc))
            return None
        self._reload(preserve_variant_id=str(duplicated_entry.record.variantId))
        return self._local_entry_for_variant_id(str(duplicated_entry.record.variantId))

    def _on_duplicate_clicked(self) -> None:
        duplicated = self._duplicate_selected_variant_as_local()
        if duplicated is None:
            return
        show_info(self, "Draft Created", f"Created local draft:\n{duplicated.record.name}")

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
            "Import Variant Library JSON",
            "",
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        mode = QtWidgets.QMessageBox.question(
            self,
            "Import mode",
            "Merge into existing local library?\n\nYes = Merge\nNo = Replace",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
        )
        if mode == QtWidgets.QMessageBox.Cancel:
            return
        try:
            import_from_json(selected_path, mode="merge" if mode == QtWidgets.QMessageBox.Yes else "replace")
        except Exception as exc:
            show_warning(self, "Import failed", str(exc))
            return

    def _on_export_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Variant Library JSON",
            "nodeVariants.json",
            "JSON (*.json);;All Files (*)",
        )
        selected_path = str(path or "").strip()
        if not selected_path:
            return
        try:
            out = export_to_json(selected_path)
        except Exception as exc:
            show_warning(self, "Export failed", str(exc))
            return
        show_info(self, "Exported", f"Saved:\n{out}")
