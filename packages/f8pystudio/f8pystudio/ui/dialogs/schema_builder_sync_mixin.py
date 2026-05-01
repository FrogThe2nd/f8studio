from __future__ import annotations

import json
from typing import Any, cast

from qtpy import QtWidgets

from f8pysdk.specs import F8DataTypeSchema

from .schema_builder_common import schema_from_json_obj, schema_to_json_obj, validate_schema_json_unknown_keys
from ..support.studio_theme import label_qss, studio_dark_theme


class SchemaBuilderSyncMixin:
    def _on_accept(self) -> None:
        host = cast(Any, self)
        if not host._is_schema_valid:
            return
        host.accept()

    def _on_json_text_changed(self) -> None:
        host = cast(Any, self)
        if host._is_updating_from_ui:
            return
        host._json_timer.start()

    def _on_json_debounce_timeout(self) -> None:
        host = cast(Any, self)
        if host._is_updating_from_ui:
            return
        text = str(host._json_edit.toPlainText() or "").strip()
        if not text:
            host._set_status_invalid("JSON is empty")
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            host._set_status_invalid(f"Invalid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})")
            return
        if not isinstance(obj, dict):
            host._set_status_invalid("Schema root must be a JSON object")
            return

        unknown = validate_schema_json_unknown_keys(obj)
        if unknown:
            host._set_status_invalid("Unknown schema keys: " + ", ".join(unknown[:5]))
            return

        try:
            schema = schema_from_json_obj(obj)
        except Exception as exc:
            host._set_status_invalid(f"Schema validation failed: {exc}")
            return

        host._schema = cast(F8DataTypeSchema, schema)
        host._schema_obj = schema_to_json_obj(schema)
        host._set_status_valid("Valid schema")

        current_path = host._current_path()
        host._is_updating_from_json = True
        try:
            host._rebuild_tree(select_path=current_path)
        finally:
            host._is_updating_from_json = False

    def _set_status_valid(self, message: str) -> None:
        host = cast(Any, self)
        host._is_schema_valid = True
        host._status.setText(str(message))
        host._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.success))
        ok = host._buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(not host._read_only)

    def _set_status_invalid(self, message: str) -> None:
        host = cast(Any, self)
        host._is_schema_valid = False
        host._status.setText(str(message))
        host._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.error))
        ok = host._buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(False)

    def _sync_from_ui(self, preferred_path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        if host._is_updating_from_json:
            return

        unknown = validate_schema_json_unknown_keys(host._schema_obj)
        if unknown:
            host._set_status_invalid("Unknown schema keys: " + ", ".join(unknown[:5]))
            return

        try:
            schema = schema_from_json_obj(host._schema_obj)
        except Exception as exc:
            host._set_status_invalid(f"Schema validation failed: {exc}")
            return

        host._schema = cast(F8DataTypeSchema, schema)
        host._schema_obj = schema_to_json_obj(schema)

        host._is_updating_from_ui = True
        try:
            host._json_edit.setPlainText(json.dumps(host._schema_obj, ensure_ascii=False, indent=2))
        finally:
            host._is_updating_from_ui = False

        host._set_status_valid("Valid schema")
        host._schedule_rebuild_tree(preferred_path)
