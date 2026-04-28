from __future__ import annotations

import json

from f8pysdk.codec import copy_model, dump_json
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.specs import schema_type
from f8pysdk.command import parse_command_port_name
from f8pysdk.specs import can_edit_existing as _policy_can_edit_existing

from ...ui.dialogs.schema_builder_dialog import SchemaBuilderDialog, schema_from_json_obj as _schema_from_json_obj
from ...nodegraph.state_schema import (
    schema_enum_items as _shared_schema_enum_items,
    schema_numeric_range as _shared_schema_numeric_range,
)


def port_group(name: str) -> str:
    port_name = str(name or "")
    if port_name.startswith("[E]") or port_name.endswith("[E]"):
        return "exec"
    if port_name.startswith("[D]") or port_name.endswith("[D]"):
        return "data"
    if port_name.startswith("[S]") or port_name.endswith("[S]"):
        return "state"
    if port_name.startswith("[C]") or port_name.endswith("[C]"):
        return "command"
    return "other"


def display_port_label(name: str, *, max_chars: int | None = None) -> str:
    """
    Display-friendly label for port text items.

    Strip `[E]/[D]/[S]/[C]` markers and optionally elide to keep compact.
    """
    label = str(name or "")
    if label.startswith("[E]"):
        label = label[3:]
    elif label.endswith("[E]"):
        label = label[:-3]
    elif label.startswith("[D]"):
        label = label[3:]
    elif label.endswith("[D]"):
        label = label[:-3]
    elif label.startswith("[S]"):
        label = label[3:]
    elif label.endswith("[S]"):
        label = label[:-3]
    elif label.startswith("[C]"):
        label = label[3:]
    elif label.endswith("[C]"):
        label = label[:-3]
    label = label.strip()
    if max_chars is not None and max_chars > 0 and len(label) > max_chars:
        return label[: max(1, max_chars - 1)] + "..."
    return label


def schema_enum_items(value_schema: Any) -> list[str]:
    return _shared_schema_enum_items(value_schema)


def schema_numeric_range(value_schema: Any) -> tuple[float | None, float | None]:
    return _shared_schema_numeric_range(value_schema)


def parse_schema_port_view_name(view_name: str) -> tuple[str, bool, str] | None:
    raw = str(view_name or "").strip()
    if raw.startswith("[D]"):
        port_name = str(raw[3:] or "").strip()
        if not port_name:
            return None
        return "data", True, port_name
    if raw.endswith("[D]"):
        port_name = str(raw[:-3] or "").strip()
        if not port_name:
            return None
        return "data", False, port_name
    if raw.startswith("[S]"):
        port_name = str(raw[3:] or "").strip()
        if not port_name:
            return None
        return "state", True, port_name
    if raw.endswith("[S]"):
        port_name = str(raw[:-3] or "").strip()
        if not port_name:
            return None
        return "state", False, port_name
    command = parse_command_port_name(raw)
    if command is not None:
        is_in, command_name = command
        return "command", is_in, command_name
    return None


def schema_brief(value_schema: Any) -> str:
    if value_schema is None:
        return "unknown"
    schema_obj = value_schema
    try:
        top = str(schema_type(value_schema) or "").strip().lower()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        top = ""
    if not top:
        try:
            top_raw = schema_obj.type
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            top_raw = None
        if top_raw is not None:
            try:
                top = str(top_raw.value).strip().lower()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                top = str(top_raw).strip().lower()
    if not top:
        return "unknown"
    if top == "array":
        item_type = ""
        try:
            items = schema_obj.items
            if items is not None:
                item_type = str(schema_type(items) or "").strip().lower()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            item_type = ""
        if not item_type:
            try:
                items = schema_obj.items
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                items = None
            if items is not None:
                try:
                    raw_item_type = items.type
                except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                    raw_item_type = None
                if raw_item_type is not None:
                    try:
                        item_type = str(raw_item_type.value).strip().lower()
                    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                        item_type = str(raw_item_type).strip().lower()
        if item_type:
            return f"array<{item_type}>"
        return "array"
    if top == "object":
        prop_count = 0
        try:
            properties = schema_obj.properties
            if isinstance(properties, dict):
                prop_count = len(properties)
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            prop_count = 0
        if prop_count > 0:
            return f"object[{prop_count}]"
        return "object"
    return top


def find_data_port_spec(node_item: Any, *, is_in: bool, port_name: str) -> tuple[Any, int] | None:
    node = node_item._backend_node()
    if node is None:
        return None
    spec = node.spec
    ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
    target_name = str(port_name or "").strip()
    for index, port in enumerate(ports):
        if str(port.name or "").strip() == target_name:
            return port, int(index)
    return None


def data_port_tooltip(node_item: Any, *, is_in: bool, port_name: str) -> str:
    direction_text = "input" if bool(is_in) else "output"
    found = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
    if found is None:
        return f"{port_name} ({direction_text})\nschema: unknown"
    port, _index = found
    schema_text = schema_brief(port.valueSchema)
    desc = str(port.description or "").strip()
    lines = [f"{port_name} ({direction_text})", f"schema: {schema_text}"]
    if desc:
        lines.append(desc)
    return "\n".join(lines)


def find_state_field_spec(node_item: Any, *, field_name: str) -> tuple[Any, int] | None:
    node = node_item._backend_node()
    if node is None:
        return None
    spec = node.spec
    fields = list(spec.stateFields or [])
    target_name = str(field_name or "").strip()
    for index, field in enumerate(fields):
        if str(field.name or "").strip() == target_name:
            return field, int(index)
    return None


def state_port_tooltip(node_item: Any, *, is_in: bool, field_name: str) -> str:
    direction_text = "state input" if bool(is_in) else "state output"
    found = find_state_field_spec(node_item, field_name=field_name)
    if found is None:
        return f"{field_name} ({direction_text})\nschema: unknown"
    field, _index = found
    schema_text = schema_brief(field.valueSchema)
    desc = str(field.description or "").strip()
    lines = [f"{field_name} ({direction_text})", f"schema: {schema_text}"]
    if desc:
        lines.append(desc)
    return "\n".join(lines)


def port_tooltip_text(node_item: Any, view_name: str) -> str:
    parsed = parse_schema_port_view_name(view_name)
    if parsed is None:
        return str(view_name or "")
    kind, is_in, port_name = parsed
    if kind == "data":
        return data_port_tooltip(node_item, is_in=bool(is_in), port_name=port_name)
    if kind == "state":
        return state_port_tooltip(node_item, is_in=bool(is_in), field_name=port_name)
    if kind == "command":
        return command_port_tooltip(node_item, is_in=bool(is_in), command_name=port_name)
    return str(view_name or "")


def find_command_spec(node_item: Any, *, command_name: str) -> tuple[Any, int] | None:
    node = node_item._backend_node()
    if node is None:
        return None
    target_name = str(command_name or "").strip()
    try:
        commands = list(node.effective_commands() or [])
    except Exception:
        spec = getattr(node, "spec", None)
        commands = list(spec.commands or []) if spec is not None else []
    for index, command in enumerate(commands):
        if str(command.name or "").strip() == target_name:
            return command, int(index)
    return None


def command_port_tooltip(node_item: Any, *, is_in: bool, command_name: str) -> str:
    direction_text = "command input" if bool(is_in) else "command output"
    found = find_command_spec(node_item, command_name=command_name)
    if found is None:
        return f"{command_name} ({direction_text})"
    command, _index = found
    desc = str(command.description or "").strip()
    params = [str(param.name or "").strip() for param in list(command.params or []) if str(param.name or "").strip()]
    lines = [f"{command_name} ({direction_text})"]
    if params:
        lines.append("params: " + ", ".join(params))
    else:
        lines.append("params: none")
    if desc:
        lines.append(desc)
    return "\n".join(lines)


def refresh_port_tooltips(node_item: Any) -> None:
    for port, text_item in node_item._input_items.items():
        full_name = str(port.name or "")
        tooltip = port_tooltip_text(node_item, full_name)
        try:
            port.setToolTip(tooltip)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            text_item.setToolTip(tooltip)
        except (AttributeError, RuntimeError, TypeError):
            pass
    for port, text_item in node_item._output_items.items():
        full_name = str(port.name or "")
        tooltip = port_tooltip_text(node_item, full_name)
        try:
            port.setToolTip(tooltip)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            text_item.setToolTip(tooltip)
        except (AttributeError, RuntimeError, TypeError):
            pass


def open_data_port_schema_dialog(node_item: Any, *, is_in: bool, port_name: str) -> None:
    node = node_item._backend_node()
    if node is None:
        return
    found = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
    if found is None:
        return
    port, index = found
    spec = node.spec
    editable = _policy_can_edit_existing(spec, "dataInPorts" if bool(is_in) else "dataOutPorts")
    missing_locked = bool(node.is_missing_locked())
    read_only = bool((not editable) or missing_locked)
    schema_value = port.valueSchema
    if schema_value is None:
        schema_value = _schema_from_json_obj({"type": "any"})
    title = f"Edit valueSchema ({port_name})"
    dlg = SchemaBuilderDialog(node_item._viewer_safe(), title=title, schema=schema_value, read_only=read_only)
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return
    if read_only:
        return
    new_schema = dlg.schema()
    replace_data_port_schema(node_item, is_in=bool(is_in), port_name=port_name, new_schema=new_schema)


def replace_data_port_schema(node_item: Any, *, is_in: bool, port_name: str, new_schema: Any) -> bool:
    node = node_item._backend_node()
    if node is None:
        return False
    found = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
    if found is None:
        return False
    _port, index = found
    spec = node.spec
    ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
    if int(index) < 0 or int(index) >= len(ports):
        return False
    updated = copy_model(ports[int(index)], update={"valueSchema": new_schema})
    ports[int(index)] = updated
    if bool(is_in):
        updated_spec = copy_model(spec, update={"dataInPorts": ports})
    else:
        updated_spec = copy_model(spec, update={"dataOutPorts": ports})
    node.set_spec(updated_spec, rebuild=True)
    return True


def open_state_field_schema_dialog(node_item: Any, *, field_name: str) -> None:
    node = node_item._backend_node()
    if node is None:
        return
    found = find_state_field_spec(node_item, field_name=field_name)
    if found is None:
        return
    field, index = found
    spec = node.spec
    editable = _policy_can_edit_existing(spec, "stateFields")
    missing_locked = bool(node.is_missing_locked())
    read_only = bool((not editable) or missing_locked)
    schema_value = field.valueSchema
    if schema_value is None:
        schema_value = _schema_from_json_obj({"type": "any"})
    title = f"Edit valueSchema ({field_name})"
    dlg = SchemaBuilderDialog(node_item._viewer_safe(), title=title, schema=schema_value, read_only=read_only)
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return
    if read_only:
        return
    new_schema = dlg.schema()
    replace_state_field_schema(node_item, field_name=field_name, new_schema=new_schema)


def replace_state_field_schema(node_item: Any, *, field_name: str, new_schema: Any) -> bool:
    node = node_item._backend_node()
    if node is None:
        return False
    found = find_state_field_spec(node_item, field_name=field_name)
    if found is None:
        return False
    _field, index = found
    spec = node.spec
    fields = list(spec.stateFields or [])
    if int(index) < 0 or int(index) >= len(fields):
        return False
    updated = copy_model(fields[int(index)], update={"valueSchema": new_schema})
    fields[int(index)] = updated
    updated_spec = copy_model(spec, update={"stateFields": fields})
    node.set_spec(updated_spec, rebuild=True)
    return True


def schema_to_clipboard_text(value_schema: Any) -> str:
    schema_obj = value_schema
    if schema_obj is None:
        schema_obj = _schema_from_json_obj({"type": "any"})
    payload = dump_json(schema_obj, mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def schema_from_clipboard_text(text: str) -> Any | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return _schema_from_json_obj(payload)
    except (TypeError, ValueError, KeyError, RuntimeError):
        return None


def _copy_value_schema_text_to_clipboard(schema_text: str) -> bool:
    clipboard = QtWidgets.QApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(schema_text)
    return True


def _read_value_schema_text_from_clipboard() -> str:
    clipboard = QtWidgets.QApplication.clipboard()
    if clipboard is None:
        return ""
    return str(clipboard.text() or "")


def copy_data_port_schema_to_clipboard(node_item: Any, *, is_in: bool, port_name: str) -> bool:
    found = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
    if found is None:
        return False
    port, _index = found
    return _copy_value_schema_text_to_clipboard(schema_to_clipboard_text(port.valueSchema))


def copy_state_field_schema_to_clipboard(node_item: Any, *, field_name: str) -> bool:
    found = find_state_field_spec(node_item, field_name=field_name)
    if found is None:
        return False
    field, _index = found
    return _copy_value_schema_text_to_clipboard(schema_to_clipboard_text(field.valueSchema))


def paste_data_port_schema_from_clipboard(node_item: Any, *, is_in: bool, port_name: str) -> bool:
    new_schema = schema_from_clipboard_text(_read_value_schema_text_from_clipboard())
    if new_schema is None:
        return False
    return replace_data_port_schema(node_item, is_in=bool(is_in), port_name=port_name, new_schema=new_schema)


def paste_state_field_schema_from_clipboard(node_item: Any, *, field_name: str) -> bool:
    new_schema = schema_from_clipboard_text(_read_value_schema_text_from_clipboard())
    if new_schema is None:
        return False
    return replace_state_field_schema(node_item, field_name=field_name, new_schema=new_schema)


def open_data_port_editor_dialog(node_item: Any, *, is_in: bool, port_name: str) -> None:
    node = node_item._backend_node()
    if node is None:
        return
    found = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
    if found is None:
        return
    port, index = found
    spec = node.spec
    editable = _policy_can_edit_existing(spec, "dataInPorts" if bool(is_in) else "dataOutPorts")
    missing_locked = bool(node.is_missing_locked())
    ui_only = bool(not editable)
    read_only = bool(missing_locked)

    from ...ui.dialogs.node_spec_edit_dialogs import _F8EditDataPortDialog
    from ...nodegraph.ui_override_mutations import (
        base_data_port_show_on_node as _base_data_port_show_on_node,
        set_data_port_show_on_node_override as _set_data_port_show_on_node_override,
    )

    dlg = _F8EditDataPortDialog(
        node_item._viewer_safe(),
        title="Edit data port",
        port=port,
        ui_only=ui_only,
        lock_identity_fields=bool(editable),
        read_only=read_only,
    )
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return
    new_port = dlg.port()
    if ui_only and not read_only:
        base_show = _base_data_port_show_on_node(spec, name=str(port.name or "").strip(), is_in=bool(is_in))
        _set_data_port_show_on_node_override(
            node,
            name=str(port_name or "").strip(),
            is_in=bool(is_in),
            show_on_node=bool(new_port.showOnNode),
            base_show_on_node=bool(base_show),
        )
        node.sync_from_spec()
        return
    if read_only:
        return

    ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
    if int(index) < 0 or int(index) >= len(ports):
        return
    ports[int(index)] = new_port
    if bool(is_in):
        updated_spec = copy_model(spec, update={"dataInPorts": ports})
    else:
        updated_spec = copy_model(spec, update={"dataOutPorts": ports})
    node.set_spec(updated_spec, rebuild=True)


def find_effective_state_field(node_item: Any, *, field_name: str) -> Any | None:
    node = node_item._backend_node()
    if node is None:
        return None
    try:
        effective_fields = list(node.effective_state_fields() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        effective_fields = []
    target_name = str(field_name or "").strip()
    for field in effective_fields:
        if str(field.name or "").strip() == target_name:
            return field
    return None


def open_state_field_editor_dialog(node_item: Any, *, field_name: str) -> None:
    node = node_item._backend_node()
    if node is None:
        return
    spec = node.spec
    current = find_effective_state_field(node_item, field_name=field_name)
    if current is None:
        found = find_state_field_spec(node_item, field_name=field_name)
        if found is None:
            return
        current, _index = found

    editable = _policy_can_edit_existing(spec, "stateFields")
    missing_locked = bool(node.is_missing_locked())
    ui_only = bool(not editable)
    read_only = bool(missing_locked)

    from ...ui.dialogs.node_spec_edit_dialogs import _F8EditStateFieldDialog
    from ...nodegraph.spec_mutations import replace_state_field as _spec_replace_state_field
    from ...nodegraph.ui_override_mutations import (
        find_base_state_field as _find_base_state_field,
        set_state_field_ui_override as _set_state_field_ui_override,
    )
    from ...nodegraph.ui_state_mutations import (
        set_state_field_global_hotkey_override as _set_state_field_global_hotkey_override,
        state_field_global_hotkey as _state_field_global_hotkey,
    )

    dlg = _F8EditStateFieldDialog(
        node_item._viewer_safe(),
        title="Edit state field",
        field=current,
        global_hotkey=_state_field_global_hotkey(node, field_name),
        current_binding_id=f"{str(node.id or '').strip()}:{str(field_name or '').strip()}",
        hotkey_conflict_lookup=(
            node.graph.global_hotkey_controller.entries_for_hotkey
            if getattr(getattr(node, "graph", None), "global_hotkey_controller", None) is not None
            else None
        ),
        hotkey_capture_started=(
            node.graph.global_hotkey_controller.suspend_hotkeys
            if getattr(getattr(node, "graph", None), "global_hotkey_controller", None) is not None
            else None
        ),
        hotkey_capture_finished=(
            node.graph.global_hotkey_controller.resume_hotkeys
            if getattr(getattr(node, "graph", None), "global_hotkey_controller", None) is not None
            else None
        ),
        ui_only=ui_only,
        lock_identity_fields=bool(editable),
        read_only=read_only,
    )
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return
    new_field = dlg.field()
    global_hotkey = dlg.global_hotkey()
    if ui_only:
        base_field = _find_base_state_field(spec, name=field_name)
        if base_field is None:
            base_field = new_field
        _set_state_field_ui_override(node, field_name=field_name, base=base_field, edited=new_field)
        _set_state_field_global_hotkey_override(node, field_name=field_name, hotkey=global_hotkey)
        node.sync_from_spec()
        return
    if read_only:
        return

    updated_spec = _spec_replace_state_field(spec, old_name=field_name, new_field=new_field)
    node.spec = updated_spec
    if str(new_field.name or "").strip() != str(field_name or "").strip():
        _set_state_field_global_hotkey_override(node, field_name=field_name, hotkey="")
    _set_state_field_global_hotkey_override(node, field_name=str(new_field.name or field_name), hotkey=global_hotkey)


def on_port_right_click(node_item: Any, port: Any, screen_pos: QtCore.QPoint) -> None:
    full_name = str(port.name or "")
    parsed = parse_schema_port_view_name(full_name)
    if parsed is None:
        return
    node = node_item._backend_node()
    if node is None:
        return
    kind, is_in, port_name = parsed
    can_edit = False
    if kind == "data":
        found_data = find_data_port_spec(node_item, is_in=bool(is_in), port_name=port_name)
        if found_data is None:
            return
        _data_port, _index = found_data
        can_edit = bool(
            _policy_can_edit_existing(node.spec, "dataInPorts" if bool(is_in) else "dataOutPorts")
            and (not bool(node.is_missing_locked()))
        )
    elif kind == "state":
        found_field = find_state_field_spec(node_item, field_name=port_name)
        if found_field is None:
            return
        _field, _index = found_field
        can_edit = bool(_policy_can_edit_existing(node.spec, "stateFields") and (not bool(node.is_missing_locked())))
    else:
        return
    menu = QtWidgets.QMenu()
    if can_edit:
        schema_action = menu.addAction("Edit valueSchema...")
    else:
        schema_action = menu.addAction("View valueSchema...")
    copy_schema_action = menu.addAction("Copy valueSchema")
    paste_schema_action = menu.addAction("Paste valueSchema")
    paste_schema_action.setEnabled(can_edit)
    if kind == "data":
        port_action = menu.addAction("Edit data port...")
    else:
        port_action = menu.addAction("Edit state field...")
    chosen = menu.exec_(screen_pos)
    if chosen is schema_action:
        if kind == "data":
            open_data_port_schema_dialog(node_item, is_in=bool(is_in), port_name=port_name)
        elif kind == "state":
            open_state_field_schema_dialog(node_item, field_name=port_name)
    elif chosen is copy_schema_action:
        if kind == "data":
            copy_data_port_schema_to_clipboard(node_item, is_in=bool(is_in), port_name=port_name)
        elif kind == "state":
            copy_state_field_schema_to_clipboard(node_item, field_name=port_name)
    elif chosen is paste_schema_action:
        if kind == "data":
            paste_data_port_schema_from_clipboard(node_item, is_in=bool(is_in), port_name=port_name)
        elif kind == "state":
            paste_state_field_schema_from_clipboard(node_item, field_name=port_name)
    elif chosen is port_action:
        if kind == "data":
            open_data_port_editor_dialog(node_item, is_in=bool(is_in), port_name=port_name)
        elif kind == "state":
            open_state_field_editor_dialog(node_item, field_name=port_name)
