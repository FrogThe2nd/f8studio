from __future__ import annotations

from qtpy import QtCore, QtWidgets

from ...nodegraph.node_graph import F8StudioGraph
from ...nodegraph.layers import extract_node_layer_ids_from_ui_state


class F8LayerMembershipEditor(QtWidgets.QWidget):
    value_changed = QtCore.Signal(str, object)

    def __init__(self, *, node_graph: F8StudioGraph, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._node_graph = node_graph
        self._name = "f8_ui_state"
        self._ui_state: dict[str, object] = {}
        self._checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        self._empty_label = QtWidgets.QLabel("No layers defined.", self)
        self._empty_label.setStyleSheet("color: rgba(235,235,235,120);")
        self._content = QtWidgets.QWidget(self)
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        self._content_layout.addStretch(1)
        self._stack = QtWidgets.QVBoxLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(6)
        self._stack.addWidget(self._empty_label)
        self._stack.addWidget(self._content)

    def set_name(self, name: str) -> None:
        self._name = str(name or "f8_ui_state")

    def get_name(self) -> str:
        return self._name

    def set_value(self, value: object) -> None:
        self._ui_state = dict(value) if isinstance(value, dict) else {}
        self._rebuild()

    def get_value(self) -> dict[str, object]:
        return dict(self._ui_state)

    def _rebuild(self) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checkboxes.clear()

        layer_defs = list(self._node_graph.session_layer_defs() or [])
        selected_ids = set(extract_node_layer_ids_from_ui_state(self._ui_state))

        if not layer_defs:
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)
        insert_at = 0
        for layer in layer_defs:
            checkbox = QtWidgets.QCheckBox(str(layer.label or layer.id), self)
            checkbox.setChecked(layer.id in selected_ids)
            tooltip = str(layer.description or "").strip()
            if tooltip:
                checkbox.setToolTip(tooltip)
            checkbox.toggled.connect(self._on_checkbox_toggled)  # type: ignore[attr-defined]
            self._checkboxes[layer.id] = checkbox
            self._content_layout.insertWidget(insert_at, checkbox)
            insert_at += 1

    def _selected_layer_ids(self) -> list[str]:
        return [layer_id for layer_id, checkbox in self._checkboxes.items() if checkbox.isChecked()]

    def _on_checkbox_toggled(self, _checked: bool) -> None:
        next_ui_state = self._node_graph.set_node_layer_ids_in_ui_state_for_editor(
            self._ui_state,
            self._selected_layer_ids(),
        )
        self._ui_state = next_ui_state
        self.value_changed.emit(self._name, dict(self._ui_state))
