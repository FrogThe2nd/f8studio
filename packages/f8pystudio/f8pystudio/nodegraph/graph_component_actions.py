from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any, Protocol, cast

from qtpy import QtWidgets

from ..assets.common import new_asset_id
from ..assets.components.component_drafts import ComponentDraftService
from ..assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRecord,
    component_now_iso,
)
from ..assets.components.component_repository import upsert_component
from ..assets.ui.component_metadata_dialogs import ComponentOverwriteMetadataDialog
from ..assets.ui.component_overwrite_choices import component_draft_overwrite_choice
from ..ui.support.ui_notifications import show_info, show_warning
from .component_publish_payload import (
    collect_component_selected_node_ids,
    trim_component_publish_payload_to_selected_nodes,
)


logger = logging.getLogger(__name__)


class _NodeClassProtocol(Protocol):
    type_: object


class _ContextNodesMenuProtocol(Protocol):
    def add_command(self, label: str, *, func: Callable[..., object], node_type: str) -> object: ...


class _SelectedNodeProtocol(Protocol):
    def name(self) -> str: ...


class _GraphComponentHost(Protocol):
    def _notification_parent(self) -> QtWidgets.QWidget | None: ...

    def context_nodes_menu(self) -> _ContextNodesMenuProtocol | None: ...

    def selected_nodes(self) -> list[object]: ...

    def serialize_publish_session(self) -> dict[str, object]: ...


class GraphComponentActionsMixin:
    _component_menu_node_types: set[str] | None = None

    def _component_menu_types(self) -> set[str]:
        node_types = self._component_menu_node_types
        if node_types is None:
            node_types = set()
            self._component_menu_node_types = node_types
        return node_types

    @staticmethod
    def _selected_node_name(node: object) -> str:
        try:
            return str(cast(_SelectedNodeProtocol, node).name() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            return ""

    @classmethod
    def _draft_component_entries(cls) -> list[F8ComponentEntry]:
        return ComponentDraftService().list_catalog_entries()

    @classmethod
    def _normalize_component_name(cls, name: str) -> str:
        return str(name or "").strip()

    @classmethod
    def _draft_component_entry_by_name(
        cls, name: str, *, exclude_component_id: str | None = None
    ) -> F8ComponentEntry | None:
        normalized_name = cls._normalize_component_name(name)
        excluded_id = str(exclude_component_id or "").strip()
        if not normalized_name:
            return None
        for entry in cls._draft_component_entries():
            component_id = str(entry.record.componentId or "").strip()
            if excluded_id and component_id == excluded_id:
                continue
            if cls._normalize_component_name(entry.record.name) == normalized_name:
                return entry
        return None

    def _save_selected_nodes_as_component(self) -> None:
        host = cast(_GraphComponentHost, cast(object, self))
        selected_nodes = list(host.selected_nodes() or [])
        if not selected_nodes:
            show_warning(host._notification_parent(), "Save component failed", "Select at least one node first.")
            return

        selected_node_ids = collect_component_selected_node_ids(selected_nodes)
        if not selected_node_ids:
            show_warning(host._notification_parent(), "Save component failed", "Selected nodes are missing stable ids.")
            return

        default_name = (
            self._selected_node_name(selected_nodes[0]) if len(selected_nodes) == 1 else "Selection Component"
        )

        overwrite_choices = [component_draft_overwrite_choice(entry) for entry in self._draft_component_entries()]
        overwrite_choices.sort(key=lambda choice: (choice.label.lower(), choice.asset_id))

        def _validate_save_component_name(candidate: str, overwrite_component_id: str | None) -> str | None:
            normalized_name = self._normalize_component_name(candidate)
            overwrite_entry = None
            if overwrite_component_id:
                for entry in self._draft_component_entries():
                    if str(entry.record.componentId) == str(overwrite_component_id):
                        overwrite_entry = entry
                        break
            exclude_id = None if overwrite_entry is None else str(overwrite_entry.record.componentId)
            if self._draft_component_entry_by_name(name=normalized_name, exclude_component_id=exclude_id) is not None:
                return (
                    f"Component draft named '{normalized_name}' already exists. "
                    "Select that local draft as the overwrite target, or use a different name."
                )
            return None

        dialog = ComponentOverwriteMetadataDialog(
            parent=host._notification_parent(),
            title="Save As Component",
            name=default_name or "Selection Component",
            description="",
            tags=[],
            overwrite_choices=overwrite_choices,
            overwrite_label="Overwrite Local Draft",
            name_validator=_validate_save_component_name,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        try:
            name, description, tags, overwrite_component_id = dialog.values()
            payload = trim_component_publish_payload_to_selected_nodes(
                payload=host.serialize_publish_session(),
                selected_node_ids=selected_node_ids,
            )
            overwrite_entry = None
            if overwrite_component_id:
                for entry in self._draft_component_entries():
                    if str(entry.record.componentId) == str(overwrite_component_id):
                        overwrite_entry = entry
                        break
            if overwrite_entry is None:
                overwrite_entry = self._draft_component_entry_by_name(name)
            timestamp = component_now_iso()
            record = F8ComponentRecord(
                componentId=new_asset_id() if overwrite_entry is None else str(overwrite_entry.record.componentId),
                name=name,
                description=description,
                tags=tags,
                content=payload,
                createdAt=timestamp,
                updatedAt=timestamp,
            )
            upsert_component(record)
        except Exception as exc:
            logger.exception("Failed to save selected nodes as component")
            show_warning(host._notification_parent(), "Save component failed", f"Failed to save component.\n\n{exc}")
            return

        action_text = "Updated" if overwrite_entry is not None else "Saved"
        show_info(host._notification_parent(), f"Component {action_text}", f"{action_text} component:\n{record.name}")

    def _on_save_component_menu_action(self, graph: object, node: object) -> None:
        _ = (graph, node)
        self._save_selected_nodes_as_component()

    def install_component_context_menu_for_nodes(self, node_classes: list[type[_NodeClassProtocol]]) -> None:
        host = cast(_GraphComponentHost, cast(object, self))
        nodes_menu = host.context_nodes_menu()
        if nodes_menu is None:
            return
        component_menu_node_types = self._component_menu_types()
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "").strip()
            if not node_type or node_type in component_menu_node_types:
                continue
            _ = nodes_menu.add_command(
                "Save As Component...",
                func=self._on_save_component_menu_action,
                node_type=node_type,
            )
            component_menu_node_types.add(node_type)
