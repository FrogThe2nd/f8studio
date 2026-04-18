from __future__ import annotations

import logging

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json, validate_as

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
from .project_asset_dialogs import ProjectAssetMetaDialog


logger = logging.getLogger(__name__)


class ComponentCatalogActionsMixin:
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
        if current_tab == self._TAB_MINE:
            can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
            load_action = menu.addAction("Offload" if can_offload else "Load")
            load_action.setEnabled(can_load or can_offload)
            load_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            delete_action = menu.addAction("Delete")
            delete_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            delete_action.triggered.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
            sync_action = menu.addAction("Sync")
            sync_action.setEnabled(local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)))
            sync_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            visibility_label = "Make Public"
            if remote_entry is not None and remote_entry.visibility == F8ComponentVisibility.public:
                visibility_label = "Make Private"
            visibility_action = menu.addAction(visibility_label)
            visibility_action.setEnabled(remote_entry is not None and self._is_owned_remote_entry(remote_entry))
            visibility_action.triggered.connect(self._on_visibility_clicked)  # type: ignore[attr-defined]
        elif current_tab == self._TAB_COMMUNITY:
            subscribe_action = menu.addAction("Unsubscribe" if selected_entry.subscribed else "Subscribe")
            subscribe_action.setEnabled(
                selected_entry.source == F8ComponentSourceKind.remote_public and not self._is_owned_remote_entry(selected_entry)
            )
            subscribe_action.triggered.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
            fork_action = menu.addAction("Copy to Draft")
            fork_action.triggered.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
        else:
            offload_action = menu.addAction("Offload")
            offload_action.setEnabled(local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)))
            offload_action.triggered.connect(self._on_install_clicked)  # type: ignore[attr-defined]
            pull_action = menu.addAction("Pull")
            pull_action.setEnabled(remote_entry is not None and component_entry_is_installed(remote_entry))
            pull_action.triggered.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
            history_action = menu.addAction("History")
            history_action.setEnabled(local_entry is not None or remote_entry is not None)
            history_action.triggered.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        if component_entry_is_installed(selected_entry):
            menu.addSeparator()
            insert_action = menu.addAction("Create on canvas")
            insert_action.triggered.connect(self._on_insert_clicked)  # type: ignore[attr-defined]
        return menu

    def _on_add_clicked(self) -> None:
        graph = self._graph
        if graph is None:
            return
        metadata_dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Save As Component",
            name="Untitled Component",
            description="",
            tags=[],
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
            name, description, tags = metadata_dialog.values()
            record = F8ComponentRecord(
                componentId=new_asset_id(),
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
        show_info(self, "Saved", f"Saved component:\n{record.name}")

    def _on_edit_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None or selected_entry.source != F8ComponentSourceKind.local:
            return
        record = selected_entry.record
        metadata_dialog = ProjectAssetMetaDialog(
            parent=self,
            title="Edit Component Metadata",
            name=record.name,
            description=record.description,
            tags=list(record.tags or []),
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, description, tags = metadata_dialog.values()
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
        upsert_component(updated_record)

    def _on_delete_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        has_owned_remote = remote_entry is not None and self._is_owned_remote_entry(remote_entry)
        if local_entry is None and not has_owned_remote:
            return
        title = "Delete component"
        prompt = f"Delete component '{selected_entry.record.name}'?"
        if local_entry is not None and has_owned_remote:
            prompt = f"Delete local and remote component '{selected_entry.record.name}'?"
        elif has_owned_remote:
            prompt = f"Delete remote component '{selected_entry.record.name}'?"
        elif local_entry is not None:
            prompt = f"Delete local component '{selected_entry.record.name}'?"
        answer = QtWidgets.QMessageBox.question(self, title, prompt)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            if local_entry is not None:
                _ = self._sync_client._catalog_service.delete_local_entry(str(local_entry.record.componentId))
            if has_owned_remote and remote_entry is not None:
                self._sync_client.delete_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Delete failed", str(exc))
            return
        self._reload()

    def _on_copy_local_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        selected_entry = self._ensure_component_hydrated(selected_entry, operation_name="Load component")
        if selected_entry is None:
            return
        copied = validate_as(
            F8ComponentRecord,
            {
                **dump_json(selected_entry.record, mode="json"),
                "componentId": new_asset_id(),
                "updatedAt": component_now_iso(),
            },
        )
        _ = self._sync_client._catalog_service.upsert_local_entry(
            F8ComponentEntry(
                record=copied,
                source=F8ComponentSourceKind.local,
                isLocalDraft=True,
                draftOriginKind=(
                    F8ComponentDraftOriginKind.copy_local
                    if selected_entry.source == F8ComponentSourceKind.local
                    else F8ComponentDraftOriginKind.copy_remote
                ),
                draftOriginAssetId=str(selected_entry.record.componentId),
                draftOriginRevision=selected_entry.remoteRevision,
            )
        )
        show_info(self, "Draft Created", f"Created local draft:\n{copied.name}")

    def _on_upload_clicked(self) -> None:
        current_tab = self._scope_tabs.currentIndex()
        if current_tab == self._TAB_INSTALLED:
            pulled = self._pull_selected_component()
            if pulled is not None:
                show_info(self, "Pulled", f"Pulled component:\n{pulled.record.name}")
            return
        synced = self._sync_selected_component()
        if synced is not None:
            show_info(self, "Synced", f"Synced component:\n{synced.record.name}")

    def _on_install_clicked(self) -> None:
        selected_entry, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None:
            return
        if self._is_local_draft_entry(local_entry):
            return
        if local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry)):
            offloaded_name = str(selected_entry.record.name or "")
            if self._offload_selected_component(local_entry=local_entry, remote_entry=remote_entry):
                show_info(self, "Offloaded", f"Offloaded component:\n{offloaded_name}")
            return
        if remote_entry is None:
            return
        try:
            installed = self._sync_client.hydrate_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        try:
            installed = self._ensure_owned_remote_component_has_local_head(installed)
        except Exception as exc:
            show_warning(self, "Load failed", str(exc))
            return
        show_info(self, "Loaded", f"Loaded component:\n{installed.record.name}")
        self._reload()

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
        self._reload()

    def _on_history_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if selected_entry.source == F8ComponentSourceKind.local:
            self._show_local_history(selected_entry)
            return
        self._show_remote_history(selected_entry)

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
        self._reload()

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
