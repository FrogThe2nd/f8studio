from __future__ import annotations

from collections.abc import Callable, Iterable

from qtpy import QtCore, QtWidgets

from ..components.component_taxonomy import (
    COMPONENT_ROLE_LABELS,
    ComponentRole,
    build_component_tags,
    component_taxonomy_from_tags,
    partition_component_tags,
)
from .project_asset_dialogs import AssetOverwriteChoice


_VALIDATION_ERROR_STYLE = (
    "QLabel { color: #fff7df; background: #5f3f0f; border: 1px solid #a66f1c; border-radius: 6px; padding: 6px 8px;}"
)


def _comma_separated_values(text: str) -> list[str]:
    return [value for part in str(text or "").split(",") if (value := part.strip())]


class ComponentMetadataEditor(QtWidgets.QWidget):
    """Structured authoring UI backed by the existing flat component tags field."""

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget,
        name: str,
        description: str,
        tags: Iterable[str],
    ) -> None:
        super().__init__(parent)
        self._name = QtWidgets.QLineEdit(self)
        self._name.setObjectName("component-name")
        self._description = QtWidgets.QLineEdit(self)
        self._description.setObjectName("component-description")

        self._role = QtWidgets.QComboBox(self)
        self._role.setObjectName("component-role")
        self._role.addItem("Unspecified", "")
        for role, label in COMPONENT_ROLE_LABELS.items():
            self._role.addItem(label, role.value)

        self._workflows = self._taxonomy_line_edit("component-workflows")
        self._signals = self._taxonomy_line_edit("component-signals")
        self._protocols = self._taxonomy_line_edit("component-protocols")
        self._levels = self._taxonomy_line_edit("component-levels")
        self._free_tags = QtWidgets.QLineEdit(self)
        self._free_tags.setObjectName("component-free-tags")

        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        form.addRow("Role", self._role)
        form.addRow("Workflows", self._workflows)
        form.addRow("Signals", self._signals)
        form.addRow("Protocols", self._protocols)
        form.addRow("Levels", self._levels)
        form.addRow("Tags", self._free_tags)

        taxonomy_tooltip = "Comma-separated lowercase values. Use '-' or '_' instead of spaces."
        for field in (self._workflows, self._signals, self._protocols, self._levels):
            field.setToolTip(taxonomy_tooltip)
        self._free_tags.setToolTip("Comma-separated custom tags. Reserved taxonomy tags use the fields above.")
        self.set_values(name=name, description=description, tags=tags)

    def _taxonomy_line_edit(self, object_name: str) -> QtWidgets.QLineEdit:
        field = QtWidgets.QLineEdit(self)
        field.setObjectName(object_name)
        return field

    def set_values(self, *, name: str, description: str, tags: Iterable[str]) -> None:
        normalized_tags = list(tags)
        taxonomy = component_taxonomy_from_tags(normalized_tags)
        partition = partition_component_tags(normalized_tags)
        self._name.setText(str(name or "").strip())
        self._description.setText(str(description or "").strip())
        role_value = "" if taxonomy.role is None else taxonomy.role.value
        role_index = self._role.findData(role_value)
        self._role.setCurrentIndex(max(0, role_index))
        self._workflows.setText(", ".join(sorted(taxonomy.workflows)))
        self._signals.setText(", ".join(sorted(taxonomy.signals)))
        self._protocols.setText(", ".join(sorted(taxonomy.protocols)))
        self._levels.setText(", ".join(sorted(taxonomy.levels)))
        self._free_tags.setText(", ".join(partition.free_tags))

    def name(self) -> str:
        return str(self._name.text() or "").strip()

    def values(self) -> tuple[str, str, list[str]]:
        role_data = str(self._role.currentData() or "").strip()
        role = None if not role_data else ComponentRole(role_data)
        tags = build_component_tags(
            role=role,
            workflows=_comma_separated_values(self._workflows.text()),
            signals=_comma_separated_values(self._signals.text()),
            protocols=_comma_separated_values(self._protocols.text()),
            levels=_comma_separated_values(self._levels.text()),
            free_tags=_comma_separated_values(self._free_tags.text()),
        )
        return (
            self.name(),
            str(self._description.text() or "").strip(),
            tags,
        )


class _ComponentMetadataDialogBase(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: Iterable[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(580, 400)
        self._editor = ComponentMetadataEditor(
            parent=self,
            name=name,
            description=description,
            tags=tags,
        )
        self._validation_error = QtWidgets.QLabel(self)
        self._validation_error.setObjectName("component-meta-validation-error")
        self._validation_error.setWordWrap(True)
        self._validation_error.setStyleSheet(_VALIDATION_ERROR_STYLE)
        self._validation_error.hide()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.addWidget(self._editor)
        self._layout.addWidget(self._validation_error)
        self._layout.addWidget(buttons)

    def _name_validation_error(self, name: str) -> str | None:
        _ = name
        return None

    def _on_accept_clicked(self) -> None:
        name = self._editor.name()
        if not name:
            self._show_validation_error("Name cannot be empty.")
            return
        message = self._name_validation_error(name)
        if message:
            self._show_validation_error(message)
            return
        try:
            self._editor.values()
        except ValueError as exc:
            self._show_validation_error(str(exc))
            return
        self._clear_validation_error()
        self.accept()

    def _show_validation_error(self, message: str) -> None:
        self._validation_error.setText(str(message or "").strip())
        self._validation_error.setVisible(True)

    def _clear_validation_error(self) -> None:
        self._validation_error.setText("")
        self._validation_error.hide()


class ComponentMetadataDialog(_ComponentMetadataDialogBase):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: Iterable[str],
        name_validator: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__(
            parent=parent,
            title=title,
            name=name,
            description=description,
            tags=tags,
        )
        self._name_validator = name_validator

    def _name_validation_error(self, name: str) -> str | None:
        if self._name_validator is None:
            return None
        return self._name_validator(name)

    def values(self) -> tuple[str, str, list[str]]:
        return self._editor.values()


class ComponentOverwriteMetadataDialog(_ComponentMetadataDialogBase):
    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: Iterable[str],
        overwrite_choices: Iterable[AssetOverwriteChoice] | None = None,
        overwrite_label: str = "Overwrite Existing",
        selected_asset_id: str | None = None,
        name_validator: Callable[[str, str | None], str | None] | None = None,
    ) -> None:
        default_name = str(name or "").strip()
        default_description = str(description or "").strip()
        default_tags = tuple(str(tag).strip() for tag in tags if str(tag).strip())
        super().__init__(
            parent=parent,
            title=title,
            name=default_name,
            description=default_description,
            tags=default_tags,
        )
        self._name_validator = name_validator
        self._default_name = default_name
        self._default_description = default_description
        self._default_tags = default_tags
        self._choices_by_id: dict[str, AssetOverwriteChoice] = {
            str(choice.asset_id): choice for choice in list(overwrite_choices or []) if str(choice.asset_id).strip()
        }

        self._overwrite_combo = QtWidgets.QComboBox(self)
        self._overwrite_combo.setObjectName("component-overwrite-target")
        self._overwrite_combo.addItem("Create New", "")
        self._overwrite_combo.setItemData(
            0,
            "Create a new local editable component.",
            QtCore.Qt.ItemDataRole.ToolTipRole,
        )
        for choice in self._choices_by_id.values():
            self._overwrite_combo.addItem(str(choice.display_label or choice.label), str(choice.asset_id))
            choice_index = self._overwrite_combo.count() - 1
            tooltip = str(choice.tooltip or "").strip()
            if tooltip:
                self._overwrite_combo.setItemData(choice_index, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
        self._overwrite_combo.currentIndexChanged.connect(self._on_overwrite_changed)  # type: ignore[attr-defined]

        overwrite_row = QtWidgets.QWidget(self)
        overwrite_layout = QtWidgets.QFormLayout(overwrite_row)
        overwrite_layout.setContentsMargins(0, 0, 0, 0)
        overwrite_layout.addRow(overwrite_label, self._overwrite_combo)
        self._layout.insertWidget(0, overwrite_row)

        selected_index = -1
        if selected_asset_id:
            selected_index = self._overwrite_combo.findData(str(selected_asset_id))
        self._overwrite_combo.setCurrentIndex(max(0, selected_index))
        self._on_overwrite_changed()

    def selected_asset_id(self) -> str | None:
        selected_asset_id = str(self._overwrite_combo.currentData() or "").strip()
        return None if not selected_asset_id else selected_asset_id

    def values(self) -> tuple[str, str, list[str], str | None]:
        name, description, tags = self._editor.values()
        return name, description, tags, self.selected_asset_id()

    def _name_validation_error(self, name: str) -> str | None:
        if self._name_validator is None:
            return None
        return self._name_validator(name, self.selected_asset_id())

    def _on_overwrite_changed(self) -> None:
        self._clear_validation_error()
        selected_asset_id = self.selected_asset_id()
        choice = None if selected_asset_id is None else self._choices_by_id.get(selected_asset_id)
        if choice is None:
            self._editor.set_values(
                name=self._default_name,
                description=self._default_description,
                tags=self._default_tags,
            )
            return
        self._editor.set_values(
            name=choice.label,
            description=choice.description,
            tags=choice.tags,
        )


__all__ = [
    "ComponentMetadataDialog",
    "ComponentMetadataEditor",
    "ComponentOverwriteMetadataDialog",
]
