from __future__ import annotations

from f8pysdk.codec import dump_json
import json
import subprocess
import sys

from qtpy import QtCore, QtWidgets

from f8pystudio.ui.dialogs.schema_builder_dialog import (
    SchemaBuilderDialog,
    schema_from_json_obj,
    schema_to_json_obj,
    validate_schema_json_unknown_keys,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_schema_round_trip_supports_all_schema_kinds() -> None:
    samples = [
        {"type": "string", "default": "x", "enum": ["x", "y"]},
        {"type": "number", "minimum": 0, "maximum": 10},
        {"type": "array", "items": {"type": "integer"}},
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "scores": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        {"type": "any"},
    ]

    for obj in samples:
        schema = schema_from_json_obj(obj)
        out = schema_to_json_obj(schema)
        reparsed = schema_from_json_obj(out)
        assert dump_json(reparsed, mode="json") == dump_json(schema, mode="json")


def test_schema_round_trip_preserves_nested_object_array_object() -> None:
    nested = {
        "type": "object",
        "properties": {
            "pose": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["x"],
                },
            }
        },
        "required": ["pose"],
    }

    schema = schema_from_json_obj(nested)
    out = schema_to_json_obj(schema)
    assert out["type"] == "object"
    assert out["required"] == ["pose"]
    assert out["properties"]["pose"]["type"] == "array"
    assert out["properties"]["pose"]["items"]["type"] == "object"
    assert out["properties"]["pose"]["items"]["properties"]["x"]["type"] == "number"
    assert out["properties"]["pose"]["items"]["required"] == ["x"]


def test_validate_schema_json_unknown_keys_reports_full_paths() -> None:
    payload = {
        "type": "object",
        "properties": {
            "pose": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "foo": 1},
                    },
                },
            }
        },
        "unknownRoot": True,
    }

    unknown = validate_schema_json_unknown_keys(payload)
    assert "$.unknownRoot" in unknown
    assert "$.properties.pose.items.properties.x.foo" in unknown


def test_schema_builder_ui_edit_updates_json_view() -> None:
    _ensure_app()
    dlg = SchemaBuilderDialog(None, title="Schema", schema=schema_from_json_obj({"type": "string"}))

    root_index = dlg._find_tree_item_for_path(())
    assert root_index.isValid()
    dlg._tree.setCurrentIndex(root_index)

    assert dlg._type_combo is not None
    dlg._type_combo.setCurrentText("object")

    payload = json.loads(dlg._json_edit.toPlainText())
    assert payload["type"] == "object"
    assert payload["properties"] == {}


def test_schema_builder_json_edit_updates_ui_tree() -> None:
    _ensure_app()
    dlg = SchemaBuilderDialog(None, title="Schema", schema=schema_from_json_obj({"type": "any"}))

    payload = {
        "type": "object",
        "properties": {
            "pose": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"x": {"type": "number"}},
                },
            }
        },
    }
    dlg._json_edit.setPlainText(json.dumps(payload))
    dlg._on_json_debounce_timeout()

    assert dlg._is_schema_valid is True
    assert dlg._find_tree_item_for_path(("properties", "pose", "items", "properties", "x")).isValid()


def test_schema_builder_blocks_invalid_json_and_unknown_keys() -> None:
    _ensure_app()
    dlg = SchemaBuilderDialog(None, title="Schema", schema=schema_from_json_obj({"type": "any"}))

    dlg._json_edit.setPlainText("{")
    dlg._on_json_debounce_timeout()
    assert dlg._is_schema_valid is False

    ok_btn = dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok)
    assert ok_btn is not None
    assert ok_btn.isEnabled() is False

    dlg._json_edit.setPlainText(json.dumps({"type": "string", "oops": 1}))
    dlg._on_json_debounce_timeout()
    assert dlg._is_schema_valid is False
    assert "Unknown schema keys" in str(dlg._status.text())


def test_schema_builder_read_only_disables_edits_and_preserves_schema() -> None:
    _ensure_app()
    base = schema_from_json_obj({"type": "object", "properties": {"x": {"type": "number"}}})
    dlg = SchemaBuilderDialog(None, title="Schema", schema=base, read_only=True)

    assert dlg._json_edit.isReadOnly() is True
    assert hasattr(dlg._json_edit, "_f8_json_highlighter")
    assert hasattr(dlg._json_edit, "_f8_json_bracket_pair_controller")
    assert dlg._tree.isEnabled() is True
    assert dlg._form_host.isEnabled() is True
    assert dlg._required_table is not None
    assert dlg._required_table.isEnabled() is True
    required_item = dlg._required_table.item(0, 1)
    assert required_item is not None
    assert bool(required_item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable) is False
    assert dump_json(dlg.schema(), mode="json") == dump_json(base, mode="json")


def test_schema_builder_type_switch_does_not_crash() -> None:
    script = """
from qtpy import QtWidgets
from f8pystudio.ui.dialogs.schema_builder_dialog import SchemaBuilderDialog, schema_from_json_obj
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
dlg = SchemaBuilderDialog(None, title='Schema', schema=schema_from_json_obj({'type': 'string'}))
root_index = dlg._find_tree_item_for_path(())
if not root_index.isValid():
    raise RuntimeError('root index invalid')
dlg._tree.setCurrentIndex(root_index)
app.processEvents()
dlg._type_combo.setCurrentText('boolean')
app.processEvents()
dlg._type_combo.setCurrentText('number')
app.processEvents()
"""
    result = subprocess.run(
        [sys.executable, "-u", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"subprocess exited with {result.returncode}: {result.stderr}"
