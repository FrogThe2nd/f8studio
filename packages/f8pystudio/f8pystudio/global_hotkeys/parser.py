from __future__ import annotations

from .models import GlobalHotkeyParseError, GlobalHotkeySpec

_MODIFIER_ALIASES: dict[str, str] = {
    "alt": "Alt",
    "cmd": "Meta",
    "command": "Meta",
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "meta": "Meta",
    "option": "Alt",
    "shift": "Shift",
    "super": "Meta",
    "win": "Meta",
    "windows": "Meta",
}

_KEY_ALIASES: dict[str, str] = {
    "backspace": "Backspace",
    "bksp": "Backspace",
    "comma": "Comma",
    "del": "Delete",
    "delete": "Delete",
    "down": "Down",
    "end": "End",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "equal": "Equal",
    "equals": "Equal",
    "home": "Home",
    "ins": "Insert",
    "insert": "Insert",
    "left": "Left",
    "minus": "Minus",
    "pagedown": "PageDown",
    "pageup": "PageUp",
    "pgdn": "PageDown",
    "pgdown": "PageDown",
    "pgup": "PageUp",
    "period": "Period",
    "plus": "Plus",
    "quote": "Quote",
    "return": "Enter",
    "right": "Right",
    "semicolon": "Semicolon",
    "slash": "Slash",
    "space": "Space",
    "tab": "Tab",
    "up": "Up",
}

for _index in range(1, 25):
    _KEY_ALIASES[f"f{_index}"] = f"F{_index}"

for _char in "abcdefghijklmnopqrstuvwxyz":
    _KEY_ALIASES[_char] = _char.upper()

for _digit in "0123456789":
    _KEY_ALIASES[_digit] = _digit


def parse_global_hotkey(text: str) -> GlobalHotkeySpec:
    raw_text = str(text or "").strip()
    if not raw_text:
        raise GlobalHotkeyParseError("Global hotkey cannot be empty.")

    tokens = [part.strip() for part in raw_text.split("+")]
    if any(not token for token in tokens):
        raise GlobalHotkeyParseError(f"Invalid hotkey format: {raw_text!r}")

    ctrl = False
    alt = False
    shift = False
    meta = False
    key_name = ""

    for token in tokens:
        normalized_token = token.lower()
        modifier_name = _MODIFIER_ALIASES.get(normalized_token)
        if modifier_name == "Ctrl":
            ctrl = True
            continue
        if modifier_name == "Alt":
            alt = True
            continue
        if modifier_name == "Shift":
            shift = True
            continue
        if modifier_name == "Meta":
            meta = True
            continue

        normalized_key = _KEY_ALIASES.get(normalized_token)
        if normalized_key is None:
            raise GlobalHotkeyParseError(f"Unsupported hotkey token: {token!r}")
        if key_name:
            raise GlobalHotkeyParseError("Only single-key shortcuts are supported.")
        key_name = normalized_key

    if not key_name:
        raise GlobalHotkeyParseError(f"Hotkey must include a non-modifier key: {raw_text!r}")

    parts: list[str] = []
    if ctrl:
        parts.append("Ctrl")
    if alt:
        parts.append("Alt")
    if shift:
        parts.append("Shift")
    if meta:
        parts.append("Meta")
    parts.append(key_name)
    return GlobalHotkeySpec(
        key_name=key_name,
        ctrl=ctrl,
        alt=alt,
        shift=shift,
        meta=meta,
        display_text="+".join(parts),
    )
