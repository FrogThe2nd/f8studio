from __future__ import annotations

import json
from typing import Any, cast

from qtpy import QtCore, QtWidgets

from .schema_builder_common import _COMMON_KEYS, _SCHEMA_TYPE_VALUES


class SchemaBuilderFormMixin:
    def _clear_form(self) -> None:
        host = cast(Any, self)
        while host._form_layout.rowCount() > 0:
            host._form_layout.removeRow(0)
        host._type_combo = None
        host._title_edit = None
        host._description_edit = None
        host._default_edit = None
        host._examples_edit = None
        host._comment_edit = None
        host._enum_edit = None
        host._minimum_edit = None
        host._maximum_edit = None
        host._exclusive_minimum_edit = None
        host._exclusive_maximum_edit = None
        host._multiple_of_edit = None
        host._additional_props_check = None
        host._required_table = None

    def _render_form(self, path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        host._is_rebuilding_form = True
        try:
            host._clear_form()
            node = host._schema_at_path(path)
            if node is None:
                host._form_layout.addRow(QtWidgets.QLabel("Invalid selection"))
                return

            type_combo = QtWidgets.QComboBox(host._form_host)
            type_combo.addItems(list(_SCHEMA_TYPE_VALUES))
            type_combo.setCurrentText(host._schema_type(node))
            type_combo.currentTextChanged.connect(lambda text, _p=path: host._on_type_changed(_p, text))  # type: ignore[attr-defined]
            host._type_combo = type_combo
            host._form_layout.addRow("type", type_combo)

            title_edit = QtWidgets.QLineEdit(str(node.get("title") or ""), host._form_host)
            title_edit.editingFinished.connect(lambda _p=path, _e=title_edit: host._on_line_key_changed(_p, "title", _e))
            host._title_edit = title_edit
            host._form_layout.addRow("title", title_edit)

            description_edit = QtWidgets.QPlainTextEdit(str(node.get("description") or ""), host._form_host)
            description_edit.setFixedHeight(60)
            description_edit.textChanged.connect(
                lambda _p=path, _e=description_edit: host._on_plain_key_changed(_p, "description", _e)
            )
            host._description_edit = description_edit
            host._form_layout.addRow("description", description_edit)

            default_edit = QtWidgets.QPlainTextEdit(host._json_field_text(node.get("default")))
            default_edit.setFixedHeight(50)
            default_edit.textChanged.connect(
                lambda _p=path, _e=default_edit: host._on_json_field_changed(_p, "default", _e, allow_null=True)
            )
            host._default_edit = default_edit
            host._form_layout.addRow("default (JSON)", default_edit)

            examples_edit = QtWidgets.QPlainTextEdit(host._json_field_text(node.get("examples")))
            examples_edit.setFixedHeight(50)
            examples_edit.textChanged.connect(
                lambda _p=path, _e=examples_edit: host._on_json_field_changed(_p, "examples", _e, allow_null=True)
            )
            host._examples_edit = examples_edit
            host._form_layout.addRow("examples (JSON)", examples_edit)

            comment_edit = QtWidgets.QLineEdit(str(node.get("$comment") or ""), host._form_host)
            comment_edit.editingFinished.connect(
                lambda _p=path, _e=comment_edit: host._on_line_key_changed(_p, "$comment", _e)
            )
            host._comment_edit = comment_edit
            host._form_layout.addRow("$comment", comment_edit)

            node_type = host._schema_type(node)
            if node_type in {"string", "number", "integer", "boolean", "null"}:
                host._render_primitive_form(path, node)
            elif node_type == "object":
                host._render_object_form(path, node)
            elif node_type == "array":
                host._render_array_form(path, node)

            if host._read_only:
                host._apply_read_only_form_controls()
        finally:
            host._is_rebuilding_form = False

    def _apply_read_only_form_controls(self) -> None:
        host = cast(Any, self)
        for line_edit in host._form_host.findChildren(QtWidgets.QLineEdit):
            line_edit.setEnabled(True)
            line_edit.setReadOnly(True)

        for plain_edit in host._form_host.findChildren(QtWidgets.QPlainTextEdit):
            plain_edit.setEnabled(True)
            plain_edit.setReadOnly(True)

        for spin_box in host._form_host.findChildren(QtWidgets.QAbstractSpinBox):
            spin_box.setEnabled(True)
            spin_box.setReadOnly(True)
            spin_box.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)

        for combo_box in host._form_host.findChildren(QtWidgets.QComboBox):
            combo_box.setEnabled(False)

        for check_box in host._form_host.findChildren(QtWidgets.QCheckBox):
            check_box.setEnabled(False)

        for push_button in host._form_host.findChildren(QtWidgets.QPushButton):
            push_button.setEnabled(False)

        table = host._required_table
        if table is None:
            return
        table.setEnabled(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        read_only_flags = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        for row_index in range(table.rowCount()):
            for col_index in (0, 1):
                item = table.item(row_index, col_index)
                if item is not None:
                    item.setFlags(read_only_flags)

    def _render_primitive_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        host = cast(Any, self)
        enum_edit = QtWidgets.QLineEdit(host._enum_text(node.get("enum")), host._form_host)
        enum_edit.editingFinished.connect(lambda _p=path, _e=enum_edit: host._on_enum_changed(_p, _e))
        host._enum_edit = enum_edit
        host._form_layout.addRow("enum (comma)", enum_edit)

        minimum_edit = QtWidgets.QLineEdit(host._num_text(node.get("minimum")), host._form_host)
        maximum_edit = QtWidgets.QLineEdit(host._num_text(node.get("maximum")), host._form_host)
        exclusive_minimum_edit = QtWidgets.QLineEdit(host._num_text(node.get("exclusiveMinimum")), host._form_host)
        exclusive_maximum_edit = QtWidgets.QLineEdit(host._num_text(node.get("exclusiveMaximum")), host._form_host)
        multiple_of_edit = QtWidgets.QLineEdit(host._num_text(node.get("multipleOf")), host._form_host)

        minimum_edit.editingFinished.connect(
            lambda _p=path, _e=minimum_edit: host._on_number_field_changed(_p, "minimum", _e)
        )
        maximum_edit.editingFinished.connect(
            lambda _p=path, _e=maximum_edit: host._on_number_field_changed(_p, "maximum", _e)
        )
        exclusive_minimum_edit.editingFinished.connect(
            lambda _p=path, _e=exclusive_minimum_edit: host._on_number_field_changed(_p, "exclusiveMinimum", _e)
        )
        exclusive_maximum_edit.editingFinished.connect(
            lambda _p=path, _e=exclusive_maximum_edit: host._on_number_field_changed(_p, "exclusiveMaximum", _e)
        )
        multiple_of_edit.editingFinished.connect(
            lambda _p=path, _e=multiple_of_edit: host._on_number_field_changed(_p, "multipleOf", _e)
        )

        host._minimum_edit = minimum_edit
        host._maximum_edit = maximum_edit
        host._exclusive_minimum_edit = exclusive_minimum_edit
        host._exclusive_maximum_edit = exclusive_maximum_edit
        host._multiple_of_edit = multiple_of_edit

        host._form_layout.addRow("minimum", minimum_edit)
        host._form_layout.addRow("maximum", maximum_edit)
        host._form_layout.addRow("exclusiveMinimum", exclusive_minimum_edit)
        host._form_layout.addRow("exclusiveMaximum", exclusive_maximum_edit)
        host._form_layout.addRow("multipleOf", multiple_of_edit)

    def _render_object_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        host = cast(Any, self)
        props = node.get("properties")
        if not isinstance(props, dict):
            node["properties"] = {}
            props = node["properties"]

        required_values = node.get("required")
        required_set = set()
        if isinstance(required_values, list):
            required_set = {str(v) for v in required_values}

        host._required_table = QtWidgets.QTableWidget(host._form_host)
        host._required_table.setColumnCount(2)
        host._required_table.setHorizontalHeaderLabels(["Property", "Required"])
        host._required_table.horizontalHeader().setStretchLastSection(True)
        host._required_table.verticalHeader().setVisible(False)
        host._required_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        host._required_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        names = sorted(props.keys())
        host._required_table.setRowCount(len(names))
        for row_index, name in enumerate(names):
            name_item = QtWidgets.QTableWidgetItem(str(name))
            req_item = QtWidgets.QTableWidgetItem()
            req_item.setFlags(req_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            req_item.setCheckState(
                QtCore.Qt.CheckState.Checked if str(name) in required_set else QtCore.Qt.CheckState.Unchecked
            )
            host._required_table.setItem(row_index, 0, name_item)
            host._required_table.setItem(row_index, 1, req_item)

        table = host._required_table
        if table is not None:
            table.itemChanged.connect(lambda _item, _p=path, _t=table: host._on_required_table_changed(_p, _t))

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add Property", host._form_host)
        ren_btn = QtWidgets.QPushButton("Rename Property", host._form_host)
        del_btn = QtWidgets.QPushButton("Delete Property", host._form_host)
        add_btn.clicked.connect(lambda _checked=False, _p=path: host._on_add_property(_p))
        ren_btn.clicked.connect(lambda _checked=False, _p=path: host._on_rename_property(_p))
        del_btn.clicked.connect(lambda _checked=False, _p=path: host._on_delete_property(_p))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(ren_btn)
        btn_row.addWidget(del_btn)

        row_widget = QtWidgets.QWidget(host._form_host)
        row_widget.setLayout(btn_row)

        host._additional_props_check = QtWidgets.QCheckBox(host._form_host)
        host._additional_props_check.setChecked(bool(node.get("additionalProperties")))
        host._additional_props_check.toggled.connect(
            lambda checked, _p=path: host._on_additional_properties_changed(_p, checked)
        )  # type: ignore[attr-defined]

        host._form_layout.addRow("properties", host._required_table)
        host._form_layout.addRow("", row_widget)
        host._form_layout.addRow("additionalProperties", host._additional_props_check)

    def _render_array_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        host = cast(Any, self)
        if not isinstance(node.get("items"), dict):
            node["items"] = {"type": "any"}
        items_btn = QtWidgets.QPushButton("Go To items", host._form_host)
        items_btn.clicked.connect(lambda _checked=False, _p=path: host._on_go_to_items(_p))
        host._form_layout.addRow("items", items_btn)

    def _on_go_to_items(self, path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        index = host._find_tree_item_for_path(path + ("items",))
        if index.isValid():
            host._tree.setCurrentIndex(index)

    def _on_type_changed(self, path: tuple[str, ...], type_value: str) -> None:
        host = cast(Any, self)
        if host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        type_name = str(type_value or "any").strip().lower()
        if type_name not in _SCHEMA_TYPE_VALUES:
            return

        preserved: dict[str, Any] = {}
        for key in _COMMON_KEYS:
            if key in node:
                preserved[key] = node[key]

        if type_name in {"string", "number", "integer", "boolean", "null"}:
            node.clear()
            node.update(preserved)
            node["type"] = type_name
        elif type_name == "object":
            node.clear()
            node.update(preserved)
            node["type"] = "object"
            node["properties"] = {}
            node["required"] = []
            node["additionalProperties"] = False
        elif type_name == "array":
            node.clear()
            node.update(preserved)
            node["type"] = "array"
            node["items"] = {"type": "any"}
        else:
            node.clear()
            node.update(preserved)
            node["type"] = "any"

        host._sync_from_ui(path)

    def _on_line_key_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QLineEdit | None) -> None:
        host = cast(Any, self)
        if edit is None or host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if text:
            node[key] = text
        else:
            node.pop(key, None)
        host._sync_from_ui(path)

    def _on_plain_key_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QPlainTextEdit | None) -> None:
        host = cast(Any, self)
        if edit is None or host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        text = str(edit.toPlainText() or "").strip()
        if text:
            node[key] = text
        else:
            node.pop(key, None)
        host._sync_from_ui(path)

    def _on_json_field_changed(
        self,
        path: tuple[str, ...],
        key: str,
        edit: QtWidgets.QPlainTextEdit | None,
        *,
        allow_null: bool,
    ) -> None:
        host = cast(Any, self)
        if edit is None or host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        raw = str(edit.toPlainText() or "").strip()
        if not raw:
            node.pop(key, None)
            host._sync_from_ui(path)
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if allow_null and raw.lower() == "null":
                node[key] = None
                host._sync_from_ui(path)
                return
            host._set_status_invalid(f"Field '{key}' must contain valid JSON")
            return
        node[key] = parsed
        host._sync_from_ui(path)

    def _on_enum_changed(self, path: tuple[str, ...], edit: QtWidgets.QLineEdit | None) -> None:
        host = cast(Any, self)
        if edit is None or host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if not text:
            node.pop("enum", None)
            host._sync_from_ui(path)
            return
        values = [segment.strip() for segment in text.split(",") if segment.strip()]
        node["enum"] = values
        host._sync_from_ui(path)

    def _on_number_field_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QLineEdit | None) -> None:
        host = cast(Any, self)
        if edit is None or host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if not text:
            node.pop(key, None)
            host._sync_from_ui(path)
            return
        try:
            value = float(text)
        except ValueError:
            host._set_status_invalid(f"Field '{key}' must be numeric")
            return
        node[key] = value
        host._sync_from_ui(path)

    def _on_required_table_changed(self, path: tuple[str, ...], table: QtWidgets.QTableWidget | None) -> None:
        host = cast(Any, self)
        if host._is_rebuilding_form:
            return
        node = host._schema_at_path(path)
        if node is None or table is None:
            return
        required: list[str] = []
        for row_index in range(table.rowCount()):
            name_item = table.item(row_index, 0)
            req_item = table.item(row_index, 1)
            if name_item is None or req_item is None:
                continue
            name = str(name_item.text() or "").strip()
            if not name:
                continue
            if req_item.checkState() == QtCore.Qt.CheckState.Checked:
                required.append(name)
        node["required"] = required
        host._sync_from_ui(path)

    def _on_additional_properties_changed(self, path: tuple[str, ...], checked: bool) -> None:
        host = cast(Any, self)
        node = host._schema_at_path(path)
        if node is None:
            return
        node["additionalProperties"] = bool(checked)
        host._sync_from_ui(path)

    def _prompt_property_name(self, *, title: str, initial: str = "") -> str | None:
        host = cast(Any, self)
        value, ok = QtWidgets.QInputDialog.getText(host, title, "Property name", text=initial)
        if not ok:
            return None
        name = str(value or "").strip()
        if not name:
            return None
        return name

    def _selected_property_name(self) -> str:
        host = cast(Any, self)
        table = host._required_table
        if table is None:
            return ""
        row = int(table.currentRow())
        if row < 0:
            return ""
        item = table.item(row, 0)
        if item is None:
            return ""
        return str(item.text() or "").strip()

    def _on_add_property(self, path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        node = host._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            node["properties"] = {}
            properties = node["properties"]

        new_name = host._prompt_property_name(title="Add Property")
        if not new_name:
            return
        if new_name in properties:
            host._set_status_invalid(f"Property already exists: {new_name}")
            return
        properties[new_name] = {"type": "any"}
        required = node.get("required")
        if not isinstance(required, list):
            node["required"] = []
        host._sync_from_ui(path + ("properties", new_name))

    def _on_rename_property(self, path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        node = host._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return
        old_name = host._selected_property_name()
        if not old_name:
            return
        new_name = host._prompt_property_name(title="Rename Property", initial=old_name)
        if not new_name or new_name == old_name:
            return
        if new_name in properties:
            host._set_status_invalid(f"Property already exists: {new_name}")
            return

        properties[new_name] = properties.pop(old_name)

        required = node.get("required")
        if isinstance(required, list):
            node["required"] = [new_name if str(item) == old_name else str(item) for item in required]

        host._sync_from_ui(path + ("properties", new_name))

    def _on_delete_property(self, path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        node = host._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return

        name = host._selected_property_name()
        if not name or name not in properties:
            return

        properties.pop(name, None)
        required = node.get("required")
        if isinstance(required, list):
            node["required"] = [str(item) for item in required if str(item) != name]

        host._sync_from_ui(path)

    @staticmethod
    def _json_field_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _enum_text(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        return ", ".join(str(item) for item in value)

    @staticmethod
    def _num_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(float(value))
        except (TypeError, ValueError):
            return ""
