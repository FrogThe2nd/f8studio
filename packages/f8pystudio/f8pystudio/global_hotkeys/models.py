from __future__ import annotations

from dataclasses import dataclass


class GlobalHotkeyError(RuntimeError):
    """Base error for global hotkey handling."""


class GlobalHotkeyParseError(GlobalHotkeyError):
    """Raised when a configured hotkey string cannot be normalized."""


class GlobalHotkeyRegistrationError(GlobalHotkeyError):
    """Raised when a backend cannot register a hotkey."""


class GlobalHotkeyUnsupportedError(GlobalHotkeyError):
    """Raised when the current platform/session cannot support global hotkeys."""


@dataclass(frozen=True)
class GlobalHotkeySpec:
    key_name: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
    display_text: str = ""


@dataclass(frozen=True)
class GlobalHotkeyBinding:
    binding_id: str
    node_id: str
    node_label: str
    field_name: str
    control_label: str
    hotkey_text: str
    hotkey_spec: GlobalHotkeySpec
    numeric_type: str
    allow_repeat: bool = True


@dataclass(frozen=True)
class GlobalHotkeyRegistryEntry:
    binding_id: str
    node_id: str
    node_label: str
    field_name: str
    control_label: str
    hotkey_text: str
    status: str
    message: str = ""
