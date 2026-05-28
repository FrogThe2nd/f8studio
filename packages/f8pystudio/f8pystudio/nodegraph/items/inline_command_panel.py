from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.command import command_input_state_field
from f8pysdk.specs import schema_default, schema_type

from f8pystudio.ui.support.command_invocation import command_state_payload
from f8pystudio.contracts.command_ui import CommandUiHandler, CommandUiSource
from ...ui.components.controls import F8OptionCombo, F8Switch, F8ValueBar, parse_select_pool
from ...ui.support.studio_theme import inline_command_button_qss, transparent_header_qss
from ...ui.support.ui_notifications import show_warning
from .proxy_widget_utils import dispose_detached_proxy_widget
from .state_inline_controls import build_inline_header_button

logger = logging.getLogger(__name__)

# Command rows are now first-class inline panels.
# They share the same layout model as state inline rows, and `showOnNode`
# controls both row visibility and command-port visibility.
COMMAND_INLINE_BUTTON_STYLE = inline_command_button_qss()

_COMMAND_SPEC_ERRORS = (AttributeError, TypeError, ValueError)
_QT_ACCESS_ERRORS = (AttributeError, RuntimeError, TypeError)
_QT_VALUE_ACCESS_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_COMMAND_UI_HANDLER_ERRORS = (Exception,)
_COMMAND_REMOTE_STATE_ERRORS = (Exception,)


def _node_item_id(node_item: Any) -> str:
    try:
        return str(node_item.id or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _command_name(command: Any) -> str:
    try:
        return str(command.name or "").strip()
    except _COMMAND_SPEC_ERRORS:
        return ""


def _command_description(command: Any) -> str:
    try:
        return str(command.description or "").strip()
    except _COMMAND_SPEC_ERRORS:
        return ""


def _visible_commands(node_item: Any) -> list[Any]:
    try:
        node = node_item._backend_node()
    except _QT_VALUE_ACCESS_ERRORS:
        logger.exception("read backend node failed nodeId=%s", _node_item_id(node_item))
        return []
    if node is None:
        return []
    try:
        commands = list(node.effective_commands() or [])
    except _QT_VALUE_ACCESS_ERRORS:
        logger.exception("read effective_commands failed nodeId=%s", _node_item_id(node_item))
        return []

    visible_commands: list[Any] = []
    for command in commands:
        command_name = _command_name(command)
        if not command_name:
            continue
        try:
            show_on_node = bool(command.showOnNode)
        except _COMMAND_SPEC_ERRORS:
            logger.exception(
                "read command showOnNode failed nodeId=%s command=%s",
                _node_item_id(node_item),
                command_name,
            )
            continue
        if show_on_node:
            visible_commands.append(command)
    return visible_commands


def _command_row_serial(*, command_name: str) -> str:
    return command_name


def _command_enabled_state(node_item: Any) -> tuple[bool, str]:
    missing_locked = _is_missing_locked(node_item)
    enabled = bool(node_item._is_service_running()) and not missing_locked
    if enabled:
        return True, ""
    if missing_locked:
        return False, "Missing dependency"
    return False, "Service not running"


def _command_tooltip(*, description: str, enabled: bool, disabled_reason: str) -> str:
    if enabled:
        return description
    if description:
        return f"{description}\n{disabled_reason}"
    return disabled_reason


def _apply_command_button_state(
    button: QtWidgets.QAbstractButton,
    *,
    command_name: str,
    description: str,
    enabled: bool,
    disabled_reason: str,
) -> None:
    try:
        button.set_full_text(command_name)  # type: ignore[attr-defined]
    except AttributeError:
        button.setText(command_name)
    button.setEnabled(bool(enabled))
    button.setToolTip(_command_tooltip(description=description, enabled=enabled, disabled_reason=disabled_reason))
    button.setStyleSheet(COMMAND_INLINE_BUTTON_STYLE)
    button.setCursor(QtCore.Qt.PointingHandCursor if enabled else QtCore.Qt.ArrowCursor)


def _remove_command_row(node_item: Any, command_name: str) -> None:
    proxy = node_item._command_inline_proxies.pop(command_name, None)
    node_item._command_inline_headers.pop(command_name, None)
    node_item._command_inline_buttons.pop(command_name, None)
    node_item._command_inline_descriptions.pop(command_name, None)
    node_item._command_inline_serials.pop(command_name, None)
    if proxy is None:
        return
    old = None
    try:
        old = proxy.widget()
    except (AttributeError, RuntimeError, TypeError):
        old = None
    try:
        proxy.setWidget(None)
    except RuntimeError:
        pass
    if old is not None:
        dispose_detached_proxy_widget(old, context=f"inline-command-remove:{command_name}")
    try:
        proxy.setParentItem(None)
        if node_item.scene() is not None:
            node_item.scene().removeItem(proxy)
    except RuntimeError:
        pass


def _remove_stale_command_rows(node_item: Any, desired_names: list[str]) -> None:
    desired_name_set = set(desired_names)
    for command_name in list(node_item._command_inline_proxies.keys()):
        if command_name in desired_name_set:
            continue
        _remove_command_row(node_item, command_name)


def _build_command_row_widget(
    node_item: Any,
    *,
    command: Any,
    command_name: str,
    description: str,
    enabled: bool,
    disabled_reason: str,
) -> tuple[QtWidgets.QGraphicsProxyWidget, QtWidgets.QWidget, QtWidgets.QAbstractButton]:
    panel = QtWidgets.QWidget()
    header, button = build_inline_header_button(
        label=command_name,
        tooltip=_command_tooltip(description=description, enabled=enabled, disabled_reason=disabled_reason),
        expandable=False,
        parent=panel,
    )
    _apply_command_button_state(
        button,
        command_name=command_name,
        description=description,
        enabled=enabled,
        disabled_reason=disabled_reason,
    )
    button.setAutoRepeat(False)
    button.clicked.connect(lambda _checked=False, _c=command: _on_command_clicked(node_item, _c))

    panel_lay = QtWidgets.QVBoxLayout(panel)
    panel_lay.setContentsMargins(0, 0, 0, 0)
    panel_lay.setSpacing(0)
    panel_lay.addWidget(header)
    panel.setProperty("_f8_command_panel", True)
    panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    panel.setStyleSheet(transparent_header_qss())

    proxy = node_item._command_inline_proxies.get(command_name)
    if proxy is None:
        proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    old = None
    try:
        old = proxy.widget()
    except (AttributeError, RuntimeError, TypeError):
        old = None
    proxy.setWidget(panel)
    if old is not None and old is not panel:
        dispose_detached_proxy_widget(old, context=f"inline-command-replace:{command_name}")
    return proxy, header, button


def _sync_command_row(
    node_item: Any,
    *,
    command: Any,
    command_name: str,
    description: str,
    enabled: bool,
    disabled_reason: str,
) -> tuple[QtWidgets.QGraphicsProxyWidget, QtWidgets.QWidget, QtWidgets.QAbstractButton] | None:
    serial = _command_row_serial(command_name=command_name)
    existing_button = node_item._command_inline_buttons.get(command_name)
    existing_proxy = node_item._command_inline_proxies.get(command_name)
    existing_header = node_item._command_inline_headers.get(command_name)
    existing_serial = str(node_item._command_inline_serials.get(command_name, "") or "")
    if existing_button is not None and existing_proxy is not None and existing_header is not None and serial == existing_serial:
        _apply_command_button_state(
            existing_button,
            command_name=command_name,
            description=description,
            enabled=enabled,
            disabled_reason=disabled_reason,
        )
        node_item._command_inline_descriptions[command_name] = description
        return existing_proxy, existing_header, existing_button

    try:
        proxy, header, button = _build_command_row_widget(
            node_item,
            command=command,
            command_name=command_name,
            description=description,
            enabled=enabled,
            disabled_reason=disabled_reason,
        )
    except _QT_VALUE_ACCESS_ERRORS:
        logger.exception("build command row failed nodeId=%s command=%s", _node_item_id(node_item), command_name)
        return None

    node_item._command_inline_serials[command_name] = serial
    node_item._command_inline_descriptions[command_name] = description
    return proxy, header, button


def refresh_inline_command_rows(node_item: Any) -> None:
    enabled, disabled_reason = _command_enabled_state(node_item)
    for command_name, button in list(node_item._command_inline_buttons.items()):
        description = str(node_item._command_inline_descriptions.get(command_name, "") or "")
        try:
            _apply_command_button_state(
                button,
                command_name=command_name,
                description=description,
                enabled=enabled,
                disabled_reason=disabled_reason,
            )
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("refresh command row state failed nodeId=%s command=%s", _node_item_id(node_item), command_name)


def _is_missing_locked(node_item: Any) -> bool:
    try:
        node = node_item._backend_node()
    except _QT_VALUE_ACCESS_ERRORS:
        return False
    if node is None:
        return False
    try:
        return bool(node.is_missing_locked())
    except _QT_VALUE_ACCESS_ERRORS:
        return False


def _snapshot_selected_node_ids(node_item: Any) -> list[str]:
    try:
        graph = node_item._graph()
    except _QT_ACCESS_ERRORS:
        return []
    if graph is None:
        return []
    try:
        selected_nodes = list(graph.selected_nodes() or [])
    except _QT_VALUE_ACCESS_ERRORS:
        return []
    out: list[str] = []
    for node in selected_nodes:
        try:
            node_id = str(node.id or "").strip()
        except _QT_VALUE_ACCESS_ERRORS:
            node_id = ""
        if node_id:
            out.append(node_id)
    return out


def _restore_selected_node_ids(node_item: Any, ids: list[str]) -> None:
    try:
        graph = node_item._graph()
    except _QT_ACCESS_ERRORS:
        return
    if graph is None:
        return
    target_ids = {str(node_id).strip() for node_id in ids if str(node_id).strip()}
    try:
        nodes = list(graph.all_nodes() or [])
    except _QT_VALUE_ACCESS_ERRORS:
        nodes = []
    for node in nodes:
        try:
            node_id = str(node.id or "").strip()
        except _QT_VALUE_ACCESS_ERRORS:
            node_id = ""
        if not node_id:
            continue
        try:
            node.set_property("selected", node_id in target_ids, push_undo=False)
        except _QT_VALUE_ACCESS_ERRORS:
            continue


def _on_command_clicked(node_item: Any, command: Any) -> None:
    selected_ids = _snapshot_selected_node_ids(node_item)
    node_item._invoke_command(command)
    QtCore.QTimer.singleShot(0, lambda: _restore_selected_node_ids(node_item, selected_ids))


def invoke_command(node_item: Any, cmd: Any) -> None:
    """
    Invoke a command declared on the node spec via hidden command input state.

    - no params: fire immediately
    - has params: show dialog to collect args
    """
    try:
        call = str(cmd.name or "").strip()
    except _COMMAND_SPEC_ERRORS:
        call = ""
    if not call:
        return
    try:
        node = node_item._backend_node()
    except _QT_VALUE_ACCESS_ERRORS:
        node = None
    bridge = node_item._bridge()
    if bridge is None:
        return
    sid = node_item._service_id()
    if not sid:
        return
    if _is_missing_locked(node_item):
        logger.warning("Skip command invoke on missing-locked node serviceId=%s", sid)
        return
    if not node_item._is_service_running():
        return

    # Allow a node to intercept command invocation with custom UI logic.
    if isinstance(node, CommandUiHandler):
        parent = None
        try:
            viewer = node_item.viewer()
            parent = viewer.window() if viewer is not None else None
        except _QT_ACCESS_ERRORS:
            parent = None
        try:
            if bool(node.handle_command_ui(cmd, parent=parent, source=CommandUiSource.NODEGRAPH)):
                return
        except _COMMAND_UI_HANDLER_ERRORS:
            logger.exception("handle_command_ui failed nodeId=%s", _node_item_id(node_item))
    try:
        params = list(cmd.params or [])
    except _COMMAND_SPEC_ERRORS:
        params = []

    # Route UI-triggered commands through hidden command input state so command
    # output ports and downstream graph fanout keep the same semantics for both
    # services and operators.
    node_id = _node_item_id(node_item) or str(sid)
    if not params:
        try:
            bridge.set_remote_state(  # type: ignore[attr-defined]
                sid,
                node_id,
                command_input_state_field(call),
                command_state_payload({}),
            )
        except _COMMAND_REMOTE_STATE_ERRORS:
            logger.exception(
                "set_remote_state failed serviceId=%s nodeId=%s call=%s",
                sid,
                node_id,
                call,
            )
        return

    args = prompt_command_args(node_item, cmd)
    if args is None:
        return
    try:
        bridge.set_remote_state(  # type: ignore[attr-defined]
            sid,
            node_id,
            command_input_state_field(call),
            command_state_payload(args),
        )
    except _COMMAND_REMOTE_STATE_ERRORS:
        logger.exception("set_remote_state failed serviceId=%s nodeId=%s call=%s", sid, node_id, call)


def prompt_command_args(node_item: Any, cmd: Any) -> dict[str, Any] | None:
    try:
        call = str(cmd.name or "").strip() or "Command"
    except _COMMAND_SPEC_ERRORS:
        call = "Command"
    try:
        params = list(cmd.params or [])
    except _COMMAND_SPEC_ERRORS:
        params = []
    if not params:
        return {}

    try:
        viewer = node_item.viewer()
        parent = viewer.window() if viewer is not None else None
    except _QT_ACCESS_ERRORS:
        parent = None

    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(call)
    dlg.setModal(True)

    form = QtWidgets.QFormLayout()
    form.setContentsMargins(12, 12, 12, 12)
    form.setSpacing(8)

    editors: dict[str, tuple[QtWidgets.QWidget, Callable[[], Any]]] = {}

    for param in params:
        try:
            name = str(param.name or "").strip()
        except _COMMAND_SPEC_ERRORS:
            name = ""
        try:
            required = bool(param.required)
        except _COMMAND_SPEC_ERRORS:
            required = False
        try:
            ui_raw = str(param.uiControl or "").strip()
            ui = ui_raw.lower()
        except _COMMAND_SPEC_ERRORS:
            ui_raw = ""
            ui = ""
        try:
            schema = param.valueSchema
        except _COMMAND_SPEC_ERRORS:
            schema = None
        try:
            desc_raw = param.description or ""
        except _COMMAND_SPEC_ERRORS:
            desc_raw = ""
        if not name:
            continue

        schema_type_value = schema_type(schema) if schema is not None else ""
        schema_type_value = schema_type_value or ""

        enum_items = node_item._schema_enum_items(schema)
        lo, hi = node_item._schema_numeric_range(schema)
        try:
            default_value = schema_default(schema)
        except _COMMAND_SPEC_ERRORS:
            default_value = None

        label = f"{name} *" if required else name
        tooltip = str(desc_raw or "").strip()

        def _with_tooltip(widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
            if tooltip:
                widget.setToolTip(tooltip)
            return widget

        pool_field = parse_select_pool(ui_raw)
        if enum_items or pool_field or ui in {"select", "dropdown", "dropbox", "combo", "combobox"}:
            combo = F8OptionCombo()
            if pool_field:
                node = node_item._backend_node()
                items = []
                if node is not None:
                    try:
                        value = node.get_property(pool_field)
                        if isinstance(value, (list, tuple)):
                            items = [str(item) for item in value]
                    except _QT_VALUE_ACCESS_ERRORS:
                        items = []
            else:
                items = list(enum_items)
            combo.set_options(items, labels=items)
            if tooltip:
                combo.set_context_tooltip(tooltip)
            if default_value is not None:
                combo.set_value(str(default_value))

            def _get() -> Any:
                value = combo.value()
                return None if value is None else str(value)

            editors[name] = (_with_tooltip(combo), _get)
            form.addRow(label, combo)
            continue

        if schema_type_value == "boolean" or ui in {"switch", "toggle"}:
            switch = F8Switch()
            switch.set_labels("True", "False")
            if default_value is not None:
                switch.set_value(bool(default_value))

            def _get() -> Any:
                return bool(switch.value())

            editors[name] = (_with_tooltip(switch), _get)
            form.addRow(label, switch)
            continue

        if schema_type_value in {"integer", "number"} and ui == "slider":
            is_int = schema_type_value == "integer"
            bar = F8ValueBar(integer=is_int, minimum=0.0, maximum=1.0)
            bar.set_range(lo, hi)
            if default_value is not None:
                bar.set_value(default_value)

            def _get() -> Any:
                value = bar.value()
                return int(value) if is_int else float(value)

            editors[name] = (_with_tooltip(bar), _get)
            form.addRow(label, bar)
            continue

        if schema_type_value == "integer" or ui in {"spinbox", "int"}:
            spin = QtWidgets.QSpinBox()
            if lo is not None:
                spin.setMinimum(int(lo))
            if hi is not None:
                spin.setMaximum(int(hi))
            if default_value is not None:
                try:
                    spin.setValue(int(default_value))
                except (TypeError, ValueError):
                    pass

            def _get() -> Any:
                return int(spin.value())

            editors[name] = (_with_tooltip(spin), _get)
            form.addRow(label, spin)
            continue

        if schema_type_value == "number" or ui in {"doublespinbox", "float"}:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(6)
            if lo is not None:
                spin.setMinimum(float(lo))
            if hi is not None:
                spin.setMaximum(float(hi))
            if default_value is not None:
                try:
                    spin.setValue(float(default_value))
                except (TypeError, ValueError):
                    pass

            def _get() -> Any:
                return float(spin.value())

            editors[name] = (_with_tooltip(spin), _get)
            form.addRow(label, spin)
            continue

        if schema_type_value in {"object", "array", "any"}:
            text_edit = QtWidgets.QPlainTextEdit()
            text_edit.setMinimumHeight(90)
            if default_value is not None:
                try:
                    text_edit.setPlainText(json.dumps(default_value, ensure_ascii=False, indent=2))
                except (TypeError, ValueError):
                    text_edit.setPlainText(str(default_value))

            def _get() -> Any:
                txt = str(text_edit.toPlainText() or "").strip()
                if not txt:
                    return None
                try:
                    return json.loads(txt)
                except json.JSONDecodeError:
                    return txt

            editors[name] = (_with_tooltip(text_edit), _get)
            form.addRow(label, text_edit)
            continue

        line_edit = QtWidgets.QLineEdit()
        if default_value is not None:
            line_edit.setText("" if default_value is None else str(default_value))

        def _get() -> Any:
            return str(line_edit.text() or "")

        editors[name] = (_with_tooltip(line_edit), _get)
        form.addRow(label, line_edit)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout = QtWidgets.QVBoxLayout(dlg)
    layout.addLayout(form)
    layout.addWidget(buttons)

    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    while True:
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        args: dict[str, Any] = {}
        missing: list[str] = []
        for param in params:
            try:
                param_name = str(param.name or "").strip()
            except _COMMAND_SPEC_ERRORS:
                param_name = ""
            try:
                required = bool(param.required)
            except _COMMAND_SPEC_ERRORS:
                required = False
            if not param_name or param_name not in editors:
                continue
            _widget, getter = editors[param_name]
            try:
                value = getter()
            except (RuntimeError, TypeError, ValueError):
                value = None
            if isinstance(value, str) and value.strip() == "":
                value = None
            if required and value is None:
                missing.append(param_name)
                continue
            if value is not None:
                args[param_name] = value
        if missing:
            show_warning(dlg, "Missing required fields", "Please fill: " + ", ".join(missing))
            continue
        return args


def ensure_inline_command_rows(node_item: Any) -> None:
    node_item._ensure_bridge_process_hook()
    visible_commands = _visible_commands(node_item)
    desired_names: list[str] = []
    for command in visible_commands:
        command_name = _command_name(command)
        if command_name:
            desired_names.append(command_name)

    _remove_stale_command_rows(node_item, desired_names)
    enabled, disabled_reason = _command_enabled_state(node_item)
    rebuilt_proxies: dict[str, QtWidgets.QGraphicsProxyWidget] = {}
    rebuilt_headers: dict[str, QtWidgets.QWidget] = {}
    rebuilt_buttons: dict[str, QtWidgets.QAbstractButton] = {}
    rebuilt_descriptions: dict[str, str] = {}

    for command in visible_commands:
        command_name = _command_name(command)
        if not command_name:
            continue
        description = _command_description(command)
        synced = _sync_command_row(
            node_item,
            command=command,
            command_name=command_name,
            description=description,
            enabled=enabled,
            disabled_reason=disabled_reason,
        )
        if synced is None:
            continue
        proxy, header, button = synced
        rebuilt_proxies[command_name] = proxy
        rebuilt_headers[command_name] = header
        rebuilt_buttons[command_name] = button
        rebuilt_descriptions[command_name] = description

    node_item._command_inline_proxies.clear()
    node_item._command_inline_headers.clear()
    node_item._command_inline_buttons.clear()
    node_item._command_inline_descriptions.clear()
    for name in desired_names:
        proxy = rebuilt_proxies.get(name)
        header = rebuilt_headers.get(name)
        button = rebuilt_buttons.get(name)
        if proxy is None or header is None or button is None:
            continue
        node_item._command_inline_proxies[name] = proxy
        node_item._command_inline_headers[name] = header
        node_item._command_inline_buttons[name] = button
        node_item._command_inline_descriptions[name] = rebuilt_descriptions.get(name, "")

    try:
        node_item._invalidate_layout_metrics()
        node_item._prepare_layout_metrics()
        node_item.sync_proxy_mode(force=True)
    except (AttributeError, RuntimeError, TypeError):
        pass
