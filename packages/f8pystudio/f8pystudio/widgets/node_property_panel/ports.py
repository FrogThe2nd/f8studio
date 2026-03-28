from __future__ import annotations

import logging
from typing import Any, Callable

import msgspec
from f8pysdk import (
    F8DataPortSpec,
    F8OperatorSpec,
    F8StateAccess,
    F8StateSpec,
    can_add as _policy_can_add,
    can_delete as _policy_can_delete,
    can_edit_existing as _policy_can_edit_existing,
)
from f8pysdk.msgspec_codec import copy_model

from qtpy import QtCore, QtGui, QtWidgets

from ...ui_notifications import show_warning
from ...global_hotkeys.parser import parse_global_hotkey
from ...ui_control import parse_ui_control
from ..schema_builder import SchemaBuilderDialog
from ..spec_mutations import set_ports as _spec_set_ports
from ..state_controls import schema_type_any as _schema_type
from ..ui_override_mutations import (
    base_data_port_show_on_node as _base_data_port_show_on_node,
    set_data_port_show_on_node_override as _set_data_port_show_on_node_override,
)
from .common import (
    _TAB_PANEL_MARGIN,
    _node_missing_lock_info,
    _package_attr,
    _schema_from_json_obj,
    _wrap_tab_page,
)
from .containers import _F8SpecListSection, _F8SpecNameRow


logger = logging.getLogger(__name__)


class _F8GlobalHotkeyEdit(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None, *, value: str = "") -> None:
        super().__init__(parent)
        self._editor = QtWidgets.QKeySequenceEdit(self)
        self._clear_btn = QtWidgets.QPushButton("Clear", self)
        self._clear_btn.setFixedWidth(56)
        self._clear_btn.clicked.connect(self.clear)  # type: ignore[attr-defined]
        self._editor.setToolTip("Click here and press a shortcut, for example Ctrl+Alt+P")
        self._clear_btn.setToolTip("Clear the current global hotkey")

        try:
            self._editor.editingFinished.connect(self._normalize_sequence)  # type: ignore[attr-defined]
        except AttributeError:
            pass

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._editor, 1)
        layout.addWidget(self._clear_btn)
        self.set_value(value)

    def setEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        super().setEnabled(enabled)
        self._editor.setEnabled(bool(enabled))
        self._clear_btn.setEnabled(bool(enabled))

    def set_value(self, value: str) -> None:
        text = str(value or "").strip()
        with QtCore.QSignalBlocker(self._editor):
            self._editor.setKeySequence(QtGui.QKeySequence(text))

    def value(self) -> str:
        try:
            sequence = self._editor.keySequence()
        except AttributeError:
            return ""
        return str(sequence.toString(QtGui.QKeySequence.SequenceFormat.PortableText) or "").strip()

    def clear(self) -> None:
        with QtCore.QSignalBlocker(self._editor):
            self._editor.clear()

    def setToolTip(self, tooltip: str) -> None:  # type: ignore[override]
        text = str(tooltip or "")
        super().setToolTip(text)
        self._editor.setToolTip(text)
        self._clear_btn.setToolTip("Clear the current global hotkey" if text else "")

    def _normalize_sequence(self) -> None:
        text = self.value()
        if not text:
            return
        try:
            normalized = parse_global_hotkey(text).display_text
        except Exception:
            return
        self.set_value(normalized)


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
        self._schema = port.valueSchema or _schema_from_json_obj({"type": "any"})

        self._name = QtWidgets.QLineEdit(str(port.name or ""))
        self._name.setClearButtonEnabled(True)
        self._required = QtWidgets.QCheckBox()
        self._required.setChecked(bool(port.required))
        self._show_on_node = QtWidgets.QCheckBox()
        self._show_on_node.setChecked(bool(port.showOnNode))
        self._desc = QtWidgets.QPlainTextEdit(str(port.description or ""))

        self._schema_summary = QtWidgets.QLabel("")
        self._schema_summary.setStyleSheet("color: #888;")
        self._refresh_schema_summary()

        self._schema_btn = QtWidgets.QPushButton("Edit Schema...")
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
            for w in (self._name, self._required, self._show_on_node, self._desc, self._schema_btn):
                w.setEnabled(False)
            ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(False)

    def _refresh_schema_summary(self) -> None:
        t = _schema_type(self._schema)
        self._schema_summary.setText(t or "unknown")

    def _edit_schema(self) -> None:
        dialog_type = _package_attr("SchemaBuilderDialog", SchemaBuilderDialog)
        dlg = dialog_type(
            self,
            title="Edit valueSchema",
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
        ui_only: bool = False,
        lock_identity_fields: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        try:
            self._schema = field.valueSchema or _schema_from_json_obj({"type": "any"})
        except Exception:
            self._schema = _schema_from_json_obj({"type": "any"})
        self._ui_only = bool(ui_only)
        self._lock_identity_fields = bool(lock_identity_fields)
        self._read_only = bool(read_only)

        self._name = QtWidgets.QLineEdit(str(field.name or ""))
        self._name.setClearButtonEnabled(True)

        self._access = QtWidgets.QComboBox()
        self._access.addItems([e.value for e in F8StateAccess])
        try:
            self._access.setCurrentText(str(field.access.value))
        except Exception:
            self._access.setCurrentText("rw")

        self._required = QtWidgets.QCheckBox()
        self._required.setChecked(bool(field.required))

        self._show_on_node = QtWidgets.QCheckBox()
        self._show_on_node.setChecked(bool(field.showOnNode))

        self._label = QtWidgets.QLineEdit(str(field.label or ""))
        self._label.setClearButtonEnabled(True)
        self._desc = QtWidgets.QPlainTextEdit(str(field.description or ""))
        self._ui_control = QtWidgets.QLineEdit(str(field.uiControl or ""))
        self._ui_control.setClearButtonEnabled(True)
        self._global_hotkey = _F8GlobalHotkeyEdit(value=str(global_hotkey or ""))
        self._ui_control.textChanged.connect(self._refresh_global_hotkey_enabled)  # type: ignore[attr-defined]

        self._schema_summary = QtWidgets.QLabel("")
        self._schema_summary.setStyleSheet("color: #888;")
        self._refresh_schema_summary()

        self._schema_btn = QtWidgets.QPushButton("Edit Schema...")
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

        if self._ui_only or self._lock_identity_fields:
            for w in (self._name, self._access, self._required, self._schema_summary):
                w.setEnabled(False)
            self._name.setToolTip("Locked by edit policy.")
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
                self._schema_btn,
            ):
                w.setEnabled(False)
            ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(False)
        self._refresh_global_hotkey_enabled()

    def _refresh_schema_summary(self) -> None:
        t = _schema_type(self._schema)
        self._schema_summary.setText(t or "unknown")

    def _edit_schema(self) -> None:
        dialog_type = _package_attr("SchemaBuilderDialog", SchemaBuilderDialog)
        dlg = dialog_type(
            self,
            title="Edit valueSchema",
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

    def _refresh_global_hotkey_enabled(self) -> None:
        if self._read_only:
            self._global_hotkey.setEnabled(False)
            return
        enabled = parse_ui_control(str(self._ui_control.text() or "")).control_name == "button"
        self._global_hotkey.setEnabled(enabled)
        if enabled:
            self._global_hotkey.setToolTip("Click and press a shortcut, for example Ctrl+Alt+P")
            return
        self._global_hotkey.setToolTip("Global hotkeys are only used for uiControl=button fields.")

    def field(self) -> F8StateSpec:
        name = str(self._name.text() or "").strip()
        access_s = str(self._access.currentText() or "rw")
        try:
            access = F8StateAccess(access_s)
        except Exception:
            access = F8StateAccess.rw
        required = bool(self._required.isChecked())
        show_on_node = bool(self._show_on_node.isChecked())
        label = str(self._label.text() or "").strip() or msgspec.UNSET
        desc = str(self._desc.toPlainText() or "").strip() or msgspec.UNSET
        ui_control = str(self._ui_control.text() or "").strip() or msgspec.UNSET
        return F8StateSpec(
            name=name,
            label=label,
            description=desc,
            valueSchema=self._schema,
            access=access,
            required=required,
            uiControl=ui_control,
            showOnNode=show_on_node,
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
        super().accept()


class _F8SpecPortEditor(QtWidgets.QWidget):
    """
    Narrow-sidebar friendly spec ports editor.
    """

    spec_applied = QtCore.Signal()

    def __init__(self, parent=None, node=None, on_apply: Callable[[], None] | None = None):
        super().__init__(parent)
        self._node = node
        self._on_apply = on_apply
        self._missing_locked = False
        self._exec_in_can_add = False
        self._exec_out_can_add = False
        self._exec_in_can_delete = False
        self._exec_out_can_delete = False
        self._data_in_can_add = False
        self._data_out_can_add = False
        self._data_in_can_delete = False
        self._data_out_can_delete = False
        self._data_in_can_edit_existing = False
        self._data_out_can_edit_existing = False

        self._sec_exec_in = _F8SpecListSection(title="Exec In")
        self._sec_exec_out = _F8SpecListSection(title="Exec Out")
        self._sec_data_in = _F8SpecListSection(title="Data In")
        self._sec_data_out = _F8SpecListSection(title="Data Out")

        self._sec_exec_in.add_clicked.connect(lambda: self._add_exec(True))
        self._sec_exec_out.add_clicked.connect(lambda: self._add_exec(False))
        self._sec_data_in.add_clicked.connect(lambda: self._add_data(True))
        self._sec_data_out.add_clicked.connect(lambda: self._add_data(False))

        content = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        v.setSpacing(4)
        v.addWidget(self._sec_exec_in)
        v.addWidget(self._sec_exec_out)
        v.addWidget(self._sec_data_in)
        v.addWidget(self._sec_data_out)
        v.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_wrap_tab_page(content))

        self._load_from_spec()

    def _load_from_spec(self) -> None:
        self._missing_locked, _ = _node_missing_lock_info(self._node)
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        is_operator = isinstance(spec, F8OperatorSpec)
        self._sec_exec_in.setVisible(is_operator)
        self._sec_exec_out.setVisible(is_operator)

        self._sec_exec_in.clear()
        self._sec_exec_out.clear()
        self._sec_data_in.clear()
        self._sec_data_out.clear()

        if spec is None:
            return

        self._data_in_can_add = _policy_can_add(spec, "dataInPorts")
        self._data_out_can_add = _policy_can_add(spec, "dataOutPorts")
        self._data_in_can_delete = _policy_can_delete(spec, "dataInPorts")
        self._data_out_can_delete = _policy_can_delete(spec, "dataOutPorts")
        self._data_in_can_edit_existing = _policy_can_edit_existing(spec, "dataInPorts")
        self._data_out_can_edit_existing = _policy_can_edit_existing(spec, "dataOutPorts")
        if is_operator:
            self._exec_in_can_add = _policy_can_add(spec, "execInPorts")
            self._exec_out_can_add = _policy_can_add(spec, "execOutPorts")
            self._exec_in_can_delete = _policy_can_delete(spec, "execInPorts")
            self._exec_out_can_delete = _policy_can_delete(spec, "execOutPorts")
        else:
            self._exec_in_can_add = False
            self._exec_out_can_add = False
            self._exec_in_can_delete = False
            self._exec_out_can_delete = False

        self._sec_exec_in.set_add_visible(bool(self._exec_in_can_add) and not self._missing_locked)
        self._sec_exec_out.set_add_visible(bool(self._exec_out_can_add) and not self._missing_locked)
        self._sec_data_in.set_add_visible(bool(self._data_in_can_add) and not self._missing_locked)
        self._sec_data_out.set_add_visible(bool(self._data_out_can_add) and not self._missing_locked)

        if is_operator:
            for name in list(spec.execInPorts or []):
                self._sec_exec_in.add_row(self._make_exec_row(str(name), is_in=True))
            for name in list(spec.execOutPorts or []):
                self._sec_exec_out.add_row(self._make_exec_row(str(name), is_in=False))

        try:
            data_in_ports = list(spec.dataInPorts or [])
        except Exception:
            data_in_ports = []
        try:
            data_out_ports = list(spec.dataOutPorts or [])
        except Exception:
            data_out_ports = []

        for p in data_in_ports:
            self._sec_data_in.add_row(self._make_data_row(p, is_in=True))
        for p in data_out_ports:
            self._sec_data_out.add_row(self._make_data_row(p, is_in=False))

    def _make_exec_row(self, name: str, *, is_in: bool) -> _F8SpecNameRow:
        row = _F8SpecNameRow(name=name, placeholder="port name")
        row.edit_clicked.connect(lambda: self._edit_exec(row))
        row.delete_clicked.connect(lambda: self._delete_row(row))
        row.name_committed.connect(lambda _v: self._commit())
        row.setProperty("_port_dir", "exec_in" if is_in else "exec_out")
        allow_delete = bool((self._exec_in_can_delete if is_in else self._exec_out_can_delete) and not self._missing_locked)
        row.set_row_editable(allow_rename=False, allow_delete=allow_delete, allow_edit=False)
        return row

    def _make_data_row(self, port: F8DataPortSpec, *, is_in: bool) -> _F8SpecNameRow:
        row = _F8SpecNameRow(name=str(port.name or ""), placeholder="port name", show_eye=True)
        row.setProperty("_port", port)
        row.edit_clicked.connect(lambda: self._edit_data(row))
        row.delete_clicked.connect(lambda: self._delete_row(row))
        row.name_committed.connect(lambda v: self._rename_data(row, v))
        row.show_on_node_changed.connect(lambda v: self._toggle_data_show_on_node(row, bool(v)))  # type: ignore[attr-defined]
        row.setToolTip(self._data_tooltip(port))
        row.setProperty("_port_dir", "data_in" if is_in else "data_out")
        can_delete = bool(self._data_in_can_delete if is_in else self._data_out_can_delete)
        can_edit_existing = bool(self._data_in_can_edit_existing if is_in else self._data_out_can_edit_existing)
        try:
            required = bool(port.required)
        except (AttributeError, TypeError, ValueError):
            required = False
        allow_delete = bool(can_delete and not self._missing_locked and not required)
        allow_edit = bool(not self._missing_locked)
        row.set_row_editable(
            allow_rename=False,
            allow_delete=allow_delete,
            allow_edit=allow_edit,
        )
        show = bool(self._node.data_port_show_on_node(str(port.name or ""), is_in=bool(is_in)))  # type: ignore[attr-defined]
        row.set_show_on_node(bool(show))
        return row

    def _toggle_data_show_on_node(self, row: _F8SpecNameRow, show_on_node: bool) -> None:
        if self._missing_locked:
            return
        dir_s = str(row.property("_port_dir") or "")
        is_in = dir_s == "data_in"
        port = row.property("_port")
        name = ""
        if isinstance(port, F8DataPortSpec):
            name = str(port.name or "")
        if not name:
            name = str(row.name_edit.text() or "").strip()
        self._apply_data_port_ui_override(name, bool(show_on_node), is_in=bool(is_in))
        row.set_show_on_node(bool(show_on_node))

    def _data_tooltip(self, port: F8DataPortSpec) -> str:
        req = bool(port.required)
        desc = str(port.description or "").strip()
        vs = port.valueSchema
        t = _schema_type(vs)
        parts = [f"required={req}", f"type={t or 'unknown'}"]
        if desc:
            parts.append(desc)
        return "\n".join(parts)

    def _row_is_required_data_port(self, row: QtWidgets.QWidget) -> bool:
        port = row.property("_port")
        if not isinstance(port, F8DataPortSpec):
            return False
        try:
            return bool(port.required)
        except (AttributeError, TypeError, ValueError):
            return False

    def _edit_exec(self, row: _F8SpecNameRow) -> None:
        if self._missing_locked:
            return
        dir_s = str(row.property("_port_dir") or "")
        return

    def _edit_data(self, row: _F8SpecNameRow) -> None:
        dir_s = str(row.property("_port_dir") or "")
        can_edit_existing = bool(
            self._data_in_can_edit_existing if dir_s == "data_in" else self._data_out_can_edit_existing
        )
        ui_only = bool(not can_edit_existing)
        read_only = bool(self._missing_locked)
        port = row.property("_port")
        if not isinstance(port, F8DataPortSpec):
            port = F8DataPortSpec(
                name=row.name_edit.text(), required=True, valueSchema=_schema_from_json_obj({"type": "any"})
            )
        dialog_type = _package_attr("_F8EditDataPortDialog", _F8EditDataPortDialog)
        dlg = dialog_type(
            self,
            title="Edit data port",
            port=port,
            ui_only=ui_only,
            lock_identity_fields=bool(can_edit_existing),
            read_only=read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_port = dlg.port()
        if ui_only and not read_only:
            show_on_node = bool(new_port.showOnNode)
            self._apply_data_port_ui_override(str(port.name or ""), bool(show_on_node), is_in=(dir_s == "data_in"))
            self._load_from_spec()
            return
        row.setProperty("_port", new_port)
        row.name_edit.setText(str(new_port.name or ""))
        row.setToolTip(self._data_tooltip(new_port))
        self._commit()

    def _apply_data_port_ui_override(self, name: str, show_on_node: bool, *, is_in: bool) -> None:
        node = self._node
        if node is None:
            return
        spec = node.spec
        base_show = _base_data_port_show_on_node(spec, name=str(name or "").strip(), is_in=bool(is_in))
        _set_data_port_show_on_node_override(
            node,
            name=str(name or "").strip(),
            is_in=bool(is_in),
            show_on_node=bool(show_on_node),
            base_show_on_node=bool(base_show),
        )

    def _rename_data(self, row: _F8SpecNameRow, name: str) -> None:
        if self._missing_locked:
            return
        if self._row_is_required_data_port(row):
            return
        dir_s = str(row.property("_port_dir") or "")
        return
        port = row.property("_port")
        if not isinstance(port, F8DataPortSpec):
            port = F8DataPortSpec(name=name, required=True, valueSchema=_schema_from_json_obj({"type": "any"}))
        else:
            port = copy_model(port, deep=True)
            port.name = name
        row.setProperty("_port", port)
        row.setToolTip(self._data_tooltip(port))
        self._commit()

    def _delete_row(self, row: QtWidgets.QWidget) -> None:
        if self._missing_locked:
            return
        dir_s = str(row.property("_port_dir") or "")
        if dir_s == "exec_in" and not self._exec_in_can_delete:
            return
        if dir_s == "exec_out" and not self._exec_out_can_delete:
            return
        if dir_s == "data_in" and not self._data_in_can_delete:
            return
        if dir_s == "data_out" and not self._data_out_can_delete:
            return
        if (dir_s == "data_in" or dir_s == "data_out") and self._row_is_required_data_port(row):
            return
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _add_exec(self, is_in: bool) -> None:
        if self._missing_locked:
            return
        if not (self._exec_in_can_add if is_in else self._exec_out_can_add):
            return
        row = self._make_exec_row("", is_in=is_in)
        (self._sec_exec_in if is_in else self._sec_exec_out).add_row(row)
        row.name_edit.setFocus()

    def _add_data(self, is_in: bool) -> None:
        if self._missing_locked:
            return
        if not (self._data_in_can_add if is_in else self._data_out_can_add):
            return
        port = F8DataPortSpec(
            name="",
            required=False,
            showOnNode=False,
            description=msgspec.UNSET,
            valueSchema=_schema_from_json_obj({"type": "any"}),
        )
        row = self._make_data_row(port, is_in=is_in)
        (self._sec_data_in if is_in else self._sec_data_out).add_row(row)
        self._edit_data(row)

    def _commit(self) -> None:
        if self._missing_locked:
            return
        if self._node is None:
            return
        spec = self._node.spec

        exec_in: list[str] = []
        exec_out: list[str] = []
        if isinstance(spec, F8OperatorSpec):
            for r in self._sec_exec_in.rows():
                name = str(r.name_edit.text() or "").strip()
                if name:
                    exec_in.append(name)
            for r in self._sec_exec_out.rows():
                name = str(r.name_edit.text() or "").strip()
                if name:
                    exec_out.append(name)

        data_in: list[F8DataPortSpec] = []
        data_out: list[F8DataPortSpec] = []
        for r in self._sec_data_in.rows():
            port = r.property("_port")
            if isinstance(port, F8DataPortSpec) and str(port.name or "").strip():
                data_in.append(port)
        for r in self._sec_data_out.rows():
            port = r.property("_port")
            if isinstance(port, F8DataPortSpec) and str(port.name or "").strip():
                data_out.append(port)

        spec2 = _spec_set_ports(spec, data_in=data_in, data_out=data_out, exec_in=exec_in, exec_out=exec_out)
        if spec2 is not spec:
            self._node.spec = spec2

        if self._on_apply:
            self._on_apply()
        self.spec_applied.emit()
