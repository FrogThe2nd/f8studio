from __future__ import annotations

import json
import logging
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk.specs import F8DataTypeSchema

from ..support.json_text_editor import attach_json_enhancements
from .schema_builder_common import (
    schema_from_json_obj,
    schema_to_json_obj,
    validate_schema_json_unknown_keys,
)
from .schema_builder_form_mixin import SchemaBuilderFormMixin
from .schema_builder_sync_mixin import SchemaBuilderSyncMixin
from .schema_builder_tree_mixin import SchemaBuilderTreeMixin

logger = logging.getLogger(__name__)

class SchemaBuilderDialog(
    SchemaBuilderFormMixin,
    SchemaBuilderSyncMixin,
    SchemaBuilderTreeMixin,
    QtWidgets.QDialog,
):
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
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setUniformRowHeights(True)

        self._form_host = QtWidgets.QWidget(self._ui_tab)
        self._form_layout = QtWidgets.QFormLayout(self._form_host)
        self._form_layout.setContentsMargins(8, 8, 8, 8)
        self._form_layout.setSpacing(6)

        self._json_edit = QtWidgets.QPlainTextEdit(self._json_tab)
        self._json_edit.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        attach_json_enhancements(self._json_edit, read_only=self._read_only)

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

        self.resize(980, 700)

    def schema(self) -> F8DataTypeSchema:
        return self._schema

    def _build_buttons(self) -> QtWidgets.QDialogButtonBox:
        if self._read_only:
            box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
            box.rejected.connect(self.reject)
            box.accepted.connect(self.reject)
            return box
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        return box

    def _build_ui_layout(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self._ui_tab)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._tree)

        form_scroll = QtWidgets.QScrollArea(self._ui_tab)
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
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
