from __future__ import annotations

from typing import Any, Protocol


class _UiStateNode(Protocol):
    def ui_state(self) -> dict[str, object]: ...

    def set_ui_state(self, value: dict[str, object] | None) -> None: ...


def get_ui_state(node: _UiStateNode) -> dict[str, Any]:
    ui_state = node.ui_state()
    return dict(ui_state) if isinstance(ui_state, dict) else {}


def set_ui_state(node: _UiStateNode, ui_state: dict[str, Any]) -> None:
    node.set_ui_state(ui_state)


def state_field_global_hotkey(node: _UiStateNode, field_name: str) -> str:
    name = str(field_name or "").strip()
    if not name:
        return ""
    try:
        ui_state = get_ui_state(node)
    except AttributeError:
        return ""
    hotkeys = ui_state.get("stateFieldHotkeys")
    if not isinstance(hotkeys, dict):
        return ""
    return str(hotkeys.get(name) or "").strip()


def set_state_field_global_hotkey_override(node: _UiStateNode, *, field_name: str, hotkey: str) -> None:
    name = str(field_name or "").strip()
    if not name:
        return
    normalized_hotkey = str(hotkey or "").strip()
    ui_state = get_ui_state(node)
    hotkeys = ui_state.get("stateFieldHotkeys")
    if not isinstance(hotkeys, dict):
        hotkeys = {}
    if normalized_hotkey:
        hotkeys[name] = normalized_hotkey
    else:
        hotkeys.pop(name, None)
    if hotkeys:
        ui_state["stateFieldHotkeys"] = hotkeys
    else:
        ui_state.pop("stateFieldHotkeys", None)
    set_ui_state(node, ui_state)


def state_inline_expanded(node: _UiStateNode, state_name: str) -> bool | None:
    key = str(state_name or "").strip()
    if not key:
        return None
    try:
        ui_state = get_ui_state(node)
    except AttributeError:
        return None
    expanded_map = ui_state.get("stateInlineExpanded")
    if not isinstance(expanded_map, dict) or key not in expanded_map:
        return None
    return bool(expanded_map.get(key))


def set_state_inline_expanded(node: _UiStateNode, *, state_name: str, expanded: bool) -> None:
    key = str(state_name or "").strip()
    if not key:
        return
    ui_state = get_ui_state(node)
    expanded_map = ui_state.get("stateInlineExpanded")
    if not isinstance(expanded_map, dict):
        expanded_map = {}
    expanded_map[key] = bool(expanded)
    ui_state["stateInlineExpanded"] = expanded_map
    set_ui_state(node, ui_state)
