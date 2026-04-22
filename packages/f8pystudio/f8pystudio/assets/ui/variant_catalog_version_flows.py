from __future__ import annotations

from typing import TYPE_CHECKING

from f8pysdk.codec import dump_json
from f8pysdk.specs import F8VariantRecord

from ..variants.variant_models import (
    F8VariantEntry,
    F8VariantLocalVersionSummary,
    F8VariantRemoteVersionEntry,
    F8VariantSourceKind,
)
from ...ui.support.ui_notifications import show_info, show_warning
from .catalog_hosts import VariantCatalogVersionHost
from .project_asset_dialogs import AssetVersionBrowserDialog, AssetVersionBrowserItem


if TYPE_CHECKING:
    _VariantCatalogVersionFlowsMixinBase = VariantCatalogVersionHost
else:
    _VariantCatalogVersionFlowsMixinBase = object


class VariantCatalogVersionFlowsMixin(_VariantCatalogVersionFlowsMixinBase):
    def _on_history_clicked(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            return
        if self._scope_tabs.currentIndex() == self._TAB_DRAFTS:
            if not selected_entry.draftOriginAssetId:
                show_info(self, "Variant History", "Local drafts do not keep local version history.")
                return
            remote_entry = self._remote_entry_for_variant_id(str(selected_entry.draftOriginAssetId))
            if remote_entry is None:
                show_warning(self, "History failed", "Linked cloud asset is not available.")
                return
            self._show_remote_history(remote_entry)
            return
        if selected_entry.source == F8VariantSourceKind.local:
            self._show_local_history(selected_entry)
            return
        self._show_remote_history(selected_entry)

    def _show_local_history(self, entry: F8VariantEntry) -> None:
        versions = self._sync_client.list_local_variant_versions(str(entry.record.variantId))
        if not versions:
            show_info(self, "Variant History", "No local history found.")
            return
        dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Variant History - {entry.record.name}",
            items=[self._local_version_item(version) for version in versions],
            load_payload=lambda version_number: dump_json(
                self._require_local_version_payload(str(entry.record.variantId), int(version_number)),
                mode="json",
            ),
        )
        dialog.exec()

    def _show_remote_history(self, entry: F8VariantEntry) -> None:
        try:
            history = self._sync_client.list_variant_versions(str(entry.record.variantId))
        except Exception as exc:
            show_warning(self, "History failed", str(exc))
            return
        if not history.versions:
            show_info(self, "Variant History", "No history found.")
            return
        dialog = AssetVersionBrowserDialog(
            parent=self,
            title=f"Variant History - {entry.record.name}",
            items=[self._remote_version_item(version) for version in history.versions],
            load_payload=lambda version_number: dump_json(
                self._sync_client.get_variant_version(str(entry.record.variantId), int(version_number)),
                mode="json",
            ),
        )
        dialog.exec()

    @staticmethod
    def _local_version_item(version: F8VariantLocalVersionSummary) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(version_number=int(version.versionNumber), created_at=str(version.createdAt))

    @staticmethod
    def _remote_version_item(version: F8VariantRemoteVersionEntry) -> AssetVersionBrowserItem:
        return AssetVersionBrowserItem(
            version_number=int(version.versionNumber),
            created_at=str(version.createdAt),
            change_summary="" if version.changeSummary is None else str(version.changeSummary),
        )

    def _require_local_version_payload(self, variant_id: str, version_number: int) -> F8VariantRecord:
        record = self._sync_client.local_variant_version_record(str(variant_id), int(version_number))
        if record is None:
            raise FileNotFoundError(f"Variant version not found: {variant_id} v{version_number}")
        return record
