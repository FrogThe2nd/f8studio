from __future__ import annotations

import logging
from typing import Any, Callable

import msgspec
from f8pysdk import F8DataPortSpec, F8OperatorSpec, F8StateAccess, F8StateSpec
from f8pysdk.msgspec_codec import copy_model

from qtpy import QtCore, QtWidgets

from ...ui_notifications import show_warning
from ..schema_builder import SchemaBuilderDialog
from ..spec_mutations import set_ports as _spec_set_ports
from ..state_widget_api import schema_type_any as _schema_type
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
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._ui_only = bool(ui_only)
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

        if self._ui_only:
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
        ui_only: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        try:
            self._schema = field.valueSchema or _schema_from_json_obj({"type": "any"})
        except Exception:
            self._schema = _schema_from_json_obj({"type": "any"})
        self._ui_only = bool(ui_only)
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
        self._ui_lang = QtWidgets.QLineEdit(str(field.uiLanguage or ""))
        self._ui_lang.setClearButtonEnabled(True)

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
        form.addRow("uiLanguage", self._ui_lang)

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

        if self._ui_only:
            for w in (self._name, self._access, self._required, self._schema_summary):
                w.setEnabled(False)
            self._name.setToolTip("Locked by spec (required/non-editable).")
        if self._read_only:
            for w in (
                self._name,
                self._access,
                self._required,
                self._show_on_node,
                self._label,
                self._desc,
                self._ui_control,
                self._ui_lang,
                self._schema_btn,
            ):
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
        ui_lang = str(self._ui_lang.text() or "").strip() or msgspec.UNSET
        return F8StateSpec(
            name=name,
            label=label,
            description=desc,
            valueSchema=self._schema,
            access=access,
            required=required,
            uiControl=ui_control,
            uiLanguage=ui_lang,
            showOnNode=show_on_node,
        )


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
        self._editable_exec_in = False
        self._editable_exec_out = False
        self._editable_data_in = False
        self._editable_data_out = False

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

        self._editable_data_in = bool(spec.editableDataInPorts)  # type: ignore[attr-defined]
        self._editable_data_out = bool(spec.editableDataOutPorts)  # type: ignore[attr-defined]
        if is_operator:
            self._editable_exec_in = bool(spec.editableExecInPorts)  # type: ignore[attr-defined]
            self._editable_exec_out = bool(spec.editableExecOutPorts)  # type: ignore[attr-defined]
        else:
            self._editable_exec_in = False
            self._editable_exec_out = False

        self._sec_exec_in.set_add_visible(bool(self._editable_exec_in) and not self._missing_locked)
        self._sec_exec_out.set_add_visible(bool(self._editable_exec_out) and not self._missing_locked)
        self._sec_data_in.set_add_visible(bool(self._editable_data_in) and not self._missing_locked)
        self._sec_data_out.set_add_visible(bool(self._editable_data_out) and not self._missing_locked)

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
        editable = bool(self._editable_exec_in if is_in else self._editable_exec_out)
        allow_edit = editable and not self._missing_locked
        row.set_row_editable(allow_rename=allow_edit, allow_delete=allow_edit, allow_edit=allow_edit)
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
        editable = bool(self._editable_data_in if is_in else self._editable_data_out)
        try:
            required = bool(port.required)
        except (AttributeError, TypeError, ValueError):
            required = False
        allow_mutate = bool(editable and not self._missing_locked and not required)
        # Even when spec ports are not editable, allow opening the dialog to edit UI-only fields (showOnNode).
        row.set_row_editable(
            allow_rename=allow_mutate,
            allow_delete=allow_mutate,
            allow_edit=True,
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
        if (dir_s == "exec_in" and not self._editable_exec_in) or (
            dir_s == "exec_out" and not self._editable_exec_out
        ):
            return
        dlg = _F8EditExecPortDialog(self, title="Edit exec port", name=row.name_edit.text())
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        row.name_edit.setText(dlg.name())
        self._commit()

    def _edit_data(self, row: _F8SpecNameRow) -> None:
        dir_s = str(row.property("_port_dir") or "")
        ui_only = bool(
            (dir_s == "data_in" and not self._editable_data_in)
            or (dir_s == "data_out" and not self._editable_data_out)
        )
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
        if (dir_s == "data_in" and not self._editable_data_in) or (
            dir_s == "data_out" and not self._editable_data_out
        ):
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
        if dir_s == "exec_in" and not self._editable_exec_in:
            return
        if dir_s == "exec_out" and not self._editable_exec_out:
            return
        if dir_s == "data_in" and not self._editable_data_in:
            return
        if dir_s == "data_out" and not self._editable_data_out:
            return
        if (dir_s == "data_in" or dir_s == "data_out") and self._row_is_required_data_port(row):
            return
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _add_exec(self, is_in: bool) -> None:
        if self._missing_locked:
            return
        if not (self._editable_exec_in if is_in else self._editable_exec_out):
            return
        row = self._make_exec_row("", is_in=is_in)
        (self._sec_exec_in if is_in else self._sec_exec_out).add_row(row)
        row.name_edit.setFocus()

    def _add_data(self, is_in: bool) -> None:
        if self._missing_locked:
            return
        if not (self._editable_data_in if is_in else self._editable_data_out):
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
