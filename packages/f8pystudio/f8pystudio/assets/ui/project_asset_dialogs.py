from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from qtpy import QtCore, QtWidgets

from ..projects.project_models import F8ProjectSummary
from ...ui_notifications import show_warning


class ProjectAssetMetaDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: list[str],
        usage_notes: str = "",
        include_usage_notes: bool,
        name_validator: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 280 if include_usage_notes else 220)
        self._name_validator = name_validator
        self._include_usage_notes = bool(include_usage_notes)
        self._name = QtWidgets.QLineEdit(name, self)
        self._description = QtWidgets.QLineEdit(description, self)
        self._tags = QtWidgets.QLineEdit(", ".join(tags), self)
        self._usage_notes = QtWidgets.QPlainTextEdit(self)
        self._usage_notes.setPlainText(str(usage_notes or ""))
        self._usage_notes.setVisible(self._include_usage_notes)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        form.addRow("Tags (comma-separated)", self._tags)
        if self._include_usage_notes:
            form.addRow("Usage Notes", self._usage_notes)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept_clicked(self) -> None:
        name = str(self._name.text() or "").strip()
        if not name:
            show_warning(self, "Invalid name", "Name cannot be empty.")
            return
        if self._name_validator is not None:
            message = self._name_validator(name)
            if message:
                show_warning(self, "Invalid name", message)
                return
        self.accept()

    def values(self) -> tuple[str, str, list[str], str]:
        tags = [part.strip() for part in str(self._tags.text() or "").split(",")]
        return (
            str(self._name.text() or "").strip(),
            str(self._description.text() or "").strip(),
            [tag for tag in tags if tag],
            str(self._usage_notes.toPlainText() or "").strip(),
        )


class ProjectPickerDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        projects: list[F8ProjectSummary],
        current_project_id: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Project")
        self.resize(720, 420)
        self._projects = list(projects)
        self._list = QtWidgets.QListWidget(self)
        self._details = QtWidgets.QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(split)
        layout.addWidget(buttons)

        for index, project in enumerate(self._projects):
            item = QtWidgets.QListWidgetItem(project.name)
            item.setData(QtCore.Qt.UserRole, project.projectId)
            if project.projectId == current_project_id:
                item.setSelected(True)
                self._list.setCurrentRow(index)
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._on_current_item_changed)  # type: ignore[attr-defined]
        if self._list.currentItem() is None and self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._on_current_item_changed(self._list.currentItem(), None)

    def selected_project_id(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return ""
        return str(item.data(QtCore.Qt.UserRole) or "").strip()

    def _on_current_item_changed(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        if current is None:
            self._details.setPlainText("")
            return
        selected_project_id = str(current.data(QtCore.Qt.UserRole) or "").strip()
        selected_summary = None
        for project in self._projects:
            if project.projectId == selected_project_id:
                selected_summary = project
                break
        if selected_summary is None:
            self._details.setPlainText("")
            return
        self._details.setPlainText(
            "\n".join(
                [
                    f"Name: {selected_summary.name}",
                    f"Description: {selected_summary.description}",
                    f"Tags: {', '.join(selected_summary.tags)}",
                    f"Version: {selected_summary.latestVersionNumber}",
                    f"Updated: {selected_summary.updatedAt}",
                ]
            )
        )


@dataclass(frozen=True)
class AssetVersionBrowserItem:
    version_number: int
    created_at: str
    revision: str = ""
    change_summary: str = ""


@dataclass(frozen=True)
class AssetVersionBrowserAction:
    action_key: str
    label: str


class AssetVersionBrowserDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        items: list[AssetVersionBrowserItem],
        load_payload: Callable[[int], dict[str, Any]],
        primary_action_label: str | None = None,
        actions: list[AssetVersionBrowserAction] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 520)
        self._items = list(items)
        self._load_payload = load_payload
        self._selected_version_number: int | None = None
        self._selected_action_key: str | None = None

        self._list = QtWidgets.QListWidget(self)
        self._details = QtWidgets.QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 5)

        buttons = QtWidgets.QDialogButtonBox(parent=self)
        close_button = buttons.addButton(QtWidgets.QDialogButtonBox.Close)
        close_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        resolved_actions = list(actions or [])
        if primary_action_label and not resolved_actions:
            resolved_actions.append(AssetVersionBrowserAction(action_key="primary", label=primary_action_label))
        self._action_buttons: dict[str, QtWidgets.QPushButton] = {}
        for action in resolved_actions:
            action_button = buttons.addButton(action.label, QtWidgets.QDialogButtonBox.AcceptRole)
            action_button.clicked.connect(
                lambda _checked=False, action_key=action.action_key: self._on_action_clicked(action_key)
            )  # type: ignore[attr-defined]
            self._action_buttons[action.action_key] = action_button

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(split)
        layout.addWidget(buttons)

        for item in self._items:
            label_parts = [f"v{item.version_number}", item.created_at]
            if item.revision:
                label_parts.append(item.revision)
            if item.change_summary:
                label_parts.append(item.change_summary)
            list_item = QtWidgets.QListWidgetItem(" | ".join(label_parts))
            list_item.setData(QtCore.Qt.UserRole, item.version_number)
            self._list.addItem(list_item)
        self._list.currentItemChanged.connect(self._on_current_item_changed)  # type: ignore[attr-defined]
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._details.setPlainText("No versions found.")
            self._set_action_buttons_enabled(False)

    def selected_version_number(self) -> int | None:
        return self._selected_version_number

    def selected_action_key(self) -> str | None:
        return self._selected_action_key

    def _on_action_clicked(self, action_key: str) -> None:
        if self._selected_version_number is None:
            return
        self._selected_action_key = str(action_key)
        self.accept()

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        for button in self._action_buttons.values():
            button.setEnabled(enabled)

    def _on_current_item_changed(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        if current is None:
            self._selected_version_number = None
            self._selected_action_key = None
            self._details.setPlainText("")
            self._set_action_buttons_enabled(False)
            return
        version_number = int(current.data(QtCore.Qt.UserRole) or 0)
        self._selected_version_number = version_number
        self._selected_action_key = None
        self._set_action_buttons_enabled(True)
        try:
            payload = self._load_payload(version_number)
        except Exception as exc:
            self._details.setPlainText(f"Failed to load version {version_number}.\n\n{exc}")
            return
        self._details.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
