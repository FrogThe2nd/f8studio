from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtCore, QtGui, QtWidgets

from ...nodegraph.node_graph import F8StudioGraph
from ...nodegraph.layers import BASE_LAYER_ID, F8LayerDef
from ...ui.support.ui_icons import StudioIcon, icon_for


@dataclass(frozen=True)
class _LayerDialogResult:
    label: str
    description: str
    color: str
    default_visible: bool


class _LayerEditDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        title: str,
        initial_label: str,
        initial_description: str,
        initial_color: str,
        initial_default_visible: bool,
        is_base: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 240)
        self._color = str(initial_color or "#64748B")

        form = QtWidgets.QFormLayout()
        self._label_edit = QtWidgets.QLineEdit(str(initial_label or ""), self)
        self._description_edit = QtWidgets.QPlainTextEdit(self)
        self._description_edit.setPlainText(str(initial_description or ""))
        self._default_visible = QtWidgets.QCheckBox("Visible by default when the session opens", self)
        self._default_visible.setChecked(bool(initial_default_visible))
        self._color_button = QtWidgets.QPushButton(self)
        self._color_button.clicked.connect(self._pick_color)  # type: ignore[attr-defined]
        self._refresh_color_button()

        form.addRow("Label", self._label_edit)
        form.addRow("Description", self._description_edit)
        form.addRow("Color", self._color_button)
        form.addRow("", self._default_visible)

        if is_base:
            self._label_edit.setText("Base")
            self._label_edit.setDisabled(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        buttons.accepted.connect(self.accept)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _refresh_color_button(self) -> None:
        self._color_button.setText(str(self._color))
        self._color_button.setStyleSheet(
            "QPushButton {"
            f"background: {self._color};"
            "color: black;"
            "padding: 6px 10px;"
            "border-radius: 6px;"
            "}"
        )

    def _pick_color(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color), self, "Choose Layer Color")
        if not chosen.isValid():
            return
        self._color = str(chosen.name()).upper()
        self._refresh_color_button()

    def result_value(self) -> _LayerDialogResult:
        return _LayerDialogResult(
            label=str(self._label_edit.text() or "").strip() or "Layer",
            description=str(self._description_edit.toPlainText() or "").strip(),
            color=str(self._color or "#64748B").upper(),
            default_visible=bool(self._default_visible.isChecked()),
        )


class LayersPanelWidget(QtWidgets.QWidget):
    def __init__(self, *, studio_graph: F8StudioGraph, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._populating = False
        self._row_by_layer_id: dict[str, int] = {}

        self._active_summary = QtWidgets.QLabel(self)
        self._active_summary.setStyleSheet("color: rgba(235,235,235,160);")

        self._show_all_btn = QtWidgets.QPushButton("Show All", self)
        self._show_all_btn.clicked.connect(self._on_show_all_clicked)  # type: ignore[attr-defined]
        self._reset_defaults_btn = QtWidgets.QPushButton("Reset to Defaults", self)
        self._reset_defaults_btn.clicked.connect(self._on_reset_defaults_clicked)  # type: ignore[attr-defined]
        self._add_btn = QtWidgets.QPushButton("Add Layer", self)
        self._add_btn.clicked.connect(self._on_add_clicked)  # type: ignore[attr-defined]
        self._up_btn = QtWidgets.QToolButton(self)
        self._up_btn.setAutoRaise(True)
        self._up_btn.setIcon(icon_for(self._up_btn, StudioIcon.ARROW_BIG_UP))
        self._up_btn.setToolTip("Move selected layer up")
        self._up_btn.clicked.connect(self._on_move_up_clicked)  # type: ignore[attr-defined]
        self._down_btn = QtWidgets.QToolButton(self)
        self._down_btn.setAutoRaise(True)
        self._down_btn.setIcon(icon_for(self._down_btn, StudioIcon.ARROW_BIG_DOWN))
        self._down_btn.setToolTip("Move selected layer down")
        self._down_btn.clicked.connect(self._on_move_down_clicked)  # type: ignore[attr-defined]

        self._table = QtWidgets.QTableWidget(0, 7, self)
        self._table.setHorizontalHeaderLabels(["Show", "Layer", "Solo", "Default", "Color", "Edit", "Delete"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeToContents)

        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(self._show_all_btn)
        actions.addWidget(self._reset_defaults_btn)
        actions.addWidget(self._add_btn)
        actions.addStretch(1)
        actions.addWidget(self._up_btn)
        actions.addWidget(self._down_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._active_summary)
        layout.addLayout(actions)
        layout.addWidget(self._table, 1)

        self._studio_graph.layers_changed.connect(self._rebuild)  # type: ignore[attr-defined]
        self._studio_graph.active_layers_changed.connect(self._rebuild)  # type: ignore[attr-defined]
        self._rebuild()

    def _selected_layer_id(self) -> str:
        row = int(self._table.currentRow())
        if row < 0:
            return ""
        item = self._table.item(row, 1)
        if item is None:
            return ""
        return str(item.data(QtCore.Qt.UserRole) or "").strip()

    def _select_row(self, row: int) -> None:
        if row < 0:
            return
        self._table.setCurrentCell(row, 1)
        self._table.selectRow(row)

    def _build_centered_cell_widget(self, child: QtWidgets.QWidget) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(child, 0, QtCore.Qt.AlignCenter)
        return container

    def _rebuild(self, *_args: object) -> None:
        self._populating = True
        try:
            selected_layer_id = self._selected_layer_id()
            self._row_by_layer_id.clear()
            layer_defs = list(self._studio_graph.session_layer_defs() or [])
            active_ids = set(self._studio_graph.active_layer_ids() or [])
            self._table.clearContents()
            self._table.setRowCount(len(layer_defs))
            for row, layer in enumerate(layer_defs):
                self._row_by_layer_id[layer.id] = row

                visible_button = QtWidgets.QToolButton(self)
                visible_button.setAutoRaise(True)
                visible_button.setCheckable(True)
                is_visible = layer.id in active_ids
                visible_button.setChecked(is_visible)
                visible_button.setIcon(
                    icon_for(visible_button, StudioIcon.EYE if is_visible else StudioIcon.EYE_SLASH)
                )
                visible_button.setToolTip(
                    (
                        f"Hide layer '{str(layer.label or layer.id)}'"
                        if is_visible
                        else f"Show layer '{str(layer.label or layer.id)}'"
                    )
                )
                visible_button.toggled.connect(
                    lambda checked, button=visible_button, layer_id=layer.id, current_row=row: self._on_visible_toggled(
                        layer_id,
                        checked,
                        current_row,
                        button,
                    )
                )  # type: ignore[attr-defined]
                self._table.setCellWidget(row, 0, visible_button)

                layer_item = QtWidgets.QTableWidgetItem(str(layer.label or layer.id))
                layer_item.setData(QtCore.Qt.UserRole, layer.id)
                layer_item.setToolTip(str(layer.description or layer.id))
                self._table.setItem(row, 1, layer_item)

                solo_btn = QtWidgets.QToolButton(self)
                solo_btn.setAutoRaise(True)
                solo_btn.setIcon(icon_for(solo_btn, StudioIcon.EYE_STAR))
                solo_btn.setToolTip(f"Show only layer '{str(layer.label or layer.id)}'")
                solo_btn.clicked.connect(
                    lambda _checked=False, layer_id=layer.id, current_row=row: self._on_solo_clicked(
                        layer_id,
                        current_row,
                    )
                )  # type: ignore[attr-defined]
                self._table.setCellWidget(row, 2, solo_btn)

                default_checkbox = QtWidgets.QCheckBox(self)
                default_checkbox.setChecked(bool(layer.default_visible))
                default_checkbox.toggled.connect(
                    lambda checked, layer_id=layer.id, current_row=row: self._on_default_visible_toggled(
                        layer_id,
                        checked,
                        current_row,
                    )
                )  # type: ignore[attr-defined]
                self._table.setCellWidget(row, 3, default_checkbox)

                color_btn = QtWidgets.QToolButton(self)
                color_btn.setAutoRaise(True)
                color_btn.setToolTip(
                    f"Change color for layer '{str(layer.label or layer.id)}' ({str(layer.color or '#64748B').upper()})"
                )
                color_btn.setCursor(QtCore.Qt.PointingHandCursor)
                color_btn.setFixedSize(28, 16)
                color_btn.clicked.connect(
                    lambda _checked=False, layer_id=layer.id, current_row=row: self._on_color_clicked(
                        layer_id,
                        current_row,
                    )
                )  # type: ignore[attr-defined]
                color_btn.setStyleSheet(
                    "QToolButton {"
                    "background: transparent;"
                    f"border: 1px solid {QtGui.QColor(str(layer.color or '#64748B')).darker(125).name()};"
                    f"background-color: {str(layer.color or '#64748B')};"
                    "border-radius: 5px;"
                    "padding: 0px;"
                    "margin: 0px;"
                    "}"
                    "QToolButton:hover {"
                    "border-width: 2px;"
                    "}"
                    "QToolButton:pressed {"
                    "border-width: 2px;"
                    "}"
                )
                self._table.setCellWidget(row, 4, self._build_centered_cell_widget(color_btn))

                edit_btn = QtWidgets.QToolButton(self)
                edit_btn.setIcon(icon_for(edit_btn, StudioIcon.EDIT))
                edit_btn.setToolTip(f"Edit layer '{str(layer.label or layer.id)}'")
                edit_btn.setAutoRaise(True)
                edit_btn.clicked.connect(
                    lambda _checked=False, layer_id=layer.id, current_row=row: self._on_edit_clicked(
                        layer_id,
                        current_row,
                    )
                )  # type: ignore[attr-defined]
                self._table.setCellWidget(row, 5, edit_btn)

                delete_btn = QtWidgets.QToolButton(self)
                delete_btn.setIcon(icon_for(delete_btn, StudioIcon.TRASH))
                delete_btn.setToolTip(f"Delete layer '{str(layer.label or layer.id)}'")
                delete_btn.setAutoRaise(True)
                delete_btn.setEnabled(not bool(layer.is_base))
                delete_btn.clicked.connect(
                    lambda _checked=False, layer_id=layer.id, current_row=row: self._on_delete_clicked(
                        layer_id,
                        current_row,
                    )
                )  # type: ignore[attr-defined]
                self._table.setCellWidget(row, 6, delete_btn)
            self._active_summary.setText(
                "Active Layers: " + (self._studio_graph.active_layer_label_summary() or "None")
            )
            if selected_layer_id:
                selected_row = self._row_by_layer_id.get(selected_layer_id, -1)
                self._select_row(selected_row)
        finally:
            self._populating = False

    def _layer_dialog(
        self,
        *,
        title: str,
        layer: F8LayerDef | None,
    ) -> _LayerDialogResult | None:
        dialog = _LayerEditDialog(
            self,
            title=title,
            initial_label=(str(layer.label or "") if layer is not None else ""),
            initial_description=(str(layer.description or "") if layer is not None else ""),
            initial_color=(str(layer.color or "#64748B") if layer is not None else "#64748B"),
            initial_default_visible=(bool(layer.default_visible) if layer is not None else True),
            is_base=(bool(layer.is_base) if layer is not None else False),
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return None
        return dialog.result_value()

    def _on_show_all_clicked(self) -> None:
        self._studio_graph.show_all_layers()

    def _on_reset_defaults_clicked(self) -> None:
        self._studio_graph.reset_active_layers_to_defaults()

    def _on_add_clicked(self) -> None:
        result = self._layer_dialog(title="Add Layer", layer=None)
        if result is None:
            return
        new_layer = self._studio_graph.add_layer(
            label=result.label,
            description=result.description,
            color=result.color,
            default_visible=result.default_visible,
        )
        self._studio_graph.solo_layer(new_layer.id)

    def _on_move_up_clicked(self) -> None:
        layer_id = self._selected_layer_id()
        if not layer_id:
            return
        self._studio_graph.move_layer(layer_id, delta=-1)

    def _on_move_down_clicked(self) -> None:
        layer_id = self._selected_layer_id()
        if not layer_id:
            return
        self._studio_graph.move_layer(layer_id, delta=1)

    def _on_visible_toggled(self, layer_id: str, checked: bool, row: int, button: QtWidgets.QToolButton) -> None:
        self._select_row(row)
        button.setIcon(icon_for(button, StudioIcon.EYE if bool(checked) else StudioIcon.EYE_SLASH))
        layer = self._studio_graph.layer_def_by_id(layer_id)
        layer_label = str(layer.label if layer is not None else layer_id)
        button.setToolTip(
            f"{'Hide' if bool(checked) else 'Show'} layer '{layer_label}'"
        )
        if self._populating:
            return
        self._studio_graph.set_layer_visible(layer_id, bool(checked))

    def _on_solo_clicked(self, layer_id: str, row: int) -> None:
        self._select_row(row)
        self._studio_graph.solo_layer(layer_id)

    def _on_default_visible_toggled(self, layer_id: str, checked: bool, row: int) -> None:
        self._select_row(row)
        if self._populating:
            return
        layer = self._studio_graph.layer_def_by_id(layer_id)
        if layer is None:
            return
        self._studio_graph.update_layer_definition(
            layer_id=layer_id,
            label=layer.label,
            description=layer.description,
            color=layer.color,
            default_visible=bool(checked),
        )

    def _on_color_clicked(self, layer_id: str, row: int) -> None:
        self._select_row(row)
        layer = self._studio_graph.layer_def_by_id(layer_id)
        if layer is None:
            return
        picked = QtWidgets.QColorDialog.getColor(QtGui.QColor(layer.color), self, "Choose Layer Color")
        if not picked.isValid():
            return
        self._studio_graph.update_layer_definition(
            layer_id=layer.id,
            label=layer.label,
            description=layer.description,
            color=str(picked.name()).upper(),
            default_visible=layer.default_visible,
        )

    def _on_edit_clicked(self, layer_id: str, row: int) -> None:
        self._select_row(row)
        layer = self._studio_graph.layer_def_by_id(layer_id)
        if layer is None:
            return
        result = self._layer_dialog(title="Edit Layer", layer=layer)
        if result is None:
            return
        self._studio_graph.update_layer_definition(
            layer_id=layer.id,
            label=result.label,
            description=result.description,
            color=result.color,
            default_visible=result.default_visible,
        )

    def _on_delete_clicked(self, layer_id: str, row: int) -> None:
        self._select_row(row)
        layer = self._studio_graph.layer_def_by_id(layer_id)
        if layer is None or layer.id == BASE_LAYER_ID:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete Layer",
            f"Delete layer '{layer.label}'?\n\nNodes in this layer will fall back to Base if needed.",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._studio_graph.delete_layer(layer_id)
