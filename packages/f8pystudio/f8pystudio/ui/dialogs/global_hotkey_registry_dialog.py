from __future__ import annotations

from typing import Callable

from qtpy import QtCore, QtWidgets

from ...global_hotkeys.models import GlobalHotkeyRegistryEntry
from ..support.studio_theme import qss_rgba, studio_dark_theme


class GlobalHotkeyRegistryDialog(QtWidgets.QDialog):
    node_requested = QtCore.Signal(str)

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        entries_provider: Callable[[], list[GlobalHotkeyRegistryEntry]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Global Hotkeys")
        self.resize(880, 420)
        self._entries_provider = entries_provider

        self._summary = QtWidgets.QLabel(self)
        self._summary.setStyleSheet(f"color: {qss_rgba(studio_dark_theme().palette.text_primary, 160)};")

        self._table = QtWidgets.QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["Key", "Binding", "Status"])
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self._table.itemDoubleClicked.connect(self._on_item_double_clicked)  # type: ignore[attr-defined]

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]
        buttons.accepted.connect(self.accept)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._table, 1)
        layout.addWidget(buttons)

        self.refresh_entries()

    def refresh_entries(self) -> None:
        entries = sorted(
            list(self._entries_provider() or []),
            key=lambda entry: (str(entry.hotkey_text or ""), str(entry.node_label or ""), str(entry.control_label or "")),
        )
        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            binding_text = f"{entry.node_id}: {entry.node_label or entry.node_id} - {entry.control_label or entry.field_name}"
            key_item = QtWidgets.QTableWidgetItem(entry.hotkey_text)
            binding_item = QtWidgets.QTableWidgetItem(binding_text)
            status_text = str(entry.status or "").strip()
            if entry.message:
                status_text = f"{status_text}: {entry.message}" if status_text else entry.message
            status_item = QtWidgets.QTableWidgetItem(status_text)
            key_item.setData(QtCore.Qt.UserRole, entry.node_id)
            binding_item.setData(QtCore.Qt.UserRole, entry.node_id)
            status_item.setData(QtCore.Qt.UserRole, entry.node_id)
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, binding_item)
            self._table.setItem(row, 2, status_item)
        self._summary.setText(f"{len(entries)} configured global hotkey binding(s). Double-click a row to select the node.")

    def _on_item_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        node_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        if node_id:
            self.node_requested.emit(node_id)
