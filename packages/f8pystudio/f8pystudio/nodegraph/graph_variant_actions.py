from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json, validate_as
from collections.abc import Callable
from typing import Any

from qtpy import QtWidgets
from NodeGraphQt import BaseNode

from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8StateAccess
from f8pysdk.spec_metadata import coerce_spec_payload

from ..ui_notifications import show_info, show_warning
from ..variants.variant_compose import build_variant_record_from_node
from ..variants.variant_ids import build_variant_node_type
from ..variants.variant_repository import is_variant_name_conflict, load_library, normalize_variant_name, upsert_variant


class GraphVariantActionsMixin:
    def _prompt_variant_metadata(
        self,
        *,
        default_name: str,
        default_description: str,
        default_tags: list[str],
        name_validator: Callable[[str], str | None] | None = None,
    ) -> tuple[str, str, list[str]] | None:
        dialog = QtWidgets.QDialog(None)
        dialog.setWindowTitle("Save Node As Variant")
        dialog.resize(520, 220)

        name_edit = QtWidgets.QLineEdit(default_name, dialog)
        desc_edit = QtWidgets.QLineEdit(default_description, dialog)
        tags_edit = QtWidgets.QLineEdit(", ".join(default_tags), dialog)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", name_edit)
        form.addRow("Description", desc_edit)
        form.addRow("Tags (comma-separated)", tags_edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=dialog,
        )

        def _on_accept() -> None:
            candidate = str(name_edit.text() or "").strip()
            if not candidate:
                show_warning(dialog, "Invalid name", "Variant name cannot be empty.")
                return
            validator = name_validator
            if validator is not None:
                message = validator(candidate)
                if message:
                    show_warning(dialog, "Invalid name", message)
                    return
            dialog.accept()

        buttons.accepted.connect(_on_accept)  # type: ignore[attr-defined]
        buttons.rejected.connect(dialog.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        name = str(name_edit.text() or "").strip()
        if not name:
            return None
        description = str(desc_edit.text() or "").strip()
        tags = [s.strip() for s in str(tags_edit.text() or "").split(",")]
        return name, description, [t for t in tags if t]

    def _save_node_as_variant(self, node: Any) -> None:
        if node is None:
            return
        try:
            spec = node.spec
        except AttributeError:
            return
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            return
        node_display_name = ""
        try:
            node_display_name = str(node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            node_display_name = ""
        default_name = str(node_display_name or node.NODE_NAME or spec.label or "").strip() or "Variant"
        default_desc = str(spec.description or "").strip()
        default_tags = [str(t) for t in list(spec.tags or []) if str(t).strip()]
        node_type = str(node.type_ or "").strip()

        def _validate_variant_name(candidate: str) -> str | None:
            normalized_name = normalize_variant_name(candidate)
            if is_variant_name_conflict(node_type, normalized_name):
                return f"Variant name '{normalized_name}' already exists. Please rename."
            return None

        values = self._prompt_variant_metadata(
            default_name=default_name,
            default_description=default_desc,
            default_tags=default_tags,
            name_validator=_validate_variant_name,
        )
        if values is None:
            return
        name, description, tags = values
        normalized_name = normalize_variant_name(name)
        node_type = str(node.type_ or "").strip()
        if is_variant_name_conflict(node_type, normalized_name):
            show_warning(
                self._notification_parent(),
                "Invalid name",
                f"Variant name '{normalized_name}' already exists. Please rename.",
            )
            return
        record = build_variant_record_from_node(node=node, name=name, description=description, tags=tags)
        try:
            upsert_variant(record)
        except ValueError as exc:
            show_warning(self._notification_parent(), "Invalid name", str(exc))
            return
        show_info(self._notification_parent(), "Variant Saved", f"Saved variant:\n{name}")

    def _on_save_variant_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        self._save_node_as_variant(node)

    def install_variant_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        nodes_menu = self.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in self._variant_menu_node_types:
                continue
            nodes_menu.add_command(
                "Save As Variant...",
                func=self._on_save_variant_menu_action,
                node_type=node_type,
            )
            self._variant_menu_node_types.add(node_type)

    @staticmethod
    def _variant_record(variant_id: str) -> dict[str, Any] | None:
        vid = str(variant_id or "").strip()
        if not vid:
            return None
        lib = load_library()
        for v in lib.variants:
            if str(v.variantId) == vid:
                return dump_json(v, mode="json")
        return None

    @staticmethod
    def _coerce_variant_spec(value: dict[str, Any]) -> F8OperatorSpec | F8ServiceSpec:
        return coerce_spec_payload(value)

    def _apply_variant_to_node(
        self,
        *,
        node: BaseNode,
        variant_id: str,
        variant_name: str,
        variant_spec_json: dict[str, Any],
    ) -> None:
        spec = self._coerce_variant_spec(variant_spec_json)
        node.spec = spec  # type: ignore[attr-defined]
        node.set_ui_overrides({}, rebuild=False)  # type: ignore[attr-defined]
        node.sync_from_spec()  # type: ignore[attr-defined]
        writable_fields: set[str] = set()
        for field_spec in list(spec.stateFields or []):
            field_name = str(field_spec.name or "").strip()
            if not field_name:
                continue
            if field_spec.access == F8StateAccess.ro:
                continue
            writable_fields.add(field_name)

        state_defaults: dict[str, Any] = {}
        raw_state_fields = variant_spec_json.get("stateFields")
        if isinstance(raw_state_fields, list):
            for raw_field in raw_state_fields:
                if not isinstance(raw_field, dict):
                    continue
                field_name = str(raw_field.get("name") or "").strip()
                if not field_name or field_name not in writable_fields:
                    continue
                value_schema = raw_field.get("valueSchema")
                if not isinstance(value_schema, dict) or "default" not in value_schema:
                    continue
                state_defaults[field_name] = value_schema.get("default")

        for field_name, default_value in state_defaults.items():
            has_property = False
            try:
                has_property = field_name in node.model.properties or field_name in node.model.custom_properties
            except (AttributeError, RuntimeError, TypeError):
                has_property = False
            if not has_property:
                continue
            try:
                node.set_property(field_name, default_value, push_undo=False)
            except (AttributeError, RuntimeError, TypeError, KeyError, ValueError):
                continue
        if not isinstance(node.model.f8_sys, dict):
            node.model.f8_sys = {}
        node.model.f8_sys["variantId"] = str(variant_id)
        node.model.f8_sys["variantName"] = str(variant_name or "")

    def create_variant_node(
        self,
        variant_id: str,
        *,
        pos: tuple[float, float] | None = None,
        selected: bool = True,
        push_undo: bool = True,
    ) -> BaseNode | None:
        node_type = build_variant_node_type(variant_id)
        return self.create_node(node_type, pos=pos, selected=selected, push_undo=push_undo)

