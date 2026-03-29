from __future__ import annotations

from typing import Any, Protocol

import msgspec
from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8StateSpec


class _UiOverrideNode(Protocol):
    def ui_overrides(self) -> dict[str, object]: ...

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None: ...


def get_ui_overrides(node: _UiOverrideNode) -> dict[str, Any]:
    ui = node.ui_overrides()
    return dict(ui) if isinstance(ui, dict) else {}


def set_ui_overrides(node: _UiOverrideNode, ui: dict[str, Any], *, rebuild: bool) -> None:
    node.set_ui_overrides(ui, rebuild=bool(rebuild))


def normalize_named_order(values: list[str] | tuple[str, ...] | None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_value in list(values or []):
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def apply_named_order(*, base_names: list[str] | tuple[str, ...], override_names: list[str] | tuple[str, ...] | None) -> list[str]:
    base = normalize_named_order(list(base_names))
    override = normalize_named_order(list(override_names or []))
    if not base:
        return []
    if not override:
        return list(base)

    base_set = set(base)
    ordered = [name for name in override if name in base_set]
    ordered_set = set(ordered)
    for name in base:
        if name in ordered_set:
            continue
        ordered.append(name)
    return ordered


def get_list_order_override(node: _UiOverrideNode, *, key: str) -> list[str]:
    name = str(key or "").strip()
    if not name:
        return []
    ui = get_ui_overrides(node)
    list_order = ui.get("listOrder")
    if not isinstance(list_order, dict):
        return []
    values = list_order.get(name)
    if not isinstance(values, list):
        return []
    return normalize_named_order(values)


def set_list_order_override(
    node: _UiOverrideNode,
    *,
    key: str,
    order: list[str] | tuple[str, ...],
    base_order: list[str] | tuple[str, ...],
    rebuild: bool = True,
) -> None:
    name = str(key or "").strip()
    if not name:
        return
    normalized_base = normalize_named_order(list(base_order))
    normalized_order = apply_named_order(base_names=normalized_base, override_names=list(order))

    ui = get_ui_overrides(node)
    list_order = ui.get("listOrder")
    if not isinstance(list_order, dict):
        list_order = {}

    if normalized_order == normalized_base or not normalized_order:
        list_order.pop(name, None)
    else:
        list_order[name] = list(normalized_order)

    if list_order:
        ui["listOrder"] = list_order
    else:
        ui.pop("listOrder", None)
    set_ui_overrides(node, ui, rebuild=bool(rebuild))


def rename_list_order_entry(
    node: _UiOverrideNode,
    *,
    key: str,
    old_name: str,
    new_name: str,
    base_order: list[str] | tuple[str, ...],
    rebuild: bool = True,
) -> None:
    normalized_old = str(old_name or "").strip()
    normalized_new = str(new_name or "").strip()
    if not normalized_old or not normalized_new:
        return
    current_order = get_list_order_override(node, key=key)
    if not current_order:
        current_order = normalize_named_order(list(base_order))
    renamed = [normalized_new if name == normalized_old else name for name in current_order]
    set_list_order_override(node, key=key, order=renamed, base_order=base_order, rebuild=bool(rebuild))


def remove_list_order_entry(
    node: _UiOverrideNode,
    *,
    key: str,
    entry_name: str,
    base_order: list[str] | tuple[str, ...],
    rebuild: bool = True,
) -> None:
    normalized_name = str(entry_name or "").strip()
    if not normalized_name:
        return
    current_order = get_list_order_override(node, key=key)
    if not current_order:
        current_order = normalize_named_order(list(base_order))
    filtered = [name for name in current_order if name != normalized_name]
    set_list_order_override(node, key=key, order=filtered, base_order=base_order, rebuild=bool(rebuild))


def _diff_state_ui(base: F8StateSpec, edited: F8StateSpec) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if edited.showOnNode != base.showOnNode:
        patch["showOnNode"] = edited.showOnNode
    base_ui_control = str(base.uiControl or "").strip()
    edited_ui_control = str(edited.uiControl or "").strip()
    if edited_ui_control != base_ui_control:
        if edited_ui_control:
            patch["uiControl"] = edited_ui_control
        else:
            patch["uiControl"] = msgspec.UNSET
    if edited.label != base.label:
        patch["label"] = edited.label
    if edited.description != base.description:
        patch["description"] = edited.description
    return patch


def set_state_field_ui_override(node: _UiOverrideNode, *, field_name: str, base: F8StateSpec, edited: F8StateSpec) -> None:
    """
    Persist UI-only overrides for a state field.

    Stores only diffs; if there are no diffs, removes the override entry.
    """
    name = str(field_name or "").strip()
    if not name:
        return
    patch = _diff_state_ui(base, edited)

    ui = get_ui_overrides(node)
    state_over = ui.get("stateFields")
    if not isinstance(state_over, dict):
        state_over = {}
    patch = {key: value for key, value in patch.items() if value is not msgspec.UNSET}
    if patch:
        state_over[name] = patch
    else:
        state_over.pop(name, None)
    if state_over:
        ui["stateFields"] = state_over
    else:
        ui.pop("stateFields", None)
    set_ui_overrides(node, ui, rebuild=True)


def set_command_show_on_node_override(
    node: _UiOverrideNode,
    *,
    name: str,
    show_on_node: bool,
    base_show_on_node: bool,
) -> None:
    """
    Persist UI-only overrides for a command (currently only showOnNode).

    Stores only diffs; if value matches base spec, removes the override entry.
    """
    n = str(name or "").strip()
    if not n:
        return
    ui = get_ui_overrides(node)
    cmd_over = ui.get("commands")
    if not isinstance(cmd_over, dict):
        cmd_over = {}
    if bool(show_on_node) == bool(base_show_on_node):
        cmd_over.pop(n, None)
    else:
        cmd_over[n] = {"showOnNode": bool(show_on_node)}
    if cmd_over:
        ui["commands"] = cmd_over
    else:
        ui.pop("commands", None)
    set_ui_overrides(node, ui, rebuild=True)


def base_command_show_on_node(spec: F8ServiceSpec | None, *, name: str) -> bool:
    if spec is None:
        return False
    n = str(name or "").strip()
    if not n:
        return False
    for c in list(spec.commands or []):
        if str(c.name or "").strip() == n:
            return bool(c.showOnNode)
    return False


def base_data_port_show_on_node(spec: F8ServiceSpec | F8OperatorSpec | None, *, name: str, is_in: bool) -> bool:
    n = str(name or "").strip()
    if not n:
        return True
    if spec is None:
        return True
    ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
    for p in ports:
        if str(p.name or "").strip() == n:
            return bool(p.showOnNode)
    return True


def set_data_port_show_on_node_override(
    node: _UiOverrideNode,
    *,
    name: str,
    is_in: bool,
    show_on_node: bool,
    base_show_on_node: bool,
) -> None:
    """
    Persist UI-only overrides for a data port (currently only showOnNode).

    Stores only diffs; if value matches base spec, removes the override entry.
    """
    n = str(name or "").strip()
    if not n:
        return
    ui = get_ui_overrides(node)
    ports_over = ui.get("dataPorts")
    if not isinstance(ports_over, dict):
        ports_over = {}
    key = "in" if bool(is_in) else "out"
    dir_over = ports_over.get(key)
    if not isinstance(dir_over, dict):
        dir_over = {}

    if bool(show_on_node) == bool(base_show_on_node):
        dir_over.pop(n, None)
    else:
        dir_over[n] = {"showOnNode": bool(show_on_node)}

    if dir_over:
        ports_over[key] = dir_over
    else:
        ports_over.pop(key, None)

    if ports_over:
        ui["dataPorts"] = ports_over
    else:
        ui.pop("dataPorts", None)

    set_ui_overrides(node, ui, rebuild=True)


def find_base_state_field(spec: F8ServiceSpec | F8OperatorSpec | None, *, name: str) -> F8StateSpec | None:
    n = str(name or "").strip()
    if not n or spec is None:
        return None
    fields = list(spec.stateFields or [])
    for f in fields:
        if str(f.name or "").strip() == n:
            return f
    return None
