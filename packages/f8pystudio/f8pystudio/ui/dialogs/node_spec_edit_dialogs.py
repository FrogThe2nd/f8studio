from __future__ import annotations

from typing import Any, Callable

import msgspec
from f8pysdk.specs import (
    F8DataPortSpec,
    F8StateAccess,
    F8StateSpec,
    can_edit_state_field_access,
    can_edit_state_field_required,
    can_edit_state_field_value_schema,
    can_rename_state_field,
)
from f8pysdk.codec import copy_model
from qtpy import QtCore, QtGui, QtWidgets

from ...global_hotkeys.parser import parse_global_hotkey
from ...nodegraph.state_schema import schema_type_any as _schema_type
from .schema_builder_dialog import SchemaBuilderDialog, schema_from_json_obj
from ...ui.support.ui_control import parse_ui_control
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.ui_notifications import show_warning
from ...ui.support.studio_theme import label_qss, studio_dark_theme


class _F8HotkeySequenceEdit(QtWidgets.QKeySequenceEdit):
    capture_started = QtCore.Signal()
    capture_finished = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMaximumSequenceLength(1)

    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        self.capture_started.emit()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        try:
            super().focusOutEvent(event)
        finally:
            self.capture_finished.emit()


class _F8GlobalHotkeyEdit(QtWidgets.QWidget):
    status_changed = QtCore.Signal()

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        value: str = "",
        current_binding_id: str = "",
        conflict_lookup: Callable[[str, str], list[Any]] | None = None,
        capture_started: Callable[[], None] | None = None,
        capture_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._current_binding_id = str(current_binding_id or "").strip()
        self._conflict_lookup = conflict_lookup
        self._capture_started = capture_started
        self._capture_finished = capture_finished
        self._capture_active = False
        self._editor = _F8HotkeySequenceEdit(self)
        self._clear_btn = QtWidgets.QPushButton(self)
        self._commit_btn = QtWidgets.QPushButton("Use Shortcut", self)
        self._status = QtWidgets.QLabel(self)
        self._clear_btn.setFixedSize(28, 28)
        self._clear_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._clear_btn.setIconSize(QtCore.QSize(16, 16))
        self._clear_btn.setIcon(icon_for(self._clear_btn, StudioIcon.X))
        self._clear_btn.clicked.connect(self.clear)  # type: ignore[attr-defined]
        self._commit_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._commit_btn.clicked.connect(self._commit_current_value)  # type: ignore[attr-defined]
        self._editor.setToolTip("Click here and press a shortcut, for example Ctrl+Alt+P")
        self._clear_btn.setToolTip("Clear the current global hotkey")
        self._commit_btn.setToolTip("Accept the currently captured shortcut")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted))

        try:
            self._editor.editingFinished.connect(self._normalize_sequence)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        self._editor.keySequenceChanged.connect(self._refresh_status)  # type: ignore[attr-defined]
        self._editor.capture_started.connect(self._on_capture_started)  # type: ignore[attr-defined]
        self._editor.capture_finished.connect(self._on_capture_finished)  # type: ignore[attr-defined]

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._editor, 1)
        row.addWidget(self._commit_btn)
        row.addWidget(self._clear_btn)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(row)
        layout.addWidget(self._status)
        self.set_value(value)
        self._refresh_status()

    def setEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        super().setEnabled(enabled)
        self._editor.setEnabled(bool(enabled))
        self._refresh_action_buttons()

    def set_value(self, value: str) -> None:
        text = str(value or "").strip()
        with QtCore.QSignalBlocker(self._editor):
            self._editor.setKeySequence(QtGui.QKeySequence(text))
        self._refresh_status()

    def value(self) -> str:
        try:
            sequence = self._editor.keySequence()
        except AttributeError:
            return ""
        return str(sequence.toString(QtGui.QKeySequence.SequenceFormat.PortableText) or "").strip()

    def clear(self) -> None:
        with QtCore.QSignalBlocker(self._editor):
            self._editor.clear()
        self._refresh_status()

    def setToolTip(self, tooltip: str) -> None:  # type: ignore[override]
        text = str(tooltip or "")
        super().setToolTip(text)
        self._editor.setToolTip(text)
        self._clear_btn.setToolTip("Clear the current global hotkey" if text else "")

    def release_capture(self) -> None:
        if not self._capture_active:
            return
        self._capture_active = False
        if self._capture_finished is not None:
            self._capture_finished()
        self._refresh_status()

    def conflicts(self) -> list[Any]:
        text = self.value()
        if not text or self._conflict_lookup is None:
            return []
        return list(self._conflict_lookup(text, exclude_binding_id=self._current_binding_id) or [])

    def has_conflict(self) -> bool:
        return bool(self.conflicts())

    def is_valid_value(self) -> bool:
        text = self.value()
        if not text:
            return True
        try:
            parse_global_hotkey(text)
        except Exception:
            return False
        return True

    def is_submittable(self) -> bool:
        return self.is_valid_value() and not self.has_conflict()

    def _normalize_sequence(self) -> None:
        text = self.value()
        if not text:
            self._refresh_status()
            return
        try:
            normalized = parse_global_hotkey(text).display_text
        except Exception:
            self._refresh_status()
            return
        self.set_value(normalized)

    def _on_capture_started(self) -> None:
        if self._capture_active:
            return
        self._capture_active = True
        if self._capture_started is not None:
            self._capture_started()
        self._refresh_status()

    def _on_capture_finished(self) -> None:
        self.release_capture()

    def _refresh_action_buttons(self) -> None:
        enabled = self.isEnabled()
        self._clear_btn.setEnabled(bool(enabled))
        self._commit_btn.setEnabled(
            bool(enabled) and self._capture_active and bool(self.value()) and self.is_submittable()
        )

    def _commit_current_value(self) -> None:
        if not self.is_submittable():
            return
        dialog = self.window()
        if isinstance(dialog, QtWidgets.QDialog):
            dialog.accept()

    def _refresh_status(self) -> None:
        self._refresh_action_buttons()
        text = self.value()
        if self._capture_active:
            if not text:
                self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.warning))
                self._status.setText("Capturing shortcut.")
                self.status_changed.emit()
                return
            if not self.is_valid_value():
                self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.error))
                self._status.setText("Invalid shortcut. Keep editing or clear it.")
                self.status_changed.emit()
                return
            conflicts = self.conflicts()
            if conflicts:
                entry = conflicts[0]
                self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.error))
                self._status.setText(
                    f"Shortcut already used by {entry.node_id}: {entry.node_label or entry.node_id} - "
                    f"{entry.control_label or entry.field_name}. Change it or clear it."
                )
                self.status_changed.emit()
                return
            self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.success))
            self._status.setText("Shortcut is available.")
            self.status_changed.emit()
            return
        if not text:
            self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted))
            self._status.setText("No global hotkey assigned.")
            self.status_changed.emit()
            return
        if not self.is_valid_value():
            self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.error))
            self._status.setText("Invalid shortcut.")
            self.status_changed.emit()
            return
        conflicts = self.conflicts()
        if conflicts:
            entry = conflicts[0]
            self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.error))
            self._status.setText(
                f"Already used by {entry.node_id}: {entry.node_label or entry.node_id} - "
                f"{entry.control_label or entry.field_name}"
            )
            self.status_changed.emit()
            return
        self._status.setStyleSheet(label_qss(color=studio_dark_theme().palette.success))
        self._status.setText("Shortcut is available.")
        self.status_changed.emit()


class _F8EditExecPortDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str, name: str):
        super().__init__(parent)
        self.setWindowTitle(title)

        self._name = QtWidgets.QLineEdit(name)
        self._name.setClearButtonEnabled(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def name(self) -> str:
        return str(self._name.text() or "").strip()


class _F8EditDataPortDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        port: F8DataPortSpec,
        ui_only: bool = False,
        lock_identity_fields: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._ui_only = bool(ui_only)
        self._lock_identity_fields = bool(lock_identity_fields)
        self._read_only = bool(read_only)
        self._schema = port.valueSchema or schema_from_json_obj({"type": "any"})

        self._name = QtWidgets.QLineEdit(str(port.name or ""))
        self._name.setClearButtonEnabled(True)
        self._required = QtWidgets.QCheckBox()
        self._required.setChecked(bool(port.required))
        self._show_on_node = QtWidgets.QCheckBox()
        self._show_on_node.setChecked(bool(port.showOnNode))
        self._desc = QtWidgets.QPlainTextEdit(str(port.description or ""))

        self._schema_summary = QtWidgets.QLabel("")
        self._schema_summary.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted))
        self._refresh_schema_summary()

        self._schema_btn = QtWidgets.QPushButton("View Schema..." if self._read_only else "Edit Schema...")
        self._schema_btn.clicked.connect(self._edit_schema)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Required", self._required)
        form.addRow("Show On Node", self._show_on_node)
        form.addRow("Description", self._desc)

        schema_row = QtWidgets.QHBoxLayout()
        schema_row.addWidget(self._schema_summary, 1)
        schema_row.addWidget(self._schema_btn)
        form.addRow("valueSchema", schema_row)

        self._buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

        if self._ui_only or self._lock_identity_fields:
            for w in (self._name, self._required):
                w.setEnabled(False)
        if self._read_only:
            for w in (self._name, self._required, self._show_on_node, self._desc):
                w.setEnabled(False)
            ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(False)

    def _refresh_schema_summary(self) -> None:
        t = _schema_type(self._schema)
        self._schema_summary.setText(t or "unknown")

    def _edit_schema(self) -> None:
        dialog_type = SchemaBuilderDialog
        dlg = dialog_type(
            self,
            title="View valueSchema" if self._read_only else "Edit valueSchema",
            schema=self._schema,
            read_only=bool(self._ui_only or self._read_only),
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        try:
            self._schema = dlg.schema()
        except Exception as e:
            show_warning(self, "Invalid schema", str(e))
            return
        self._refresh_schema_summary()

    def port(self) -> F8DataPortSpec:
        name = str(self._name.text() or "").strip()
        required = bool(self._required.isChecked())
        show_on_node = bool(self._show_on_node.isChecked())
        desc = str(self._desc.toPlainText() or "").strip() or msgspec.UNSET
        port = F8DataPortSpec(name=name, required=required, description=desc, valueSchema=self._schema)
        try:
            return copy_model(port, update={"showOnNode": bool(show_on_node)})
        except Exception:
            return port


class _F8EditStateFieldDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        field: F8StateSpec,
        global_hotkey: str = "",
        current_binding_id: str = "",
        hotkey_conflict_lookup: Callable[[str, str], list[Any]] | None = None,
        hotkey_capture_started: Callable[[], None] | None = None,
        hotkey_capture_finished: Callable[[], None] | None = None,
        ui_only: bool = False,
        lock_identity_fields: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        try:
            self._schema = field.valueSchema or schema_from_json_obj({"type": "any"})
        except Exception:
            self._schema = schema_from_json_obj({"type": "any"})
        self._ui_only = bool(ui_only)
        self._lock_identity_fields = bool(lock_identity_fields)
        self._read_only = bool(read_only)
        self._original_name = str(field.name or "")
        try:
            self._original_access = F8StateAccess(field.access)
        except (TypeError, ValueError):
            self._original_access = F8StateAccess.rw
        self._original_required = bool(field.required)
        self._original_schema = self._schema
        self._redact_on_publish = field.redactOnPublish
        self._editor_assist = field.editorAssist
        structure_locked = bool(self._ui_only or self._lock_identity_fields)
        self._can_rename = bool(can_rename_state_field(field) and not structure_locked)
        self._can_edit_access = bool(can_edit_state_field_access(field) and not structure_locked)
        self._can_edit_required = bool(can_edit_state_field_required(field) and not structure_locked)
        self._can_edit_value_schema = bool(can_edit_state_field_value_schema(field) and not structure_locked)

        self._name = QtWidgets.QLineEdit(str(field.name or ""))
        self._name.setClearButtonEnabled(True)

        self._access = QtWidgets.QComboBox(self)
        self._access.addItems([e.value for e in F8StateAccess])
        try:
            self._access.setCurrentText(str(field.access.value))
        except Exception:
            self._access.setCurrentText("rw")

        self._required = QtWidgets.QCheckBox(self)
        self._required.setChecked(bool(field.required))

        self._show_on_node = QtWidgets.QCheckBox(self)
        self._show_on_node.setChecked(bool(field.showOnNode))

        self._label = QtWidgets.QLineEdit(str(field.label or ""))
        self._label.setClearButtonEnabled(True)
        self._desc = QtWidgets.QPlainTextEdit(str(field.description or ""))
        self._ui_control = QtWidgets.QLineEdit(str(field.uiControl or ""))
        self._ui_control.setClearButtonEnabled(True)
        self._global_hotkey = _F8GlobalHotkeyEdit(
            value=str(global_hotkey or ""),
            current_binding_id=current_binding_id,
            conflict_lookup=hotkey_conflict_lookup,
            capture_started=hotkey_capture_started,
            capture_finished=hotkey_capture_finished,
        )
        self._ui_control.textChanged.connect(self._refresh_global_hotkey_enabled)  # type: ignore[attr-defined]
        self._global_hotkey.status_changed.connect(self._refresh_accept_enabled)  # type: ignore[attr-defined]

        self._schema_summary = QtWidgets.QLabel("", self)
        self._schema_summary.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted))
        self._refresh_schema_summary()

        self._schema_btn = QtWidgets.QPushButton(
            "View Schema..." if self._schema_is_read_only() else "Edit Schema...",
            self,
        )
        self._schema_btn.clicked.connect(self._edit_schema)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Access", self._access)
        form.addRow("Required", self._required)
        form.addRow("Show On Node", self._show_on_node)
        form.addRow("Label", self._label)
        form.addRow("Description", self._desc)
        form.addRow("uiControl", self._ui_control)
        form.addRow("Global Hotkey", self._global_hotkey)

        schema_row = QtWidgets.QHBoxLayout()
        schema_row.addWidget(self._schema_summary, 1)
        schema_row.addWidget(self._schema_btn)
        form.addRow("valueSchema", schema_row)

        self._buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

        if not self._can_rename:
            self._name.setEnabled(False)
            self._name.setToolTip("State field structure is locked.")
        if not self._can_edit_access:
            self._access.setEnabled(False)
            self._access.setToolTip("State field structure is locked.")
        if not self._can_edit_required:
            self._required.setEnabled(False)
            self._required.setToolTip("State field structure is locked.")
        if not self._can_edit_value_schema:
            self._schema_summary.setEnabled(False)
            self._schema_btn.setToolTip("State field structure is locked.")
        if self._read_only:
            for w in (
                self._name,
                self._access,
                self._required,
                self._show_on_node,
                self._label,
                self._desc,
                self._ui_control,
                self._global_hotkey,
            ):
                w.setEnabled(False)
            ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(False)
        self._refresh_global_hotkey_enabled()
        self._refresh_accept_enabled()

    def _refresh_schema_summary(self) -> None:
        t = _schema_type(self._schema)
        self._schema_summary.setText(t or "unknown")

    def _schema_is_read_only(self) -> bool:
        return bool(self._ui_only or self._read_only or self._lock_identity_fields or not self._can_edit_value_schema)

    def _edit_schema(self) -> None:
        schema_read_only = self._schema_is_read_only()
        dialog_type = SchemaBuilderDialog
        dlg = dialog_type(
            self,
            title="View valueSchema" if schema_read_only else "Edit valueSchema",
            schema=self._schema,
            read_only=schema_read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        try:
            self._schema = dlg.schema()
        except Exception as e:
            show_warning(self, "Invalid schema", str(e))
            return
        self._refresh_schema_summary()

    def _refresh_global_hotkey_enabled(self) -> None:
        if self._read_only:
            self._global_hotkey.setEnabled(False)
            self._refresh_accept_enabled()
            return
        enabled = parse_ui_control(str(self._ui_control.text() or "")).control_name == "button"
        self._global_hotkey.setEnabled(enabled)
        if enabled:
            self._global_hotkey.setToolTip("Click and press a shortcut, for example Ctrl+Alt+P")
        else:
            self._global_hotkey.setToolTip("Global hotkeys are only used for uiControl=button fields.")
        self._refresh_accept_enabled()

    def _refresh_accept_enabled(self) -> None:
        ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_btn is None:
            return
        if self._read_only:
            ok_btn.setEnabled(False)
            return
        if parse_ui_control(str(self._ui_control.text() or "")).control_name != "button":
            ok_btn.setEnabled(True)
            return
        ok_btn.setEnabled(self._global_hotkey.is_submittable())

    def field(self) -> F8StateSpec:
        name = str(self._name.text() or "").strip() if self._can_rename else self._original_name
        if self._can_edit_access:
            access_s = str(self._access.currentText() or "rw")
            try:
                access = F8StateAccess(access_s)
            except (TypeError, ValueError):
                access = F8StateAccess.rw
        else:
            access = self._original_access
        required = bool(self._required.isChecked()) if self._can_edit_required else self._original_required
        schema = self._schema if self._can_edit_value_schema else self._original_schema
        show_on_node = bool(self._show_on_node.isChecked())
        label = str(self._label.text() or "").strip() or msgspec.UNSET
        desc = str(self._desc.toPlainText() or "").strip() or msgspec.UNSET
        ui_control = str(self._ui_control.text() or "").strip()
        return F8StateSpec(
            name=name,
            label=label,
            description=desc,
            valueSchema=schema,
            access=access,
            required=required,
            uiControl=ui_control,
            showOnNode=show_on_node,
            redactOnPublish=self._redact_on_publish,
            editorAssist=self._editor_assist,
        )

    def global_hotkey(self) -> str:
        if parse_ui_control(str(self._ui_control.text() or "")).control_name != "button":
            return ""
        return self._global_hotkey.value()

    def accept(self) -> None:  # type: ignore[override]
        hotkey = self.global_hotkey()
        if hotkey:
            try:
                parse_global_hotkey(hotkey)
            except Exception as exc:
                show_warning(self, "Invalid global hotkey", str(exc))
                return
            conflicts = self._global_hotkey.conflicts()
            if conflicts:
                entry = conflicts[0]
                show_warning(
                    self,
                    "Global hotkey already in use",
                    f"{hotkey} is already assigned to {entry.node_id}: "
                    f"{entry.node_label or entry.node_id} - {entry.control_label or entry.field_name}",
                )
                return
        self._global_hotkey.release_capture()
        super().accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        self._global_hotkey.release_capture()
        super().done(result)


__all__ = ["_F8EditDataPortDialog", "_F8EditStateFieldDialog"]
