# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

import msgspec
from NodeGraphQt import BaseNode
from qtpy import QtWidgets

from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8StateAccess, F8VariantRecord
from f8pysdk.spec_metadata import coerce_spec_payload

from ..assets.common import JsonObject, json_object_from_value
from ..ui.support.ui_notifications import show_info, show_warning
from ..assets.ui.project_asset_dialogs import AssetOverwriteChoice, AssetOverwriteMetaDialog
from ..assets.variants.variant_compose import _VariantNode as _ComposeVariantNode
from ..assets.variants.variant_compose import build_variant_record_from_node
from ..assets.variants.variant_ids import build_variant_node_type
from ..assets.variants.variant_metadata import variant_ref_from_record, variant_ref_to_json
from ..assets.variants.variant_repository import (
    local_variant_entry_by_name,
    list_entries_for_base,
    normalize_variant_name,
    upsert_variant,
    variant_record,
)
from .node_base import F8StudioBaseNode
from .node_model import F8StudioNodeModel


class _NodeClassProtocol(Protocol):
    type_: object


class _ContextNodesMenuProtocol(Protocol):
    def add_command(self, label: str, *, func: Callable[..., object], node_type: str) -> object: ...


class _GraphVariantHost(Protocol):
    def _notification_parent(self) -> QtWidgets.QWidget | None: ...

    def context_nodes_menu(self) -> _ContextNodesMenuProtocol | None: ...

    def create_node(
        self,
        node_type: str,
        *,
        pos: tuple[float, float] | None = None,
        selected: bool = True,
        push_undo: bool = True,
    ) -> BaseNode | None: ...


class _StateFieldProtocol(Protocol):
    name: object
    access: F8StateAccess


class _VariantNodeModelProtocol(Protocol):
    properties: dict[str, object]
    custom_properties: dict[object, object]
    f8_sys: dict[str, object]


class _VariantStudioNodeProtocol(Protocol):
    NODE_NAME: str
    spec: F8OperatorSpec | F8ServiceSpec
    model: _VariantNodeModelProtocol
    type_: object

    def name(self) -> str: ...

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None: ...

    def sync_from_spec(self) -> None: ...

    def set_property(self, name: str, value: object, *, push_undo: bool = True) -> None: ...


class GraphVariantActionsMixin:
    _variant_menu_node_types: set[str] | None = None

    def _variant_menu_types(self) -> set[str]:
        node_types: set[str] | None = self._variant_menu_node_types
        if node_types is None:
            node_types = set()
            self._variant_menu_node_types = node_types
        return node_types

    @staticmethod
    def _variant_node_or_none(node: BaseNode | None) -> F8StudioBaseNode | None:
        if isinstance(node, F8StudioBaseNode):
            return node
        return None

    @staticmethod
    def _variant_tags(spec: F8OperatorSpec | F8ServiceSpec) -> list[str]:
        raw_tags = spec.tags
        if isinstance(raw_tags, msgspec.UnsetType):
            return []
        return [str(tag) for tag in list(raw_tags or []) if str(tag).strip()]

    @staticmethod
    def _state_fields(spec: F8OperatorSpec | F8ServiceSpec) -> list[_StateFieldProtocol]:
        raw_state_fields = spec.stateFields
        if isinstance(raw_state_fields, msgspec.UnsetType):
            return []
        return [cast(_StateFieldProtocol, cast(object, field)) for field in list(raw_state_fields or [])]

    @staticmethod
    def _json_object_or_none(value: object) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        return json_object_from_value(cast(object, value))

    @classmethod
    def _json_object_list(cls, value: object) -> list[JsonObject]:
        if not isinstance(value, list):
            return []
        out: list[JsonObject] = []
        for entry in cast(list[object], value):
            entry_object = cls._json_object_or_none(entry)
            if entry_object is not None:
                out.append(entry_object)
        return out

    def _prompt_variant_metadata(
        self,
        *,
        default_name: str,
        default_description: str,
        default_tags: list[str],
        overwrite_choices: list[AssetOverwriteChoice],
        name_validator: Callable[[str, str | None], str | None] | None = None,
    ) -> tuple[str, str, list[str], str | None] | None:
        dialog = AssetOverwriteMetaDialog(
            parent=None,
            title="Save Node As Variant",
            name=default_name,
            description=default_description,
            tags=default_tags,
            overwrite_choices=overwrite_choices,
            overwrite_label="Overwrite Existing Variant",
            name_validator=name_validator,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:  # pyright: ignore[reportUnknownMemberType]
            return None
        return dialog.values()

    def _save_node_as_variant(self, node: BaseNode | None) -> None:
        host = cast(_GraphVariantHost, cast(object, self))
        raw_variant_node = self._variant_node_or_none(node)
        if raw_variant_node is None:
            return
        variant_node = cast(_VariantStudioNodeProtocol, cast(object, raw_variant_node))
        spec = variant_node.spec
        node_display_name = str(variant_node.name() or "").strip()
        default_name = str(node_display_name or variant_node.NODE_NAME or spec.label or "").strip() or "Variant"
        default_desc = str(spec.description or "").strip()
        default_tags = self._variant_tags(spec)
        node_type = str(variant_node.type_ or "").strip()
        overwrite_choices = [
            AssetOverwriteChoice(
                asset_id=str(entry.record.variantId),
                label=str(entry.record.name),
                description=str(entry.record.description),
                tags=[str(tag) for tag in list(entry.record.tags or []) if str(tag).strip()],
            )
            for entry in list_entries_for_base(node_type, include_uninstalled=True)
            if str(entry.source.value) == "local"
        ]

        def _validate_variant_name(candidate: str, overwrite_variant_id: str | None) -> str | None:
            normalized_name = normalize_variant_name(candidate)
            existing_local_entry = local_variant_entry_by_name(node_type, normalized_name)
            if overwrite_variant_id:
                if existing_local_entry is not None and str(existing_local_entry.record.variantId) != str(overwrite_variant_id):
                    return f"Variant name '{normalized_name}' already exists. Please choose the existing variant to overwrite."
                return None
            if existing_local_entry is not None:
                return f"Variant name '{normalized_name}' already exists. Please choose the existing variant to overwrite."
            return None

        values = self._prompt_variant_metadata(
            default_name=default_name,
            default_description=default_desc,
            default_tags=default_tags,
            overwrite_choices=overwrite_choices,
            name_validator=_validate_variant_name,
        )
        if values is None:
            return
        name, description, tags, overwrite_variant_id = values
        normalized_name = normalize_variant_name(name)
        existing_local_entry = (
            None
            if overwrite_variant_id is None
            else next(
                (entry for entry in list_entries_for_base(node_type, include_uninstalled=True) if str(entry.record.variantId) == str(overwrite_variant_id)),
                None,
            )
        )
        if existing_local_entry is None:
            existing_local_entry = local_variant_entry_by_name(node_type, normalized_name)
        record = build_variant_record_from_node(
            node=cast(_ComposeVariantNode, cast(object, variant_node)),
            name=name,
            description=description,
            tags=tags,
            variant_id=(None if existing_local_entry is None else str(existing_local_entry.record.variantId)),
        )
        try:
            saved_record = upsert_variant(record)
        except ValueError as exc:
            show_warning(host._notification_parent(), "Invalid name", str(exc))
            return
        title = "Variant Updated" if existing_local_entry is not None else "Variant Saved"
        show_info(host._notification_parent(), title, f"Saved variant:\n{saved_record.name}")

    def _on_save_variant_menu_action(self, graph: object, node: BaseNode | None) -> None:
        _ = graph
        self._save_node_as_variant(node)

    def install_variant_context_menu_for_nodes(self, node_classes: list[type[_NodeClassProtocol]]) -> None:
        host = cast(_GraphVariantHost, cast(object, self))
        nodes_menu = host.context_nodes_menu()
        if nodes_menu is None:
            return
        variant_menu_node_types = self._variant_menu_types()
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in variant_menu_node_types:
                continue
            _ = nodes_menu.add_command(
                "Save As Variant...",
                func=self._on_save_variant_menu_action,
                node_type=node_type,
            )
            variant_menu_node_types.add(node_type)

    @staticmethod
    def _variant_record(variant_id: str) -> F8VariantRecord | None:
        return variant_record(variant_id)

    @staticmethod
    def _coerce_variant_spec(value: JsonObject) -> F8OperatorSpec | F8ServiceSpec:
        return coerce_spec_payload(value)

    def _apply_variant_to_node(
        self,
        *,
        node: F8StudioBaseNode,
        variant_record: F8VariantRecord,
        variant_spec_json: JsonObject,
    ) -> None:
        _ = cast(_GraphVariantHost, cast(object, self))
        typed_node = cast(_VariantStudioNodeProtocol, cast(object, node))
        spec = self._coerce_variant_spec(variant_spec_json)
        typed_node.spec = spec
        typed_node.set_ui_overrides({}, rebuild=False)
        typed_node.sync_from_spec()

        writable_fields: set[str] = set()
        for field_spec in self._state_fields(spec):
            field_name = str(field_spec.name or "").strip()
            if not field_name or field_spec.access == F8StateAccess.ro:
                continue
            writable_fields.add(field_name)

        state_defaults: dict[str, object] = {}
        for raw_field in self._json_object_list(variant_spec_json.get("stateFields")):
            field_name = str(raw_field.get("name") or "").strip()
            if not field_name or field_name not in writable_fields:
                continue
            value_schema = self._json_object_or_none(raw_field.get("valueSchema"))
            if value_schema is None or "default" not in value_schema:
                continue
            state_defaults[field_name] = value_schema["default"]

        model = typed_node.model
        property_names = set(model.properties.keys())
        custom_properties = model.custom_properties
        custom_property_names = {str(key) for key in custom_properties.keys()}
        for field_name, default_value in state_defaults.items():
            if field_name not in property_names and field_name not in custom_property_names:
                continue
            try:
                typed_node.set_property(field_name, default_value, push_undo=False)
            except (KeyError, TypeError, ValueError, RuntimeError):
                continue
        variant_ref = variant_ref_from_record(variant_record)
        if isinstance(model, F8StudioNodeModel):
            model.variantRef = variant_ref
        else:
            model.f8_sys["variantRef"] = variant_ref_to_json(variant_ref)

    def create_variant_node(
        self,
        variant_id: str,
        *,
        pos: tuple[float, float] | None = None,
        selected: bool = True,
        push_undo: bool = True,
    ) -> BaseNode | None:
        host = cast(_GraphVariantHost, cast(object, self))
        node_type = build_variant_node_type(variant_id)
        return host.create_node(node_type, pos=pos, selected=selected, push_undo=push_undo)
