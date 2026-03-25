from __future__ import annotations

import logging
from typing import Any, Callable

import msgspec
from f8pysdk import F8Command, F8CommandParam, F8OperatorSpec, F8ServiceSpec
from f8pysdk.command_state import command_input_state_field
from f8pysdk.schema_helpers import schema_default

from qtpy import QtCore, QtWidgets

from ...command_ui_protocol import CommandUiHandler, CommandUiSource
from ...components.controls import F8OptionCombo, F8Switch, F8ValueBar
from ...ui_notifications import show_warning
from ...ui_icons import StudioIcon
from ..schema_builder import SchemaBuilderDialog
from ..spec_mutations import (
    add_command as _spec_add_command,
    delete_command as _spec_delete_command,
    replace_command as _spec_replace_command,
)
from ..state_controls import (
    schema_enum_items as _schema_enum_items,
    schema_numeric_range as _schema_numeric_range,
    schema_type_any as _schema_type,
)
from ..ui_override_mutations import (
    base_command_show_on_node as _base_command_show_on_node,
    set_command_show_on_node_override as _set_command_show_on_node_override,
)
from .common import _TAB_PANEL_MARGIN, _node_missing_lock_info, _package_attr, _schema_from_json_obj, _wrap_tab_page
from .containers import _F8SpecListSection, _icon_from_style, _set_icon


logger = logging.getLogger(__name__)
_CommandSpec = F8ServiceSpec | F8OperatorSpec


class _F8EditCommandParamDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        param: F8CommandParam,
        ui_only: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        try:
            self._schema = param.valueSchema or _schema_from_json_obj({"type": "any"})
        except Exception:
            self._schema = _schema_from_json_obj({"type": "any"})
        self._ui_only = bool(ui_only)
        self._read_only = bool(read_only)

        self._name = QtWidgets.QLineEdit(str(param.name or ""))
        self._name.setClearButtonEnabled(True)

        self._required = QtWidgets.QCheckBox()
        self._required.setChecked(bool(param.required))

        self._desc = QtWidgets.QPlainTextEdit(str(param.description or ""))
        self._ui_control = QtWidgets.QLineEdit(str(param.uiControl or ""))
        self._ui_control.setClearButtonEnabled(True)

        self._schema_summary = QtWidgets.QLabel("")
        self._schema_summary.setStyleSheet("color: #888;")
        self._refresh_schema_summary()

        self._schema_btn = QtWidgets.QPushButton("Edit Schema...")
        self._schema_btn.clicked.connect(self._edit_schema)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Required", self._required)
        form.addRow("Description", self._desc)
        form.addRow("uiControl", self._ui_control)

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
            for w in (self._name, self._required, self._desc, self._ui_control, self._schema_btn):
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

    def param(self) -> F8CommandParam:
        name = str(self._name.text() or "").strip()
        required = bool(self._required.isChecked())
        desc = str(self._desc.toPlainText() or "").strip() or msgspec.UNSET
        ui_control = str(self._ui_control.text() or "").strip() or msgspec.UNSET
        return F8CommandParam(
            name=name, required=required, description=desc, uiControl=ui_control, valueSchema=self._schema
        )


class _F8EditCommandDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        cmd: F8Command,
        ui_only: bool = False,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._ui_only = bool(ui_only)
        self._read_only = bool(read_only)
        self._params: list[F8CommandParam] = list(cmd.params or [])

        self._name = QtWidgets.QLineEdit(str(cmd.name or ""))
        self._name.setClearButtonEnabled(True)
        self._desc = QtWidgets.QPlainTextEdit(str(cmd.description or ""))
        self._required = QtWidgets.QCheckBox()
        self._required.setChecked(bool(cmd.required))
        self._show_on_node = QtWidgets.QCheckBox()
        self._show_on_node.setChecked(bool(cmd.showOnNode))

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Required", self._required)
        form.addRow("Show On Node", self._show_on_node)
        form.addRow("Description", self._desc)

        self._params_list = QtWidgets.QListWidget()
        self._params_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._refresh_params_list()

        self._btn_add = QtWidgets.QPushButton("Add Param...")
        self._btn_edit = QtWidgets.QPushButton("Edit Param...")
        self._btn_del = QtWidgets.QPushButton("Delete Param")
        self._btn_add.clicked.connect(self._add_param)
        self._btn_edit.clicked.connect(self._edit_param)
        self._btn_del.clicked.connect(self._delete_param)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._btn_add)
        row.addWidget(self._btn_edit)
        row.addWidget(self._btn_del)
        row.addStretch(1)

        self._buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QtWidgets.QLabel("Params"))
        layout.addWidget(self._params_list, 1)
        layout.addLayout(row)
        layout.addWidget(self._buttons)

        if self._ui_only:
            for w in (self._name, self._required, self._desc, self._btn_add, self._btn_edit, self._btn_del):
                w.setEnabled(False)
        if self._read_only:
            for w in (
                self._name,
                self._required,
                self._show_on_node,
                self._desc,
                self._btn_add,
                self._btn_edit,
                self._btn_del,
                self._params_list,
            ):
                w.setEnabled(False)
            ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setEnabled(False)

    def _refresh_params_list(self) -> None:
        self._params_list.clear()
        for p in self._params:
            name = str(p.name or "")
            req = bool(p.required)
            item = QtWidgets.QListWidgetItem(f"{name}{' *' if req else ''}")
            item.setData(QtCore.Qt.UserRole, p)
            self._params_list.addItem(item)

    def _selected_index(self) -> int:
        row = int(self._params_list.currentRow())
        if row < 0 or row >= len(self._params):
            return -1
        return row

    def _add_param(self) -> None:
        if self._ui_only or self._read_only:
            return
        dlg = _F8EditCommandParamDialog(
            self,
            title="Add command param",
            param=F8CommandParam(name="", valueSchema=_schema_from_json_obj({"type": "any"})),
            read_only=self._read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        p = dlg.param()
        if not str(p.name or "").strip():
            return
        self._params.append(p)
        self._refresh_params_list()

    def _edit_param(self) -> None:
        if self._ui_only or self._read_only:
            return
        idx = self._selected_index()
        if idx < 0:
            return
        dlg = _F8EditCommandParamDialog(
            self,
            title="Edit command param",
            param=self._params[idx],
            read_only=self._read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        self._params[idx] = dlg.param()
        self._refresh_params_list()

    def _delete_param(self) -> None:
        if self._ui_only or self._read_only:
            return
        idx = self._selected_index()
        if idx < 0:
            return
        self._params.pop(idx)
        self._refresh_params_list()

    def command(self) -> F8Command:
        name = str(self._name.text() or "").strip()
        desc = str(self._desc.toPlainText() or "").strip() or msgspec.UNSET
        required = bool(self._required.isChecked())
        show = bool(self._show_on_node.isChecked())
        return F8Command(name=name, description=desc, required=required, showOnNode=show, params=list(self._params))


class _F8CommandRow(QtWidgets.QWidget):
    invoke_clicked = QtCore.Signal(str)
    edit_clicked = QtCore.Signal(str)
    delete_clicked = QtCore.Signal(str)
    show_on_node_changed = QtCore.Signal(bool)

    def __init__(
        self,
        parent=None,
        *,
        name: str,
        description: str,
        allow_edit: bool,
        allow_delete: bool,
        show_on_node: bool,
    ):
        super().__init__(parent)
        self._name = str(name or "")
        self._base_tooltip = str(description or "").strip()

        self._btn_invoke = QtWidgets.QPushButton(self._name)
        self._btn_invoke.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._btn_invoke.clicked.connect(self._on_invoke_clicked)

        self._eye_btn = QtWidgets.QToolButton()
        self._eye_btn.setAutoRaise(True)
        self._eye_btn.setCheckable(True)
        self._eye_btn.setToolTip("Show on node")
        self._eye_btn.toggled.connect(self._on_eye_toggled)  # type: ignore[attr-defined]

        self._btn_edit = QtWidgets.QToolButton()
        self._btn_edit.setAutoRaise(True)
        self._btn_edit.setToolTip("Edit command...")
        self._btn_edit.setIcon(
            _icon_from_style(self._btn_edit, QtWidgets.QStyle.SP_FileDialogDetailedView, "document-edit")
        )
        self._btn_edit.setEnabled(bool(allow_edit))
        self._btn_edit.setVisible(True)
        self._btn_edit.clicked.connect(self._on_edit_clicked)

        self._btn_del = QtWidgets.QToolButton()
        self._btn_del.setAutoRaise(True)
        self._btn_del.setToolTip("Delete command")
        self._btn_del.setIcon(_icon_from_style(self._btn_del, QtWidgets.QStyle.SP_TrashIcon, "edit-delete"))
        self._btn_del.setEnabled(bool(allow_delete))
        self._btn_del.setVisible(bool(allow_delete))
        self._btn_del.clicked.connect(self._on_delete_clicked)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._btn_invoke, 1)
        layout.addWidget(self._btn_edit, 0)
        layout.addWidget(self._eye_btn, 0)
        layout.addWidget(self._btn_del, 0)

        if self._base_tooltip:
            self._btn_invoke.setToolTip(self._base_tooltip)
            self._btn_edit.setToolTip("Edit command...\n" + self._base_tooltip)

        self.set_show_on_node(bool(show_on_node))

    def set_show_on_node(self, show: bool) -> None:
        with QtCore.QSignalBlocker(self._eye_btn):
            self._eye_btn.setChecked(bool(show))
        self._update_eye_icon(bool(show))

    def _update_eye_icon(self, show: bool) -> None:
        token = StudioIcon.EYE if bool(show) else StudioIcon.EYE_SLASH
        _set_icon(self._eye_btn, token=token)

    def _on_eye_toggled(self, checked: bool) -> None:
        self._update_eye_icon(bool(checked))
        self.show_on_node_changed.emit(bool(checked))

    def set_invoke_enabled(self, enabled: bool, *, disabled_reason: str = "Service not running") -> None:
        """
        Enable/disable the invoke button (eg. based on service process running state).
        """
        en = bool(enabled)
        try:
            self._btn_invoke.setEnabled(en)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to set invoke enabled state command=%s", self._name)
            return
        if en:
            if self._base_tooltip:
                try:
                    self._btn_invoke.setToolTip(self._base_tooltip)
                except (AttributeError, RuntimeError, TypeError):
                    logger.exception("Failed to restore invoke tooltip command=%s", self._name)
            return
        msg = str(disabled_reason or "").strip() or "Service not running"
        tip = (self._base_tooltip + "\n" + msg) if self._base_tooltip else msg
        try:
            self._btn_invoke.setToolTip(tip)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to set disabled tooltip command=%s", self._name)

    def _on_invoke_clicked(self, _checked: bool = False) -> None:
        self.invoke_clicked.emit(self._name)

    def _on_edit_clicked(self, _checked: bool = False) -> None:
        self.edit_clicked.emit(self._name)

    def _on_delete_clicked(self, _checked: bool = False) -> None:
        self.delete_clicked.emit(self._name)


class _F8SpecCommandEditor(QtWidgets.QWidget):
    def __init__(self, parent=None, *, node: Any, on_apply: Callable[[], None] | None):
        super().__init__(parent)
        self._node = node
        self._on_apply = on_apply
        self._missing_locked = False
        self._bridge_proc_hooked = False
        self._cmd_rows: dict[str, _F8CommandRow] = {}

        self._sec = _F8SpecListSection(title="Commands")
        self._sec.add_clicked.connect(self._add_command)

        content = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        v.setSpacing(4)
        v.addWidget(self._sec)
        v.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_wrap_tab_page(content))

        self._load()

    def _bridge(self) -> Any | None:
        try:
            g = self._node.graph
        except Exception:
            return None
        try:
            return g.service_bridge
        except Exception:
            return None

    def _service_id(self) -> str:
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if isinstance(spec, F8OperatorSpec):
            try:
                return str(self._node.svcId or "").strip()
            except Exception:
                return ""
        try:
            return str(self._node.id or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _editable_commands(spec: _CommandSpec) -> bool:
        try:
            return bool(spec.editableCommands)
        except Exception:
            return False

    def _ensure_bridge_process_hook(self) -> None:
        if self._bridge_proc_hooked:
            return
        bridge = self._bridge()
        if bridge is None:
            return
        try:
            bridge.service_process_state.connect(self._on_bridge_service_process_state)  # type: ignore[attr-defined]
            self._bridge_proc_hooked = True
        except Exception:
            self._bridge_proc_hooked = False

    def _is_service_running(self) -> bool:
        bridge = self._bridge()
        sid = self._service_id()
        if bridge is None or not sid:
            return False
        try:
            return bool(bridge.is_service_running(sid))
        except Exception:
            return False

    @QtCore.Slot(str, bool)
    def _on_bridge_service_process_state(self, service_id: str, running: bool) -> None:
        if str(service_id or "").strip() != self._service_id():
            return
        self._apply_running_state(bool(running))

    def _apply_running_state(self, running: bool) -> None:
        enabled = bool(running) and not self._missing_locked
        reason = "Missing dependency" if self._missing_locked else "Service not running"
        for row in list(self._cmd_rows.values()):
            row.set_invoke_enabled(enabled, disabled_reason=reason)

    def _load(self) -> None:
        self._ensure_bridge_process_hook()
        self._missing_locked, _ = _node_missing_lock_info(self._node)
        self._sec.clear()
        self._cmd_rows = {}
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            self._sec.set_add_visible(False)
            return
        editable = self._editable_commands(spec)
        self._sec.set_add_visible(bool(editable) and not self._missing_locked)

        running = self._is_service_running()
        try:
            cmds = list(self._node.effective_commands() or [])
        except Exception:
            cmds = list(spec.commands or [])
        for c in cmds:
            try:
                name = str(c.name or "")
            except Exception:
                name = ""
            if not name:
                continue
            try:
                desc = str(c.description or "")
            except Exception:
                desc = ""
            try:
                show_on_node = bool(c.showOnNode)
            except Exception:
                show_on_node = False
            try:
                required = bool(c.required)
            except Exception:
                required = False
            row = _F8CommandRow(
                name=name,
                description=desc,
                allow_edit=True,
                allow_delete=bool(editable) and not self._missing_locked and not required,
                show_on_node=bool(show_on_node),
            )
            row.invoke_clicked.connect(self._invoke_command)
            row.edit_clicked.connect(self._edit_command)
            row.delete_clicked.connect(self._delete_command)
            row.show_on_node_changed.connect(lambda v, _n=str(name): self._toggle_command_show_on_node(_n, bool(v)))  # type: ignore[attr-defined]
            try:
                row.set_invoke_enabled(
                    bool(running) and not self._missing_locked, disabled_reason="Missing dependency"
                )
            except (AttributeError, RuntimeError, TypeError):
                logger.exception("Failed to apply running-state to command row command=%s", name)
            self._cmd_rows[str(name)] = row
            self._sec.add_row(row)

    def _toggle_command_show_on_node(self, name: str, show_on_node: bool) -> None:
        if self._missing_locked:
            return
        n = str(name or "").strip()
        if not n:
            return
        self._apply_command_ui_override(n, bool(show_on_node))
        row = self._cmd_rows.get(n)
        if row is not None:
            row.set_show_on_node(bool(show_on_node))

    def _prompt_command_args(self, cmd: F8Command) -> dict[str, Any] | None:
        params = list(cmd.params or [])
        if not params:
            return {}
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(str(cmd.name or "Command"))
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        editors: dict[str, Callable[[], Any]] = {}
        widgets: dict[str, QtWidgets.QWidget] = {}

        for p in params:
            pname = str(p.name or "").strip()
            if not pname:
                continue
            required = bool(p.required)
            ui = str(p.uiControl or "").strip().lower()
            schema = p.valueSchema
            t = _schema_type(schema) if schema is not None else ""
            enum_items = _schema_enum_items(schema) if schema is not None else []
            lo, hi = _schema_numeric_range(schema) if schema is not None else (None, None)
            if t in {"string", "number", "integer", "boolean", "null", "object", "array", "any"}:
                default_value = schema_default(schema)
            else:
                default_value = None

            label = f"{pname} *" if required else pname
            tooltip = str(p.description or "").strip()

            if enum_items or ui in {"select", "dropdown", "dropbox", "combo", "combobox"}:
                combo = F8OptionCombo()
                items = [str(x) for x in enum_items]
                combo.set_options(items, labels=items)
                if tooltip:
                    combo.set_context_tooltip(tooltip)
                if default_value is not None:
                    combo.set_value(str(default_value))
                widgets[pname] = combo
                editors[pname] = lambda _c=combo: _c.value()
                form.addRow(label, combo)
                continue

            if t == "boolean" or ui in {"switch", "toggle"}:
                sw = F8Switch()
                sw.set_labels("True", "False")
                if tooltip:
                    sw.setToolTip(tooltip)
                if default_value is not None:
                    sw.set_value(bool(default_value))
                widgets[pname] = sw
                editors[pname] = lambda _s=sw: bool(_s.value())
                form.addRow(label, sw)
                continue

            if t in {"integer", "number"} and ui == "slider":
                is_int = t == "integer"
                bar = F8ValueBar(integer=is_int, minimum=0.0, maximum=1.0)
                bar.set_range(lo, hi)
                if default_value is not None:
                    bar.set_value(default_value)
                widgets[pname] = bar
                editors[pname] = lambda _b=bar, _is_int=is_int: (int(_b.value()) if _is_int else float(_b.value()))
                form.addRow(label, bar)
                continue

            w = QtWidgets.QLineEdit()
            if default_value is not None:
                w.setText(str(default_value))
            widgets[pname] = w
            editors[pname] = lambda _w=w: str(_w.text() or "").strip()
            form.addRow(label, w)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.addLayout(form)
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        while True:
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return None
            args: dict[str, Any] = {}
            missing: list[str] = []
            for p in params:
                pname = str(p.name or "").strip()
                if not pname or pname not in editors:
                    continue
                required = bool(p.required)
                v = editors[pname]()
                if isinstance(v, str) and v.strip() == "":
                    v = None
                if required and v is None:
                    missing.append(pname)
                    continue
                if v is not None:
                    args[pname] = v
            if missing:
                show_warning(dlg, "Missing required fields", "Please fill: " + ", ".join(missing))
                continue
            return args

    def _invoke_command(self, name: str) -> None:
        if self._missing_locked:
            return
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        cmd = None
        for c in list(spec.commands or []):
            try:
                cname = str(c.name or "").strip()
            except (AttributeError, TypeError):
                continue
            if cname == str(name or "").strip():
                cmd = c
                break
        if cmd is None:
            return

        # Mirror NodeGraph behavior: commands are only invokable when the service is running.
        if not self._is_service_running():
            return

        # Allow node-specific UI to override command invocation (eg. open a custom dialog).
        if isinstance(self._node, CommandUiHandler):
            parent = None
            try:
                parent = self.window()
            except Exception:
                parent = None
            try:
                if bool(self._node.handle_command_ui(cmd, parent=parent, source=CommandUiSource.PROPERTIES_BIN)):
                    return
            except Exception:
                node_id = ""
                try:
                    node_id = str(self._node.id or "").strip()
                except Exception:
                    node_id = ""
                logger.exception("handle_command_ui failed command=%s nodeId=%s", name, node_id)

        bridge = self._bridge()
        sid = self._service_id()
        if bridge is None or not sid:
            return
        args = {}
        params = list(cmd.params or [])
        if params:
            args = self._prompt_command_args(cmd)
            if args is None:
                return
        try:
            if isinstance(spec, F8OperatorSpec):
                bridge.set_remote_state(
                    sid,
                    str(self._node.id or "").strip(),
                    command_input_state_field(str(cmd.name or "")),
                    args or {},
                )
            else:
                bridge.invoke_remote_command(sid, str(cmd.name or ""), args or {})
        except Exception as e:
            show_warning(self, "Command failed", str(e))

    def _add_command(self) -> None:
        if self._missing_locked:
            return
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        editable = self._editable_commands(spec)
        if not editable:
            return
        cmd = F8Command(
            name="",
            description=msgspec.UNSET,
            required=False,
            showOnNode=False,
            params=[],
        )
        dialog_type = _package_attr("_F8EditCommandDialog", _F8EditCommandDialog)
        dlg = dialog_type(self, title="Add command", cmd=cmd, ui_only=False)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_cmd = dlg.command()
        if not str(new_cmd.name or "").strip():
            return
        spec2 = _spec_add_command(spec, cmd=new_cmd)
        if spec2 is not spec:
            self._node.spec = spec2
        if self._on_apply:
            self._on_apply()
        self._load()

    def _edit_command(self, name: str) -> None:
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        read_only = bool(self._missing_locked)
        editable = self._editable_commands(spec)
        cmds = list(spec.commands or [])
        idx = -1
        for i, c in enumerate(cmds):
            try:
                cname = str(c.name or "").strip()
            except (AttributeError, TypeError):
                continue
            if cname == str(name or "").strip():
                idx = i
                break
        if idx < 0:
            return
        # If not editable, only allow UI override edits (showOnNode).
        # Apply current UI override to dialog initial state (best-effort).
        init_cmd = cmds[idx]
        if not editable:
            try:
                for c in list(self._node.effective_commands() or []):
                    try:
                        if str(c.name or "").strip() == str(name or "").strip():
                            init_cmd = c
                            break
                    except (AttributeError, TypeError):
                        continue
            except (AttributeError, RuntimeError, TypeError):
                logger.exception("Failed to read effective commands for non-editable command dialog")
        dialog_type = _package_attr("_F8EditCommandDialog", _F8EditCommandDialog)
        dlg = dialog_type(
            self,
            title="Edit command",
            cmd=init_cmd,
            ui_only=not editable,
            read_only=read_only,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        edited = dlg.command()
        if editable and not read_only:
            spec2 = _spec_replace_command(spec, name=str(name or "").strip(), cmd=edited)
            if spec2 is not spec:
                self._node.spec = spec2
            if self._on_apply:
                self._on_apply()
        elif not read_only:
            self._apply_command_ui_override(str(init_cmd.name or ""), bool(edited.showOnNode))
        self._load()

    def _apply_command_ui_override(self, name: str, show_on_node: bool) -> None:
        n = str(name or "").strip()
        if not n:
            return
        node = self._node
        try:
            spec = node.spec
        except Exception:
            spec = None
        base_show = _base_command_show_on_node(spec, name=n)
        _set_command_show_on_node_override(
            node, name=n, show_on_node=bool(show_on_node), base_show_on_node=bool(base_show)
        )

    def _is_required_command(self, spec: _CommandSpec, *, name: str) -> bool:
        n = str(name or "").strip()
        if not n:
            return False
        for c in list(spec.commands or []):
            if str(c.name or "").strip() == n:
                return bool(c.required)
        return False

    def _delete_command(self, name: str) -> None:
        if self._missing_locked:
            return
        try:
            spec = self._node.spec
        except Exception:
            spec = None
        if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
            return
        editable = self._editable_commands(spec)
        if not editable:
            return
        n = str(name or "").strip()
        if self._is_required_command(spec, name=n):
            return
        if QtWidgets.QMessageBox.question(self, "Delete command", f"Delete '{n}'?") != QtWidgets.QMessageBox.Yes:
            return
        spec2 = _spec_delete_command(spec, name=n)
        if spec2 is not spec:
            self._node.spec = spec2
        if self._on_apply:
            self._on_apply()
        self._load()
