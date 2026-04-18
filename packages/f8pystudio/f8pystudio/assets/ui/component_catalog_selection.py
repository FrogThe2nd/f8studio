from __future__ import annotations

import json
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.codec import dump_json

from ...nodegraph.session_schema import extract_layout
from ...ui.support.ui_icons import StudioIcon, icon_for
from ..components.component_catalog import (
    component_entry_can_hydrate,
    component_entry_has_cached_content,
    component_entry_is_installed,
)
from ..components.component_models import F8ComponentEntry, F8ComponentSourceKind, F8ComponentVisibility

logger = logging.getLogger(__name__)

AUTO_PREVIEW_NODE_THRESHOLD = 10


class ComponentCatalogSelectionMixin:
    def _selected_entry(self) -> F8ComponentEntry | None:
        item = self._list.currentItem()
        if item is None:
            return None
        component_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        if not component_id:
            return None
        for entry in self._entries:
            if str(entry.record.componentId) == component_id:
                return entry
        return None

    def _local_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self._sync_client._catalog_service._local_provider.load_entries():
            if str(entry.record.componentId or "").strip() == normalized_component_id:
                return entry
        return None

    def _remote_entry_for_component_id(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self._sync_client._catalog_service._remote_provider.load_entries():
            if str(entry.record.componentId or "").strip() == normalized_component_id:
                return entry
        return None

    def _selected_action_entries(self) -> tuple[F8ComponentEntry | None, F8ComponentEntry | None, F8ComponentEntry | None]:
        active_entry = self._selected_entry()
        if active_entry is None:
            return None, None, None
        component_id = str(active_entry.record.componentId or "").strip()
        return (
            active_entry,
            self._local_entry_for_component_id(component_id),
            self._remote_entry_for_component_id(component_id),
        )

    def _refresh_action_buttons(
        self,
        selected_entry_override: F8ComponentEntry | None,
    ) -> None:
        selected, local_entry, remote_entry = self._selected_action_entries()
        if selected_entry_override is not None:
            selected = selected_entry_override
            component_id = str(selected_entry_override.record.componentId or "").strip()
            local_entry = self._local_entry_for_component_id(component_id)
            remote_entry = self._remote_entry_for_component_id(component_id)

        current_tab = self._scope_tabs.currentIndex()
        has_selection = selected is not None
        can_load, can_offload = self._load_action_availability(local_entry=local_entry, remote_entry=remote_entry)
        load_tooltip = self._load_action_tooltip(
            can_offload=can_offload,
            local_entry=local_entry,
        )
        self._set_button_state(
            self._btn_install,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=can_load or can_offload,
            tooltip=load_tooltip,
            icon_token=StudioIcon.CLOUD_DOWN if not can_offload else StudioIcon.CLOUD_UP,
        )

        self._set_button_state(
            self._btn_upload,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)),
            tooltip="Sync",
            icon_token=StudioIcon.TRANSFER,
        )

        can_subscribe = (
            has_selection
            and selected is not None
            and selected.source == F8ComponentSourceKind.remote_public
            and not self._is_owned_remote_entry(selected)
        )
        subscribe_text = "Unsubscribe" if selected is not None and selected.subscribed else "Subscribe"
        self._set_button_state(
            self._btn_subscribe,
            visible=current_tab == self._TAB_COMMUNITY and can_subscribe,
            enabled=can_subscribe,
            tooltip=subscribe_text,
            icon_token=StudioIcon.HEART_ON if selected is not None and selected.subscribed else StudioIcon.HEART_OFF,
        )

        self._set_button_state(
            self._btn_copy_local,
            visible=has_selection and current_tab != self._TAB_INSTALLED,
            enabled=has_selection,
            tooltip="Copy to Draft",
            icon_token=StudioIcon.SAVE_AS,
        )

        self._set_button_state(
            self._btn_delete,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=local_entry is not None or (remote_entry is not None and self._is_owned_remote_entry(remote_entry)),
            tooltip="Delete",
            icon_token=StudioIcon.TRASH,
        )

        self._set_button_state(
            self._btn_edit,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=local_entry is not None,
            tooltip="Edit Metadata",
            icon_token=StudioIcon.EDIT,
        )

        visibility_label = "Make Public"
        if remote_entry is not None and remote_entry.visibility == F8ComponentVisibility.public:
            visibility_label = "Make Private"
        self._set_button_state(
            self._btn_visibility,
            visible=has_selection and current_tab == self._TAB_MINE,
            enabled=remote_entry is not None and self._is_owned_remote_entry(remote_entry),
            tooltip=visibility_label,
            icon_token=StudioIcon.PRIVATE if visibility_label == "Make Private" else StudioIcon.PUBLIC,
        )

        self._set_button_state(
            self._btn_history,
            visible=has_selection and current_tab != self._TAB_COMMUNITY,
            enabled=local_entry is not None or remote_entry is not None,
            tooltip="History",
            icon_token=StudioIcon.ARTICLE,
        )

        self._set_button_state(
            self._btn_create,
            visible=has_selection and current_tab == self._TAB_INSTALLED,
            enabled=selected is not None and component_entry_is_installed(selected),
            tooltip="Create on canvas",
            icon_token=StudioIcon.CIRCLE_PLUS,
        )

    def _on_selection_changed(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is None:
            self._raw.setPlainText("")
            self._preview.clear_preview("Select a component to preview.")
            self._refresh_action_buttons(None)
            return

        preview_entry, hydration_error = self._hydrate_selection_entry(selected_entry=selected_entry)
        if preview_entry is None:
            self._show_hydration_failure(
                selected_entry=selected_entry,
                hydration_error=hydration_error,
            )
        else:
            self._show_component_preview(entry=preview_entry)
        self._refresh_action_buttons(selected_entry)

    def _hydrate_selection_entry(
        self,
        *,
        selected_entry: F8ComponentEntry,
    ) -> tuple[F8ComponentEntry | None, str]:
        if not component_entry_can_hydrate(selected_entry):
            return selected_entry, ""
        if component_entry_has_cached_content(selected_entry):
            return selected_entry, ""
        component_id = str(selected_entry.record.componentId or "").strip()
        try:
            return self._sync_client.hydrate_component(component_id), ""
        except Exception as exc:
            logger.exception(
                "Component manager failed to hydrate selected component preview component_id=%s",
                component_id,
            )
            return None, str(exc)

    def _show_hydration_failure(
        self,
        *,
        selected_entry: F8ComponentEntry,
        hydration_error: str,
    ) -> None:
        self._raw.setPlainText(
            json.dumps(
                {
                    "componentId": str(selected_entry.record.componentId),
                    "operation": "hydrate_component",
                    "error": hydration_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self._preview.clear_preview(f"Failed to preview component.\n{hydration_error}")

    def _show_component_preview(self, *, entry: F8ComponentEntry) -> None:
        self._raw.setPlainText(
            json.dumps(
                dump_json(entry, mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        preview_node_count = component_preview_node_count(entry.record.content)
        if preview_node_count > AUTO_PREVIEW_NODE_THRESHOLD:
            self._preview.show_deferred_component_payload(
                entry.record.content,
                message=(
                    f"This component has {preview_node_count} nodes.\n"
                    "Automatic preview is paused to keep browsing fast."
                ),
                button_text="Load preview manually",
            )
            return
        self._preview.show_component_payload(entry.record.content)

    @staticmethod
    def _set_button_state(
        button: QtWidgets.QPushButton,
        *,
        visible: bool,
        enabled: bool,
        tooltip: str,
        icon_token: StudioIcon,
    ) -> None:
        button.setVisible(visible)
        button.setEnabled(visible and enabled)
        button.setToolTip(tooltip)
        button.setIcon(icon_for(button, icon_token))

    @staticmethod
    def _is_local_draft_entry(entry: F8ComponentEntry | None) -> bool:
        return entry is not None and entry.isLocalDraft

    @classmethod
    def _load_action_availability(
        cls,
        *,
        local_entry: F8ComponentEntry | None,
        remote_entry: F8ComponentEntry | None,
    ) -> tuple[bool, bool]:
        if cls._is_local_draft_entry(local_entry):
            return False, False
        can_load = remote_entry is not None and not component_entry_is_installed(remote_entry)
        can_offload = local_entry is not None or (remote_entry is not None and component_entry_is_installed(remote_entry))
        return can_load, can_offload

    @classmethod
    def _load_action_tooltip(cls, *, can_offload: bool, local_entry: F8ComponentEntry | None) -> str:
        if local_entry is not None and local_entry.isLocalDraft:
            return cls.LOCAL_DRAFT_LOAD_TOOLTIP
        if can_offload:
            return "Offload"
        return "Load"

def component_preview_node_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        layout = extract_layout(payload)
    except ValueError:
        return 0
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict):
        return 0
    return len(nodes)
