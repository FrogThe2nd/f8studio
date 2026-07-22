from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy import QtWidgets

from f8pysdk.codec import dump_json

from ..common import new_asset_id
from ..components.component_catalog import component_entry_has_cached_content
from ..components.component_models import (
    F8ComponentEntry,
    F8ComponentLocalVersionSummary,
    F8ComponentRecord,
    F8ComponentRemoteAuthError,
    F8ComponentRemoteConflictError,
    F8ComponentRemoteRequestError,
    F8ComponentRemoteVersionEntry,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from ..components.component_repository import upsert_component
from ...ui.support.ui_notifications import show_info, show_warning
from .component_metadata_dialogs import ComponentMetadataDialog
from .project_asset_dialogs import (
    AssetVersionBrowserAction,
    AssetVersionBrowserDialog,
    AssetVersionBrowserItem,
)
from .catalog_hosts import ComponentCatalogVersionHost


if TYPE_CHECKING:
    _ComponentCatalogVersionFlowsMixinBase = ComponentCatalogVersionHost
else:
    _ComponentCatalogVersionFlowsMixinBase = object


_COMPONENT_VERSION_REMOTE_ERRORS = (
    F8ComponentRemoteAuthError,
    F8ComponentRemoteConflictError,
    F8ComponentRemoteRequestError,
    OSError,
    ValueError,
)
_COMPONENT_VERSION_LOCAL_SAVE_ERRORS = (OSError, ValueError)


class ComponentCatalogVersionFlowsMixin(_ComponentCatalogVersionFlowsMixinBase):
    @staticmethod
    def _local_version_item(version: F8ComponentLocalVersionSummary) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(version_number=int(version.versionNumber), created_at=str(version.createdAt))

    @staticmethod
    def _remote_version_item(version: F8ComponentRemoteVersionEntry) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(
            version_number=int(version.versionNumber),
            created_at=str(version.createdAt),
            change_summary="" if version.changeSummary is None else str(version.changeSummary),
        )

    def _show_local_history(self, entry: F8ComponentEntry) -> None:
        versions = self._sync_client.list_local_component_versions(str(entry.record.componentId))
        if not versions:
            show_info(self, "Component History", "No local history found.")
            return
        history_dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Component History - {entry.record.name}",
            items=[self._local_version_item(version) for version in versions],
            load_payload=lambda version_number: dump_json(
                self._require_local_version_payload(entry.record.componentId, version_number),
                mode="json",
            ),
        )
        history_dialog.exec()

    def _show_remote_history(self, entry: F8ComponentEntry) -> None:
        try:
            history = self._sync_client.list_component_versions(str(entry.record.componentId))
        except _COMPONENT_VERSION_REMOTE_ERRORS as exc:
            show_warning(self, "History failed", str(exc))
            return
        if not history.versions:
            show_info(self, "Component History", "No history found.")
            return
        history_dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Component History - {entry.record.name}",
            items=[self._remote_version_item(version) for version in history.versions],
            load_payload=lambda version_number: dump_json(
                self._sync_client.get_component_version(str(entry.record.componentId), version_number),
                mode="json",
            ),
            actions=[
                AssetVersionBrowserAction(action_key="save_local", label="Save As Local Component"),
                AssetVersionBrowserAction(action_key="fork_remote", label="Fork To My Cloud"),
            ],
        )
        if history_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        selected_version_number = history_dialog.selected_version_number()
        action_key = history_dialog.selected_action_key()
        if selected_version_number is None or not action_key:
            return
        if action_key == "save_local":
            self._save_remote_version_as_local_component(entry=entry, version_number=int(selected_version_number))
            return
        if action_key == "fork_remote":
            self._fork_remote_version_to_cloud(entry=entry, version_number=int(selected_version_number))

    def _require_local_version_payload(self, component_id: str, version_number: int) -> F8ComponentRecord:
        record = self._sync_client.local_component_version_record(str(component_id), int(version_number))
        if record is None:
            raise FileNotFoundError(f"Component version not found: {component_id} v{version_number}")
        return record

    def _save_remote_version_as_local_component(self, *, entry: F8ComponentEntry, version_number: int) -> None:
        try:
            historical_entry = self._sync_client.get_component_version(
                str(entry.record.componentId), int(version_number)
            )
        except _COMPONENT_VERSION_REMOTE_ERRORS as exc:
            show_warning(self, "Load version failed", str(exc))
            return
        metadata_dialog = ComponentMetadataDialog(
            parent=self,
            title="Save Remote Version As Local Component",
            name=f"{historical_entry.record.name} v{int(version_number)}",
            description=historical_entry.record.description,
            tags=list(historical_entry.record.tags or []),
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, description, tags = metadata_dialog.values()
        timestamp = component_now_iso()
        local_record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            tags=tags,
            content=historical_entry.record.content,
            createdAt=timestamp,
            updatedAt=timestamp,
        )
        try:
            upsert_component(local_record)
        except _COMPONENT_VERSION_LOCAL_SAVE_ERRORS as exc:
            show_warning(self, "Save failed", str(exc))
            return
        show_info(self, "Saved", f"Saved local component from v{int(version_number)}:\n{local_record.name}")
        self._rebuild_browser_after_draft_changed(preserve_component_id=str(local_record.componentId))

    def _fork_remote_version_to_cloud(self, *, entry: F8ComponentEntry, version_number: int) -> None:
        if not self._ensure_logged_in():
            return
        try:
            historical_entry = self._sync_client.get_component_version(
                str(entry.record.componentId), int(version_number)
            )
        except _COMPONENT_VERSION_REMOTE_ERRORS as exc:
            show_warning(self, "Load version failed", str(exc))
            return
        metadata_dialog = ComponentMetadataDialog(
            parent=self,
            title="Fork Remote Component Version",
            name=f"{historical_entry.record.name} Copy",
            description=historical_entry.record.description,
            tags=list(historical_entry.record.tags or []),
        )
        if metadata_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        visibility = self._choose_visibility()
        if visibility is None:
            return
        name, description, tags = metadata_dialog.values()
        timestamp = component_now_iso()
        forked_record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            tags=tags,
            content=historical_entry.record.content,
            createdAt=timestamp,
            updatedAt=timestamp,
        )
        forked_entry = F8ComponentEntry(
            record=forked_record,
            source=F8ComponentSourceKind.local,
            installed=True,
        )
        try:
            created = self._sync_client.fork_component(
                source_component_id=str(entry.record.componentId),
                forked_entry=forked_entry,
                visibility=visibility,
                version_number=int(version_number),
            )
        except _COMPONENT_VERSION_REMOTE_ERRORS as exc:
            show_warning(self, "Fork failed", str(exc))
            return
        show_info(self, "Forked", f"Created remote fork from v{int(version_number)}:\n{created.record.name}")
        self._rebuild_browser_after_remote_asset_changed(preserve_component_id=str(created.record.componentId))

    def _choose_visibility(self) -> F8ComponentVisibility | None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Upload visibility",
            "Publish this component publicly?\n\nYes = public\nNo = private",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
            | QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Cancel:
            return None
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            return F8ComponentVisibility.public
        return F8ComponentVisibility.private

    def _ensure_component_hydrated(
        self,
        entry: F8ComponentEntry,
        *,
        operation_name: str,
    ) -> F8ComponentEntry | None:
        if entry.source == F8ComponentSourceKind.local or component_entry_has_cached_content(entry):
            return entry
        try:
            return self._sync_client.hydrate_component(str(entry.record.componentId))
        except _COMPONENT_VERSION_REMOTE_ERRORS as exc:
            show_warning(self, f"{operation_name} failed", str(exc))
            return None
