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

from ....operators.patch_hub import OPERATOR_CLASS as PATCH_HUB_OPERATOR_CLASS
from ....operators.patch_hub import normalize_patch_hub_spec
from ...dialogs.node_spec_edit_dialogs import _F8EditDataPortDialog, _F8EditStateFieldDialog
from ....nodegraph.spec_mutations import set_ports as _spec_set_ports
from ....nodegraph.state_schema import schema_type_any as _schema_type
from ....nodegraph.ui_override_mutations import (
    base_data_port_show_on_node as _base_data_port_show_on_node,
    set_list_order_override as _set_list_order_override,
    set_data_port_show_on_node_override as _set_data_port_show_on_node_override,
)
from ...support.node_property_support import node_missing_lock_info, schema_from_json_obj_loose
from .common import (
    _TAB_PANEL_MARGIN,
    _wrap_tab_page,
)
from .containers import _F8SpecListSection, _F8SpecNameRow


logger = logging.getLogger(__name__)


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
        self._state_can_add = False
        self._state_can_delete = False
        self._state_can_edit_existing = False
        self._patch_data_can_add = False
        self._patch_data_can_delete = False
        self._patch_data_can_edit_existing = False
        self._is_patch_hub = False

        self._sec_exec_in = _F8SpecListSection(self, title="Exec In")
        self._sec_exec_out = _F8SpecListSection(self, title="Exec Out")
        self._sec_data_in = _F8SpecListSection(self, title="Data In")
        self._sec_data_out = _F8SpecListSection(self, title="Data Out")
        self._sec_patch_data = _F8SpecListSection(self, title="Data Terminals")
        self._sec_patch_state = _F8SpecListSection(self, title="State Terminals")

        self._sec_exec_in.add_clicked.connect(lambda: self._add_exec(True))
        self._sec_exec_out.add_clicked.connect(lambda: self._add_exec(False))
        self._sec_data_in.add_clicked.connect(lambda: self._add_data(True))
        self._sec_data_out.add_clicked.connect(lambda: self._add_data(False))
        self._sec_patch_data.add_clicked.connect(self._add_patch_data)
        self._sec_patch_state.add_clicked.connect(self._add_patch_state)
        self._sec_exec_in.rows_reordered.connect(lambda names: self._on_rows_reordered("execInPorts", names))
        self._sec_exec_out.rows_reordered.connect(lambda names: self._on_rows_reordered("execOutPorts", names))
        self._sec_data_in.rows_reordered.connect(lambda names: self._on_rows_reordered("dataInPorts", names))
        self._sec_data_out.rows_reordered.connect(lambda names: self._on_rows_reordered("dataOutPorts", names))
        self._sec_patch_data.rows_reordered.connect(lambda names: self._on_rows_reordered("dataInPorts", names))
        self._sec_patch_state.rows_reordered.connect(lambda names: self._on_rows_reordered("stateFields", names))

        content = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        v.setSpacing(4)
        v.addWidget(self._sec_exec_in)
        v.addWidget(self._sec_exec_out)
        v.addWidget(self._sec_data_in)
        v.addWidget(self._sec_data_out)
        v.addWidget(self._sec_patch_data)
        v.addWidget(self._sec_patch_state)
        v.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_wrap_tab_page(content))

        self._load_from_spec()

    def _load_from_spec(self) -> None:
        self._missing_locked, _ = node_missing_lock_info(self._node)
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        self._is_patch_hub = bool(
            isinstance(spec, F8OperatorSpec) and str(spec.operatorClass or "").strip() == PATCH_HUB_OPERATOR_CLASS
        )
        if self._is_patch_hub and isinstance(spec, F8OperatorSpec):
            spec = normalize_patch_hub_spec(spec)
        is_operator = isinstance(spec, F8OperatorSpec)
        self._sec_exec_in.setVisible(bool(is_operator and not self._is_patch_hub))
        self._sec_exec_out.setVisible(bool(is_operator and not self._is_patch_hub))
        self._sec_data_in.setVisible(not self._is_patch_hub)
        self._sec_data_out.setVisible(not self._is_patch_hub)
        self._sec_patch_data.setVisible(self._is_patch_hub)
        self._sec_patch_state.setVisible(self._is_patch_hub)

        self._sec_exec_in.clear()
        self._sec_exec_out.clear()
        self._sec_data_in.clear()
        self._sec_data_out.clear()
        self._sec_patch_data.clear()
        self._sec_patch_state.clear()

        if spec is None:
            return

        self._data_in_can_add = _policy_can_add(spec, "dataInPorts")
        self._data_out_can_add = _policy_can_add(spec, "dataOutPorts")
        self._data_in_can_delete = _policy_can_delete(spec, "dataInPorts")
        self._data_out_can_delete = _policy_can_delete(spec, "dataOutPorts")
        self._data_in_can_edit_existing = _policy_can_edit_existing(spec, "dataInPorts")
        self._data_out_can_edit_existing = _policy_can_edit_existing(spec, "dataOutPorts")
        self._state_can_add = _policy_can_add(spec, "stateFields")
        self._state_can_delete = _policy_can_delete(spec, "stateFields")
        self._state_can_edit_existing = _policy_can_edit_existing(spec, "stateFields")
        self._patch_data_can_add = bool(self._data_in_can_add and self._data_out_can_add)
        self._patch_data_can_delete = bool(self._data_in_can_delete and self._data_out_can_delete)
        self._patch_data_can_edit_existing = bool(self._data_in_can_edit_existing and self._data_out_can_edit_existing)
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
        self._sec_patch_data.set_add_visible(bool(self._patch_data_can_add) and not self._missing_locked)
        self._sec_patch_state.set_add_visible(bool(self._state_can_add) and not self._missing_locked)

        if is_operator:
            try:
                exec_in_names = list(self._node.ordered_exec_port_names(is_in=True) or [])
            except Exception:
                exec_in_names = list(spec.execInPorts or [])
            for name in exec_in_names:
                self._sec_exec_in.add_row(self._make_exec_row(str(name), is_in=True))
            try:
                exec_out_names = list(self._node.ordered_exec_port_names(is_in=False) or [])
            except Exception:
                exec_out_names = list(spec.execOutPorts or [])
            for name in exec_out_names:
                self._sec_exec_out.add_row(self._make_exec_row(str(name), is_in=False))

        try:
            data_in_ports = list(self._node.ordered_data_port_specs(is_in=True) or [])
        except Exception:
            data_in_ports = list(spec.dataInPorts or [])
        try:
            data_out_ports = list(self._node.ordered_data_port_specs(is_in=False) or [])
        except Exception:
            data_out_ports = list(spec.dataOutPorts or [])

        if self._is_patch_hub:
            for p in data_in_ports:
                self._sec_patch_data.add_row(self._make_patch_data_row(p))
            try:
                state_fields = list(self._node.ordered_state_field_specs() or [])
            except Exception:
                state_fields = list(spec.stateFields or [])
            for field in state_fields:
                self._sec_patch_state.add_row(self._make_patch_state_row(field))
        else:
            for p in data_in_ports:
                self._sec_data_in.add_row(self._make_data_row(p, is_in=True))
            for p in data_out_ports:
                self._sec_data_out.add_row(self._make_data_row(p, is_in=False))

        for section in (
            self._sec_exec_in,
            self._sec_exec_out,
            self._sec_data_in,
            self._sec_data_out,
            self._sec_patch_data,
            self._sec_patch_state,
        ):
            section.set_drag_enabled(not self._missing_locked)

    def _base_order_for_key(self, key: str, *, spec: F8OperatorSpec | Any | None = None) -> list[str]:
        current_spec = spec
        if current_spec is None:
            try:
                current_spec = self._node.spec
            except Exception:
                current_spec = None
        if current_spec is None:
            return []

        normalized_key = str(key or "").strip()
        if normalized_key == "execInPorts" and isinstance(current_spec, F8OperatorSpec):
            return [str(name or "").strip() for name in list(current_spec.execInPorts or []) if str(name or "").strip()]
        if normalized_key == "execOutPorts" and isinstance(current_spec, F8OperatorSpec):
            return [str(name or "").strip() for name in list(current_spec.execOutPorts or []) if str(name or "").strip()]
        if normalized_key == "dataInPorts":
            return [str(port.name or "").strip() for port in list(current_spec.dataInPorts or []) if str(port.name or "").strip()]
        if normalized_key == "dataOutPorts":
            return [str(port.name or "").strip() for port in list(current_spec.dataOutPorts or []) if str(port.name or "").strip()]
        if normalized_key == "stateFields":
            return [str(field.name or "").strip() for field in list(current_spec.stateFields or []) if str(field.name or "").strip()]
        return []

    def _on_rows_reordered(self, key: str, ordered_names: list[str]) -> None:
        if self._missing_locked or self._node is None:
            return
        _set_list_order_override(
            self._node,
            key=str(key or "").strip(),
            order=[str(name or "").strip() for name in list(ordered_names or [])],
            base_order=self._base_order_for_key(str(key or "").strip()),
            rebuild=True,
        )

    @staticmethod
    def _rows_in_base_order(
        rows: list[_F8SpecNameRow],
        *,
        base_order: list[str],
    ) -> list[_F8SpecNameRow]:
        rows_by_original: dict[str, _F8SpecNameRow] = {}
        appended: list[_F8SpecNameRow] = []
        for row in rows:
            original = str(row.property("_original_order_key") or "").strip()
            current = str(row.property("_order_key") or "").strip()
            if original:
                rows_by_original[original] = row
                continue
            if current:
                appended.append(row)

        ordered_rows: list[_F8SpecNameRow] = []
        for name in list(base_order or []):
            row = rows_by_original.pop(str(name or "").strip(), None)
            if row is not None:
                ordered_rows.append(row)
        ordered_rows.extend(appended)
        for row in rows:
            if row in ordered_rows:
                continue
            ordered_rows.append(row)
        return ordered_rows

    def _sync_list_orders_after_commit(self, spec: Any) -> None:
        if self._node is None:
            return
        _set_list_order_override(
            self._node,
            key="execInPorts",
            order=[str(row.property("_order_key") or "").strip() for row in self._sec_exec_in.rows()],
            base_order=self._base_order_for_key("execInPorts", spec=spec),
            rebuild=False,
        )
        _set_list_order_override(
            self._node,
            key="execOutPorts",
            order=[str(row.property("_order_key") or "").strip() for row in self._sec_exec_out.rows()],
            base_order=self._base_order_for_key("execOutPorts", spec=spec),
            rebuild=False,
        )
        _set_list_order_override(
            self._node,
            key="dataInPorts",
            order=[str(row.property("_order_key") or "").strip() for row in (self._sec_patch_data.rows() if self._is_patch_hub else self._sec_data_in.rows())],
            base_order=self._base_order_for_key("dataInPorts", spec=spec),
            rebuild=False,
        )
        _set_list_order_override(
            self._node,
            key="dataOutPorts",
            order=[str(row.property("_order_key") or "").strip() for row in self._sec_data_out.rows()],
            base_order=self._base_order_for_key("dataOutPorts", spec=spec),
            rebuild=False,
        )
        _set_list_order_override(
            self._node,
            key="stateFields",
            order=[str(row.property("_order_key") or "").strip() for row in self._sec_patch_state.rows()],
            base_order=self._base_order_for_key("stateFields", spec=spec),
            rebuild=False,
        )

    def _make_exec_row(self, name: str, *, is_in: bool) -> _F8SpecNameRow:
        row = _F8SpecNameRow(self, name=name, placeholder="port name")
        row.setProperty("_original_order_key", str(name or "").strip())
        row.edit_clicked.connect(lambda: self._edit_exec(row))
        row.delete_clicked.connect(lambda: self._delete_row(row))
        row.name_committed.connect(lambda _v: self._commit())
        row.setProperty("_port_dir", "exec_in" if is_in else "exec_out")
        allow_delete = bool((self._exec_in_can_delete if is_in else self._exec_out_can_delete) and not self._missing_locked)
        row.set_row_editable(allow_rename=False, allow_delete=allow_delete, allow_edit=False)
        return row

    def _make_data_row(self, port: F8DataPortSpec, *, is_in: bool) -> _F8SpecNameRow:
        row = _F8SpecNameRow(self, name=str(port.name or ""), placeholder="port name", show_eye=True)
        row.setProperty("_original_order_key", str(port.name or "").strip())
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

    def _make_patch_data_row(self, port: F8DataPortSpec) -> _F8SpecNameRow:
        row = _F8SpecNameRow(self, name=str(port.name or ""), placeholder="terminal name")
        row.setProperty("_original_order_key", str(port.name or "").strip())
        row.setProperty("_port", port)
        row.setProperty("_port_dir", "patch_data")
        row.edit_clicked.connect(lambda: self._edit_patch_data(row))
        row.delete_clicked.connect(lambda: self._delete_row(row))
        row.name_committed.connect(lambda value: self._rename_patch_data(row, value))
        row.setToolTip(self._data_tooltip(port))
        row.set_row_editable(
            allow_rename=bool(not self._missing_locked),
            allow_delete=bool(self._patch_data_can_delete and not self._missing_locked),
            allow_edit=bool(not self._missing_locked),
        )
        return row

    def _make_patch_state_row(self, field: F8StateSpec) -> _F8SpecNameRow:
        row = _F8SpecNameRow(self, name=str(field.name or ""), placeholder="terminal name")
        row.setProperty("_original_order_key", str(field.name or "").strip())
        row.setProperty("_field", field)
        row.setProperty("_port_dir", "patch_state")
        row.edit_clicked.connect(lambda: self._edit_patch_state(row))
        row.delete_clicked.connect(lambda: self._delete_row(row))
        row.name_committed.connect(lambda value: self._rename_patch_state(row, value))
        row.setToolTip(self._state_terminal_tooltip(field))
        row.set_row_editable(
            allow_rename=bool(not self._missing_locked),
            allow_delete=bool(self._state_can_delete and not self._missing_locked and not bool(field.required)),
            allow_edit=bool(not self._missing_locked),
        )
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

    def _state_terminal_tooltip(self, field: F8StateSpec) -> str:
        desc = str(field.description or "").strip()
        parts = [f"type={_schema_type(field.valueSchema) or 'unknown'}"]
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
                name=row.name_edit.text(), required=True, valueSchema=schema_from_json_obj_loose({"type": "any"})
            )
        dlg = _F8EditDataPortDialog(
            self,
            title="Edit data port",
            port=port,
            ui_only=ui_only,
            lock_identity_fields=False,
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

    def _edit_patch_data(self, row: _F8SpecNameRow) -> None:
        can_edit_existing = bool(self._patch_data_can_edit_existing)
        ui_only = bool(not can_edit_existing)
        read_only = bool(self._missing_locked)
        port = row.property("_port")
        if not isinstance(port, F8DataPortSpec):
            port = F8DataPortSpec(
                name=str(row.name_edit.text() or "").strip(),
                required=False,
                showOnNode=True,
                valueSchema=schema_from_json_obj_loose({"type": "any"}),
            )
        dlg = _F8EditDataPortDialog(
            self,
            title="Edit data terminal",
            port=port,
            ui_only=ui_only,
            lock_identity_fields=False,
            read_only=read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_port = dlg.port()
        row.setProperty(
            "_port",
            copy_model(new_port, update={"required": False, "showOnNode": True}),
        )
        row.name_edit.setText(str(new_port.name or ""))
        row.setToolTip(self._data_tooltip(new_port))
        self._commit()

    def _edit_patch_state(self, row: _F8SpecNameRow) -> None:
        field = row.property("_field")
        if not isinstance(field, F8StateSpec):
            field = F8StateSpec(
                name=str(row.name_edit.text() or "").strip(),
                valueSchema=schema_from_json_obj_loose({"type": "any"}),
                access=F8StateAccess.rw,
                required=False,
                showOnNode=True,
            )
        dlg = _F8EditStateFieldDialog(
            self,
            title="Edit state terminal",
            field=field,
            ui_only=False,
            read_only=bool(self._missing_locked),
        )
        for widget in (dlg._access, dlg._required, dlg._show_on_node, dlg._ui_control, dlg._global_hotkey):  # type: ignore[attr-defined]
            widget.setEnabled(False)
        dlg._access.setCurrentText(F8StateAccess.rw.value)  # type: ignore[attr-defined]
        dlg._required.setChecked(False)  # type: ignore[attr-defined]
        dlg._show_on_node.setChecked(True)  # type: ignore[attr-defined]
        dlg._ui_control.setText("")  # type: ignore[attr-defined]
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        edited = dlg.field()
        normalized = copy_model(
            edited,
            update={
                "access": F8StateAccess.rw,
                "required": False,
                "showOnNode": True,
                "uiControl": msgspec.UNSET,
                "valueSchema": edited.valueSchema or schema_from_json_obj_loose({"type": "any"}),
            },
        )
        row.setProperty("_field", normalized)
        row.name_edit.setText(str(normalized.name or ""))
        row.setToolTip(self._state_terminal_tooltip(normalized))
        self._commit()

    def _rename_patch_data(self, row: _F8SpecNameRow, name: str) -> None:
        if self._missing_locked:
            return
        clean_name = str(name or "").strip()
        port = row.property("_port")
        if not isinstance(port, F8DataPortSpec):
            port = F8DataPortSpec(
                name=clean_name,
                required=False,
                showOnNode=True,
                valueSchema=schema_from_json_obj_loose({"type": "any"}),
            )
        else:
            port = copy_model(
                port,
                update={
                    "name": clean_name,
                    "required": False,
                    "showOnNode": True,
                },
            )
        row.setProperty("_port", port)
        row.setToolTip(self._data_tooltip(port))
        self._commit()

    def _rename_patch_state(self, row: _F8SpecNameRow, name: str) -> None:
        if self._missing_locked:
            return
        clean_name = str(name or "").strip()
        field = row.property("_field")
        if not isinstance(field, F8StateSpec):
            field = F8StateSpec(
                name=clean_name,
                valueSchema=schema_from_json_obj_loose({"type": "any"}),
                access=F8StateAccess.rw,
                required=False,
                showOnNode=True,
            )
        else:
            field = copy_model(
                field,
                update={
                    "name": clean_name,
                    "access": F8StateAccess.rw,
                    "required": False,
                    "showOnNode": True,
                    "uiControl": msgspec.UNSET,
                },
            )
        row.setProperty("_field", field)
        row.setToolTip(self._state_terminal_tooltip(field))
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
            port = F8DataPortSpec(name=name, required=True, valueSchema=schema_from_json_obj_loose({"type": "any"}))
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
        if dir_s == "patch_data" and not self._patch_data_can_delete:
            return
        if dir_s == "patch_state" and not self._state_can_delete:
            return
        if (dir_s == "data_in" or dir_s == "data_out") and self._row_is_required_data_port(row):
            return
        if dir_s == "exec_in":
            self._sec_exec_in.remove_row(row)
        elif dir_s == "exec_out":
            self._sec_exec_out.remove_row(row)
        elif dir_s == "data_in":
            self._sec_data_in.remove_row(row)
        elif dir_s == "data_out":
            self._sec_data_out.remove_row(row)
        elif dir_s == "patch_data":
            self._sec_patch_data.remove_row(row)
        elif dir_s == "patch_state":
            self._sec_patch_state.remove_row(row)
        else:
            try:
                row.setVisible(False)
            except (AttributeError, RuntimeError, TypeError):
                pass
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
            valueSchema=schema_from_json_obj_loose({"type": "any"}),
        )
        row = self._make_data_row(port, is_in=is_in)
        (self._sec_data_in if is_in else self._sec_data_out).add_row(row)
        self._edit_data(row)

    def _add_patch_data(self) -> None:
        if self._missing_locked or not self._patch_data_can_add:
            return
        port = F8DataPortSpec(
            name="",
            required=False,
            showOnNode=True,
            description=msgspec.UNSET,
            valueSchema=schema_from_json_obj_loose({"type": "any"}),
        )
        row = self._make_patch_data_row(port)
        self._sec_patch_data.add_row(row)
        self._edit_patch_data(row)

    def _add_patch_state(self) -> None:
        if self._missing_locked or not self._state_can_add:
            return
        field = F8StateSpec(
            name="",
            valueSchema=schema_from_json_obj_loose({"type": "any"}),
            access=F8StateAccess.rw,
            required=False,
            showOnNode=True,
        )
        row = self._make_patch_state_row(field)
        self._sec_patch_state.add_row(row)
        self._edit_patch_state(row)

    def _commit(self) -> None:
        if self._missing_locked:
            return
        if self._node is None:
            return
        spec = self._node.spec

        if self._is_patch_hub and isinstance(spec, F8OperatorSpec):
            data_terminals: list[F8DataPortSpec] = []
            for row in self._rows_in_base_order(
                self._sec_patch_data.rows(),
                base_order=self._base_order_for_key("dataInPorts", spec=spec),
            ):
                port = row.property("_port")
                if not isinstance(port, F8DataPortSpec):
                    continue
                if not str(port.name or "").strip():
                    continue
                data_terminals.append(copy_model(port, update={"required": False, "showOnNode": True}))

            state_terminals: list[F8StateSpec] = []
            for row in self._rows_in_base_order(
                self._sec_patch_state.rows(),
                base_order=self._base_order_for_key("stateFields", spec=spec),
            ):
                field = row.property("_field")
                if not isinstance(field, F8StateSpec):
                    continue
                if not str(field.name or "").strip():
                    continue
                state_terminals.append(
                    copy_model(
                        field,
                        update={
                            "access": F8StateAccess.rw,
                            "required": False,
                            "showOnNode": True,
                            "uiControl": msgspec.UNSET,
                        },
                    )
                )

            spec2 = normalize_patch_hub_spec(
                copy_model(
                    spec,
                    update={
                        "dataInPorts": data_terminals,
                        "dataOutPorts": [copy_model(port, update={}) for port in data_terminals],
                        "stateFields": state_terminals,
                        "execInPorts": [],
                        "execOutPorts": [],
                    },
                )
            )
        else:
            exec_in: list[str] = []
            exec_out: list[str] = []
            if isinstance(spec, F8OperatorSpec):
                for r in self._rows_in_base_order(
                    self._sec_exec_in.rows(),
                    base_order=self._base_order_for_key("execInPorts", spec=spec),
                ):
                    name = str(r.name_edit.text() or "").strip()
                    if name:
                        exec_in.append(name)
                for r in self._rows_in_base_order(
                    self._sec_exec_out.rows(),
                    base_order=self._base_order_for_key("execOutPorts", spec=spec),
                ):
                    name = str(r.name_edit.text() or "").strip()
                    if name:
                        exec_out.append(name)

            data_in: list[F8DataPortSpec] = []
            data_out: list[F8DataPortSpec] = []
            for r in self._rows_in_base_order(
                self._sec_data_in.rows(),
                base_order=self._base_order_for_key("dataInPorts", spec=spec),
            ):
                port = r.property("_port")
                if isinstance(port, F8DataPortSpec) and str(port.name or "").strip():
                    data_in.append(port)
            for r in self._rows_in_base_order(
                self._sec_data_out.rows(),
                base_order=self._base_order_for_key("dataOutPorts", spec=spec),
            ):
                port = r.property("_port")
                if isinstance(port, F8DataPortSpec) and str(port.name or "").strip():
                    data_out.append(port)

            spec2 = _spec_set_ports(spec, data_in=data_in, data_out=data_out, exec_in=exec_in, exec_out=exec_out)
        self._sync_list_orders_after_commit(spec2)
        if spec2 is not spec:
            self._node.spec = spec2

        if self._on_apply:
            self._on_apply()
        self.spec_applied.emit()
