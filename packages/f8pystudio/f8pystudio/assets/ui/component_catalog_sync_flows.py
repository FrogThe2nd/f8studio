from __future__ import annotations

from qtpy import QtWidgets

from ..components.component_catalog import component_entry_is_installed
from ..components.component_models import (
    F8ComponentEntry,
    F8ComponentVisibility,
)
from ...ui.support.ui_notifications import show_warning


class ComponentCatalogSyncFlowsMixin:
    def _offload_selected_component(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> bool:
        changed = False
        if remote_entry is not None and component_entry_is_installed(remote_entry):
            changed = self._sync_client._catalog_service.uninstall_remote_entry(str(remote_entry.record.componentId)) is not None or changed
        if changed:
            preserve_component_id = ""
            if remote_entry is not None:
                preserve_component_id = str(remote_entry.record.componentId)
            elif local_entry is not None:
                preserve_component_id = str(local_entry.record.componentId)
            self._rebuild_browser_after_installed_state_changed(
                preserve_component_id=preserve_component_id
            )
        return changed

    def _sync_selected_component(self) -> F8ComponentEntry | None:
        return None

    def _push_selected_component(
        self,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> F8ComponentEntry | None:
        _ = local_entry
        _ = remote_entry
        return None

    def _pull_selected_component(self, *, force_replace_local: bool = False) -> F8ComponentEntry | None:
        _ = force_replace_local
        selected_entry, _local_entry, remote_entry = self._selected_action_entries()
        if selected_entry is None or remote_entry is None:
            return None
        try:
            pulled = self._sync_client.hydrate_component(str(remote_entry.record.componentId))
        except Exception as exc:
            show_warning(self, "Pull failed", str(exc))
            return None
        self._rebuild_browser_after_installed_state_changed(
            preserve_component_id=str(pulled.record.componentId)
        )
        return pulled
