from __future__ import annotations

import json
import logging
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk import F8DataTypeSchema

logger = logging.getLogger(__name__)

_SCHEMA_TYPE_VALUES: tuple[str, ...] = (
    "string",
    "number",
    "integer",
    "boolean",
    "null",
    "object",
    "array",
    "any",
)

_COMMON_KEYS: set[str] = {
    "type",
    "title",
    "description",
    "default",
    "examples",
    "$comment",
}

_PRIMITIVE_KEYS: set[str] = _COMMON_KEYS | {
    "enum",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
}

_OBJECT_KEYS: set[str] = _COMMON_KEYS | {
    "properties",
    "required",
    "additionalProperties",
}

_ARRAY_KEYS: set[str] = _COMMON_KEYS | {
    "items",
}

_ANY_KEYS: set[str] = set(_COMMON_KEYS)

_PATH_ROLE = int(QtCore.Qt.UserRole) + 1


def _encode_path(path: tuple[str, ...]) -> str:
    return json.dumps(list(path), ensure_ascii=False)


def _decode_path(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    out: list[str] = []
    for item in data:
        out.append(str(item))
    return tuple(out)


def schema_to_json_obj(schema: F8DataTypeSchema) -> dict[str, Any]:
    obj = schema.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(obj, dict):
        return obj
    raise ValueError("schema must serialize to a JSON object")


def schema_from_json_obj(obj: Any) -> F8DataTypeSchema:
    return F8DataTypeSchema.model_validate(obj)


def validate_schema_json_unknown_keys(obj: Any) -> list[str]:
    unknown: list[str] = []

    def _join(path: str, segment: str) -> str:
        if segment.startswith("["):
            return path + segment
        return path + "." + segment

    def _allowed_keys(schema_type: str) -> set[str]:
        if schema_type in {"string", "number", "integer", "boolean", "null"}:
            return _PRIMITIVE_KEYS
        if schema_type == "object":
            return _OBJECT_KEYS
        if schema_type == "array":
            return _ARRAY_KEYS
        if schema_type == "any":
            return _ANY_KEYS
        return _COMMON_KEYS

    def _visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return

        schema_type = str(node.get("type") or "").strip().lower()
        allowed = _allowed_keys(schema_type)
        for key in node.keys():
            if key not in allowed:
                unknown.append(_join(path, str(key)))

        if schema_type == "object":
            properties = node.get("properties")
            if isinstance(properties, dict):
                for prop_name, prop_schema in properties.items():
                    _visit(prop_schema, _join(_join(path, "properties"), str(prop_name)))
            return

        if schema_type == "array":
            _visit(node.get("items"), _join(path, "items"))
            return

    _visit(obj, "$")
    return sorted(unknown)


class SchemaBuilderDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str, schema: F8DataTypeSchema, read_only: bool = False):
        super().__init__(parent)
        self.setWindowTitle(str(title or "Schema Builder"))

        self._read_only = bool(read_only)
        self._schema_obj: dict[str, Any] = schema_to_json_obj(schema)
        self._schema: F8DataTypeSchema = schema
        self._is_schema_valid = True

        self._is_updating_from_ui = False
        self._is_updating_from_json = False
        self._is_rebuilding_form = False

        self._tabs = QtWidgets.QTabWidget(self)
        self._ui_tab = QtWidgets.QWidget(self)
        self._json_tab = QtWidgets.QWidget(self)

        self._tree = QtWidgets.QTreeView(self._ui_tab)
        self._tree_model = QtGui.QStandardItemModel(self._tree)
        self._tree_model.setHorizontalHeaderLabels(["Path", "Type"])
        self._tree.setModel(self._tree_model)
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._tree.setUniformRowHeights(True)

        self._form_host = QtWidgets.QWidget(self._ui_tab)
        self._form_layout = QtWidgets.QFormLayout(self._form_host)
        self._form_layout.setContentsMargins(8, 8, 8, 8)
        self._form_layout.setSpacing(6)

        self._json_edit = QtWidgets.QPlainTextEdit(self._json_tab)
        self._json_edit.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))

        self._status = QtWidgets.QLabel(self)

        self._json_timer = QtCore.QTimer(self)
        self._json_timer.setSingleShot(True)
        self._json_timer.setInterval(250)
        self._rebuild_timer = QtCore.QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(0)
        self._pending_rebuild_path: tuple[str, ...] = ()

        self._buttons = self._build_buttons()

        self._type_combo: QtWidgets.QComboBox | None = None
        self._title_edit: QtWidgets.QLineEdit | None = None
        self._description_edit: QtWidgets.QPlainTextEdit | None = None
        self._default_edit: QtWidgets.QPlainTextEdit | None = None
        self._examples_edit: QtWidgets.QPlainTextEdit | None = None
        self._comment_edit: QtWidgets.QLineEdit | None = None
        self._enum_edit: QtWidgets.QLineEdit | None = None
        self._minimum_edit: QtWidgets.QLineEdit | None = None
        self._maximum_edit: QtWidgets.QLineEdit | None = None
        self._exclusive_minimum_edit: QtWidgets.QLineEdit | None = None
        self._exclusive_maximum_edit: QtWidgets.QLineEdit | None = None
        self._multiple_of_edit: QtWidgets.QLineEdit | None = None
        self._additional_props_check: QtWidgets.QCheckBox | None = None
        self._required_table: QtWidgets.QTableWidget | None = None

        self._build_ui_layout()
        self._wire_signals()

        self._json_edit.setPlainText(json.dumps(self._schema_obj, ensure_ascii=False, indent=2))
        self._rebuild_tree(select_path=())
        self._set_status_valid("Valid schema")

        if self._read_only:
            self._form_host.setEnabled(False)
            self._json_edit.setReadOnly(True)

        self.resize(980, 700)

    def schema(self) -> F8DataTypeSchema:
        return self._schema

    def _build_buttons(self) -> QtWidgets.QDialogButtonBox:
        if self._read_only:
            box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            box.rejected.connect(self.reject)
            box.accepted.connect(self.reject)
            return box
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        return box

    def _build_ui_layout(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self._ui_tab)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._tree)

        form_scroll = QtWidgets.QScrollArea(self._ui_tab)
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        form_scroll.setWidget(self._form_host)
        splitter.addWidget(form_scroll)
        splitter.setStretchFactor(1, 1)

        ui_layout = QtWidgets.QVBoxLayout(self._ui_tab)
        ui_layout.setContentsMargins(0, 0, 0, 0)
        ui_layout.addWidget(splitter)

        json_layout = QtWidgets.QVBoxLayout(self._json_tab)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.addWidget(self._json_edit)

        self._tabs.addTab(self._ui_tab, "UI")
        self._tabs.addTab(self._json_tab, "JSON")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._buttons)

    def _wire_signals(self) -> None:
        selection_model = self._tree.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(self._on_tree_selection_changed)
        self._json_edit.textChanged.connect(self._on_json_text_changed)
        self._json_timer.timeout.connect(self._on_json_debounce_timeout)
        self._rebuild_timer.timeout.connect(self._on_rebuild_timeout)

    def _on_accept(self) -> None:
        if not self._is_schema_valid:
            return
        self.accept()

    def _on_json_text_changed(self) -> None:
        if self._is_updating_from_ui:
            return
        self._json_timer.start()

    def _on_json_debounce_timeout(self) -> None:
        if self._is_updating_from_ui:
            return
        text = str(self._json_edit.toPlainText() or "").strip()
        if not text:
            self._set_status_invalid("JSON is empty")
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            self._set_status_invalid(f"Invalid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})")
            return
        if not isinstance(obj, dict):
            self._set_status_invalid("Schema root must be a JSON object")
            return

        unknown = validate_schema_json_unknown_keys(obj)
        if unknown:
            self._set_status_invalid("Unknown schema keys: " + ", ".join(unknown[:5]))
            return

        try:
            schema = schema_from_json_obj(obj)
        except Exception as exc:
            self._set_status_invalid(f"Schema validation failed: {exc}")
            return

        self._schema = schema
        self._schema_obj = schema_to_json_obj(schema)
        self._set_status_valid("Valid schema")

        current_path = self._current_path()
        self._is_updating_from_json = True
        try:
            self._rebuild_tree(select_path=current_path)
        finally:
            self._is_updating_from_json = False

    def _set_status_valid(self, message: str) -> None:
        self._is_schema_valid = True
        self._status.setText(str(message))
        self._status.setStyleSheet("color: #3c9f40;")
        ok = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(not self._read_only)

    def _set_status_invalid(self, message: str) -> None:
        self._is_schema_valid = False
        self._status.setText(str(message))
        self._status.setStyleSheet("color: #c54141;")
        ok = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(False)

    def _schema_type(self, schema_obj: dict[str, Any]) -> str:
        return str(schema_obj.get("type") or "any").strip().lower()

    def _current_path(self) -> tuple[str, ...]:
        current = self._tree.currentIndex()
        if not current.isValid():
            return ()
        path_index = current.siblingAtColumn(0)
        if not path_index.isValid():
            return ()
        return _decode_path(path_index.data(_PATH_ROLE))

    def _schema_at_path(self, path: tuple[str, ...]) -> dict[str, Any] | None:
        node: Any = self._schema_obj
        idx = 0
        while idx < len(path):
            token = path[idx]
            if not isinstance(node, dict):
                return None
            if token == "properties":
                props = node.get("properties")
                if not isinstance(props, dict):
                    return None
                idx += 1
                if idx >= len(path):
                    return None
                prop_name = path[idx]
                node = props.get(prop_name)
            elif token == "items":
                node = node.get("items")
            else:
                return None
            idx += 1
        if isinstance(node, dict):
            return node
        return None

    def _rebuild_tree(self, *, select_path: tuple[str, ...] | None = None) -> None:
        if select_path is None:
            select_path = ()

        selected_path: tuple[str, ...] = ()
        with QtCore.QSignalBlocker(self._tree):
            self._tree_model.removeRows(0, self._tree_model.rowCount())
            path_to_index: dict[tuple[str, ...], QtCore.QModelIndex] = {}

            root_path_item = QtGui.QStandardItem("$")
            root_type_item = QtGui.QStandardItem(self._schema_type(self._schema_obj))
            root_path_item.setEditable(False)
            root_type_item.setEditable(False)
            root_path_item.setData(_encode_path(()), _PATH_ROLE)
            self._tree_model.appendRow([root_path_item, root_type_item])
            path_to_index[()] = root_path_item.index()

            def _add_children(parent_item: QtGui.QStandardItem, path: tuple[str, ...], node: dict[str, Any]) -> None:
                node_type = self._schema_type(node)
                if node_type == "object":
                    properties = node.get("properties")
                    if not isinstance(properties, dict):
                        return
                    for prop_name in sorted(properties.keys()):
                        child_schema = properties.get(prop_name)
                        if not isinstance(child_schema, dict):
                            continue
                        child_path = path + ("properties", str(prop_name))
                        child_path_item = QtGui.QStandardItem(f".{prop_name}")
                        child_type_item = QtGui.QStandardItem(self._schema_type(child_schema))
                        child_path_item.setEditable(False)
                        child_type_item.setEditable(False)
                        child_path_item.setData(_encode_path(child_path), _PATH_ROLE)
                        parent_item.appendRow([child_path_item, child_type_item])
                        path_to_index[child_path] = child_path_item.index()
                        _add_children(child_path_item, child_path, child_schema)
                    return

                if node_type == "array":
                    items = node.get("items")
                    if not isinstance(items, dict):
                        return
                    child_path = path + ("items",)
                    child_path_item = QtGui.QStandardItem("[items]")
                    child_type_item = QtGui.QStandardItem(self._schema_type(items))
                    child_path_item.setEditable(False)
                    child_type_item.setEditable(False)
                    child_path_item.setData(_encode_path(child_path), _PATH_ROLE)
                    parent_item.appendRow([child_path_item, child_type_item])
                    path_to_index[child_path] = child_path_item.index()
                    _add_children(child_path_item, child_path, items)

            _add_children(root_path_item, (), self._schema_obj)
            self._tree.expandAll()
            self._tree.resizeColumnToContents(0)

            selected_index = path_to_index.get(select_path)
            if selected_index is None:
                selected_index = path_to_index.get((), QtCore.QModelIndex())
            if selected_index.isValid():
                self._tree.setCurrentIndex(selected_index)
                selected_path = _decode_path(selected_index.data(_PATH_ROLE))
            else:
                selected_path = ()

        self._render_form(selected_path)

    def _find_tree_item_for_path(self, path: tuple[str, ...]) -> QtCore.QModelIndex:
        def _walk(parent_index: QtCore.QModelIndex) -> QtCore.QModelIndex:
            rows = self._tree_model.rowCount(parent_index)
            for row in range(rows):
                index = self._tree_model.index(row, 0, parent_index)
                if not index.isValid():
                    continue
                if _decode_path(index.data(_PATH_ROLE)) == path:
                    return index
                child_match = _walk(index)
                if child_match.isValid():
                    return child_match
            return QtCore.QModelIndex()

        return _walk(QtCore.QModelIndex())

    def _on_tree_selection_changed(self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex) -> None:
        del _previous
        if not current.isValid():
            return
        path_index = current.siblingAtColumn(0)
        if not path_index.isValid():
            return
        self._render_form(_decode_path(path_index.data(_PATH_ROLE)))

    def _schedule_rebuild_tree(self, preferred_path: tuple[str, ...]) -> None:
        self._pending_rebuild_path = tuple(preferred_path)
        self._rebuild_timer.start()

    def _on_rebuild_timeout(self) -> None:
        self._rebuild_tree(select_path=self._pending_rebuild_path)

    def _clear_form(self) -> None:
        while self._form_layout.rowCount() > 0:
            self._form_layout.removeRow(0)
        self._type_combo = None
        self._title_edit = None
        self._description_edit = None
        self._default_edit = None
        self._examples_edit = None
        self._comment_edit = None
        self._enum_edit = None
        self._minimum_edit = None
        self._maximum_edit = None
        self._exclusive_minimum_edit = None
        self._exclusive_maximum_edit = None
        self._multiple_of_edit = None
        self._additional_props_check = None
        self._required_table = None

    def _render_form(self, path: tuple[str, ...]) -> None:
        self._is_rebuilding_form = True
        try:
            self._clear_form()
            node = self._schema_at_path(path)
            if node is None:
                self._form_layout.addRow(QtWidgets.QLabel("Invalid selection"))
                return

            type_combo = QtWidgets.QComboBox(self._form_host)
            type_combo.addItems(list(_SCHEMA_TYPE_VALUES))
            type_combo.setCurrentText(self._schema_type(node))
            type_combo.currentTextChanged.connect(lambda text, _p=path: self._on_type_changed(_p, text))  # type: ignore[attr-defined]
            self._type_combo = type_combo
            self._form_layout.addRow("type", type_combo)

            title_edit = QtWidgets.QLineEdit(str(node.get("title") or ""), self._form_host)
            title_edit.editingFinished.connect(lambda _p=path, _e=title_edit: self._on_line_key_changed(_p, "title", _e))
            self._title_edit = title_edit
            self._form_layout.addRow("title", title_edit)

            description_edit = QtWidgets.QPlainTextEdit(str(node.get("description") or ""), self._form_host)
            description_edit.setFixedHeight(60)
            description_edit.textChanged.connect(
                lambda _p=path, _e=description_edit: self._on_plain_key_changed(_p, "description", _e)
            )
            self._description_edit = description_edit
            self._form_layout.addRow("description", description_edit)

            default_edit = QtWidgets.QPlainTextEdit(self._json_field_text(node.get("default")))
            default_edit.setFixedHeight(50)
            default_edit.textChanged.connect(
                lambda _p=path, _e=default_edit: self._on_json_field_changed(_p, "default", _e, allow_null=True)
            )
            self._default_edit = default_edit
            self._form_layout.addRow("default (JSON)", default_edit)

            examples_edit = QtWidgets.QPlainTextEdit(self._json_field_text(node.get("examples")))
            examples_edit.setFixedHeight(50)
            examples_edit.textChanged.connect(
                lambda _p=path, _e=examples_edit: self._on_json_field_changed(_p, "examples", _e, allow_null=True)
            )
            self._examples_edit = examples_edit
            self._form_layout.addRow("examples (JSON)", examples_edit)

            comment_edit = QtWidgets.QLineEdit(str(node.get("$comment") or ""), self._form_host)
            comment_edit.editingFinished.connect(lambda _p=path, _e=comment_edit: self._on_line_key_changed(_p, "$comment", _e))
            self._comment_edit = comment_edit
            self._form_layout.addRow("$comment", comment_edit)

            node_type = self._schema_type(node)
            if node_type in {"string", "number", "integer", "boolean", "null"}:
                self._render_primitive_form(path, node)
            elif node_type == "object":
                self._render_object_form(path, node)
            elif node_type == "array":
                self._render_array_form(path, node)

            if self._read_only:
                self._form_host.setEnabled(False)
        finally:
            self._is_rebuilding_form = False

    def _render_primitive_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        enum_edit = QtWidgets.QLineEdit(self._enum_text(node.get("enum")), self._form_host)
        enum_edit.editingFinished.connect(lambda _p=path, _e=enum_edit: self._on_enum_changed(_p, _e))
        self._enum_edit = enum_edit
        self._form_layout.addRow("enum (comma)", enum_edit)

        minimum_edit = QtWidgets.QLineEdit(self._num_text(node.get("minimum")), self._form_host)
        maximum_edit = QtWidgets.QLineEdit(self._num_text(node.get("maximum")), self._form_host)
        exclusive_minimum_edit = QtWidgets.QLineEdit(self._num_text(node.get("exclusiveMinimum")), self._form_host)
        exclusive_maximum_edit = QtWidgets.QLineEdit(self._num_text(node.get("exclusiveMaximum")), self._form_host)
        multiple_of_edit = QtWidgets.QLineEdit(self._num_text(node.get("multipleOf")), self._form_host)

        minimum_edit.editingFinished.connect(
            lambda _p=path, _e=minimum_edit: self._on_number_field_changed(_p, "minimum", _e)
        )
        maximum_edit.editingFinished.connect(
            lambda _p=path, _e=maximum_edit: self._on_number_field_changed(_p, "maximum", _e)
        )
        exclusive_minimum_edit.editingFinished.connect(
            lambda _p=path, _e=exclusive_minimum_edit: self._on_number_field_changed(_p, "exclusiveMinimum", _e)
        )
        exclusive_maximum_edit.editingFinished.connect(
            lambda _p=path, _e=exclusive_maximum_edit: self._on_number_field_changed(_p, "exclusiveMaximum", _e)
        )
        multiple_of_edit.editingFinished.connect(
            lambda _p=path, _e=multiple_of_edit: self._on_number_field_changed(_p, "multipleOf", _e)
        )

        self._minimum_edit = minimum_edit
        self._maximum_edit = maximum_edit
        self._exclusive_minimum_edit = exclusive_minimum_edit
        self._exclusive_maximum_edit = exclusive_maximum_edit
        self._multiple_of_edit = multiple_of_edit

        self._form_layout.addRow("minimum", minimum_edit)
        self._form_layout.addRow("maximum", maximum_edit)
        self._form_layout.addRow("exclusiveMinimum", exclusive_minimum_edit)
        self._form_layout.addRow("exclusiveMaximum", exclusive_maximum_edit)
        self._form_layout.addRow("multipleOf", multiple_of_edit)

    def _render_object_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        props = node.get("properties")
        if not isinstance(props, dict):
            node["properties"] = {}
            props = node["properties"]

        required_values = node.get("required")
        required_set = set()
        if isinstance(required_values, list):
            required_set = {str(v) for v in required_values}

        self._required_table = QtWidgets.QTableWidget(self._form_host)
        self._required_table.setColumnCount(2)
        self._required_table.setHorizontalHeaderLabels(["Property", "Required"])
        self._required_table.horizontalHeader().setStretchLastSection(True)
        self._required_table.verticalHeader().setVisible(False)
        self._required_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._required_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        names = sorted(props.keys())
        self._required_table.setRowCount(len(names))
        for row_index, name in enumerate(names):
            name_item = QtWidgets.QTableWidgetItem(str(name))
            req_item = QtWidgets.QTableWidgetItem()
            req_item.setFlags(req_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            req_item.setCheckState(QtCore.Qt.Checked if str(name) in required_set else QtCore.Qt.Unchecked)
            self._required_table.setItem(row_index, 0, name_item)
            self._required_table.setItem(row_index, 1, req_item)

        table = self._required_table
        if table is not None:
            table.itemChanged.connect(lambda _item, _p=path, _t=table: self._on_required_table_changed(_p, _t))

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add Property", self._form_host)
        ren_btn = QtWidgets.QPushButton("Rename Property", self._form_host)
        del_btn = QtWidgets.QPushButton("Delete Property", self._form_host)
        add_btn.clicked.connect(lambda _checked=False, _p=path: self._on_add_property(_p))
        ren_btn.clicked.connect(lambda _checked=False, _p=path: self._on_rename_property(_p))
        del_btn.clicked.connect(lambda _checked=False, _p=path: self._on_delete_property(_p))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(ren_btn)
        btn_row.addWidget(del_btn)

        row_widget = QtWidgets.QWidget(self._form_host)
        row_widget.setLayout(btn_row)

        self._additional_props_check = QtWidgets.QCheckBox(self._form_host)
        self._additional_props_check.setChecked(bool(node.get("additionalProperties")))
        self._additional_props_check.toggled.connect(lambda checked, _p=path: self._on_additional_properties_changed(_p, checked))  # type: ignore[attr-defined]

        self._form_layout.addRow("properties", self._required_table)
        self._form_layout.addRow("", row_widget)
        self._form_layout.addRow("additionalProperties", self._additional_props_check)

    def _render_array_form(self, path: tuple[str, ...], node: dict[str, Any]) -> None:
        if not isinstance(node.get("items"), dict):
            node["items"] = {"type": "any"}
        items_btn = QtWidgets.QPushButton("Go To items", self._form_host)
        items_btn.clicked.connect(lambda _checked=False, _p=path: self._on_go_to_items(_p))
        self._form_layout.addRow("items", items_btn)

    def _on_go_to_items(self, path: tuple[str, ...]) -> None:
        index = self._find_tree_item_for_path(path + ("items",))
        if index.isValid():
            self._tree.setCurrentIndex(index)

    def _on_type_changed(self, path: tuple[str, ...], type_value: str) -> None:
        if self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
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

        self._sync_from_ui(path)

    def _on_line_key_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QLineEdit | None) -> None:
        if edit is None or self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if text:
            node[key] = text
        else:
            node.pop(key, None)
        self._sync_from_ui(path)

    def _on_plain_key_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QPlainTextEdit | None) -> None:
        if edit is None or self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        text = str(edit.toPlainText() or "").strip()
        if text:
            node[key] = text
        else:
            node.pop(key, None)
        self._sync_from_ui(path)

    def _on_json_field_changed(
        self,
        path: tuple[str, ...],
        key: str,
        edit: QtWidgets.QPlainTextEdit | None,
        *,
        allow_null: bool,
    ) -> None:
        if edit is None or self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        raw = str(edit.toPlainText() or "").strip()
        if not raw:
            node.pop(key, None)
            self._sync_from_ui(path)
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if allow_null and raw.lower() == "null":
                node[key] = None
                self._sync_from_ui(path)
                return
            self._set_status_invalid(f"Field '{key}' must contain valid JSON")
            return
        node[key] = parsed
        self._sync_from_ui(path)

    def _on_enum_changed(self, path: tuple[str, ...], edit: QtWidgets.QLineEdit | None) -> None:
        if edit is None or self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if not text:
            node.pop("enum", None)
            self._sync_from_ui(path)
            return
        values = [segment.strip() for segment in text.split(",") if segment.strip()]
        node["enum"] = values
        self._sync_from_ui(path)

    def _on_number_field_changed(self, path: tuple[str, ...], key: str, edit: QtWidgets.QLineEdit | None) -> None:
        if edit is None or self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        text = str(edit.text() or "").strip()
        if not text:
            node.pop(key, None)
            self._sync_from_ui(path)
            return
        try:
            value = float(text)
        except ValueError:
            self._set_status_invalid(f"Field '{key}' must be numeric")
            return
        node[key] = value
        self._sync_from_ui(path)

    def _on_required_table_changed(self, path: tuple[str, ...], table: QtWidgets.QTableWidget | None) -> None:
        if self._is_rebuilding_form:
            return
        node = self._schema_at_path(path)
        if node is None:
            return
        if table is None:
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
            if req_item.checkState() == QtCore.Qt.Checked:
                required.append(name)
        node["required"] = required
        self._sync_from_ui(path)

    def _on_additional_properties_changed(self, path: tuple[str, ...], checked: bool) -> None:
        node = self._schema_at_path(path)
        if node is None:
            return
        node["additionalProperties"] = bool(checked)
        self._sync_from_ui(path)

    def _prompt_property_name(self, *, title: str, initial: str = "") -> str | None:
        value, ok = QtWidgets.QInputDialog.getText(self, title, "Property name", text=initial)
        if not ok:
            return None
        name = str(value or "").strip()
        if not name:
            return None
        return name

    def _selected_property_name(self) -> str:
        table = self._required_table
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
        node = self._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            node["properties"] = {}
            properties = node["properties"]

        new_name = self._prompt_property_name(title="Add Property")
        if not new_name:
            return
        if new_name in properties:
            self._set_status_invalid(f"Property already exists: {new_name}")
            return
        properties[new_name] = {"type": "any"}
        required = node.get("required")
        if not isinstance(required, list):
            node["required"] = []
        self._sync_from_ui(path + ("properties", new_name))

    def _on_rename_property(self, path: tuple[str, ...]) -> None:
        node = self._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return
        old_name = self._selected_property_name()
        if not old_name:
            return
        new_name = self._prompt_property_name(title="Rename Property", initial=old_name)
        if not new_name or new_name == old_name:
            return
        if new_name in properties:
            self._set_status_invalid(f"Property already exists: {new_name}")
            return

        properties[new_name] = properties.pop(old_name)

        required = node.get("required")
        if isinstance(required, list):
            node["required"] = [new_name if str(item) == old_name else str(item) for item in required]

        self._sync_from_ui(path + ("properties", new_name))

    def _on_delete_property(self, path: tuple[str, ...]) -> None:
        node = self._schema_at_path(path)
        if node is None:
            return
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return

        name = self._selected_property_name()
        if not name:
            return
        if name not in properties:
            return

        properties.pop(name, None)
        required = node.get("required")
        if isinstance(required, list):
            node["required"] = [str(item) for item in required if str(item) != name]

        self._sync_from_ui(path)

    def _sync_from_ui(self, preferred_path: tuple[str, ...]) -> None:
        if self._is_updating_from_json:
            return

        unknown = validate_schema_json_unknown_keys(self._schema_obj)
        if unknown:
            self._set_status_invalid("Unknown schema keys: " + ", ".join(unknown[:5]))
            return

        try:
            schema = schema_from_json_obj(self._schema_obj)
        except Exception as exc:
            self._set_status_invalid(f"Schema validation failed: {exc}")
            return

        self._schema = schema
        self._schema_obj = schema_to_json_obj(schema)

        self._is_updating_from_ui = True
        try:
            self._json_edit.setPlainText(json.dumps(self._schema_obj, ensure_ascii=False, indent=2))
        finally:
            self._is_updating_from_ui = False

        self._set_status_valid("Valid schema")
        # Defer rebuild to avoid destroying sender widgets during active signal callbacks.
        self._schedule_rebuild_tree(preferred_path)

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
