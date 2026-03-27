from __future__ import annotations

import ctypes
import os
import platform
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from qtpy import QtCore

from .models import (
    GlobalHotkeyBinding,
    GlobalHotkeyRegistrationError,
    GlobalHotkeySpec,
    GlobalHotkeyUnsupportedError,
)

WM_HOTKEY = 0x0312


class GlobalHotkeyBackend(Protocol):
    def register_hotkey(self, binding: GlobalHotkeyBinding) -> None: ...

    def unregister_all(self) -> None: ...

    def close(self) -> None: ...


class _Win32Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Win32Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_size_t),
        ("time", ctypes.c_uint32),
        ("pt", _Win32Point),
    ]


class _Win32NativeEventFilter(QtCore.QAbstractNativeEventFilter):
    def __init__(self, *, activation_callback: Callable[[int], None]) -> None:
        super().__init__()
        self._activation_callback = activation_callback

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:  # type: ignore[override]
        event_type_text = str(event_type or "")
        if event_type_text not in {"windows_dispatcher_MSG", "windows_generic_MSG"}:
            return False, 0
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(_Win32Msg)).contents
        except (TypeError, ValueError):
            return False, 0
        if int(msg.message) != WM_HOTKEY:
            return False, 0
        self._activation_callback(int(msg.wParam))
        return False, 0


@dataclass(frozen=True)
class _Win32Apis:
    RegisterHotKey: Any
    UnregisterHotKey: Any


def _build_win32_apis() -> _Win32Apis:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    register_hot_key = user32.RegisterHotKey
    register_hot_key.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    register_hot_key.restype = ctypes.c_int
    unregister_hot_key = user32.UnregisterHotKey
    unregister_hot_key.argtypes = [ctypes.c_void_p, ctypes.c_int]
    unregister_hot_key.restype = ctypes.c_int
    return _Win32Apis(RegisterHotKey=register_hot_key, UnregisterHotKey=unregister_hot_key)


def win32_modifiers_for_hotkey(spec: GlobalHotkeySpec) -> int:
    modifiers = 0
    if spec.alt:
        modifiers |= 0x0001
    if spec.ctrl:
        modifiers |= 0x0002
    if spec.shift:
        modifiers |= 0x0004
    if spec.meta:
        modifiers |= 0x0008
    return modifiers


def win32_vk_for_hotkey(spec: GlobalHotkeySpec) -> int:
    key_name = str(spec.key_name or "")
    if len(key_name) == 1 and key_name.isalpha():
        return ord(key_name.upper())
    if len(key_name) == 1 and key_name.isdigit():
        return ord(key_name)
    if key_name.startswith("F"):
        try:
            index = int(key_name[1:])
        except ValueError as exc:
            raise GlobalHotkeyRegistrationError(f"Unsupported Windows hotkey key: {key_name!r}") from exc
        if 1 <= index <= 24:
            return 0x70 + (index - 1)
    special = {
        "Backspace": 0x08,
        "Delete": 0x2E,
        "Down": 0x28,
        "End": 0x23,
        "Enter": 0x0D,
        "Escape": 0x1B,
        "Home": 0x24,
        "Insert": 0x2D,
        "Left": 0x25,
        "PageDown": 0x22,
        "PageUp": 0x21,
        "Right": 0x27,
        "Space": 0x20,
        "Tab": 0x09,
        "Up": 0x26,
        "Comma": 0xBC,
        "Equal": 0xBB,
        "Minus": 0xBD,
        "Period": 0xBE,
        "Plus": 0xBB,
        "Quote": 0xDE,
        "Semicolon": 0xBA,
        "Slash": 0xBF,
    }
    vk = special.get(key_name)
    if vk is None:
        raise GlobalHotkeyRegistrationError(f"Unsupported Windows hotkey key: {key_name!r}")
    return int(vk)


class Win32GlobalHotkeyBackend:
    def __init__(
        self,
        *,
        activation_callback: Callable[[str], None],
        app: QtCore.QCoreApplication | None = None,
        apis: _Win32Apis | None = None,
    ) -> None:
        self._activation_callback = activation_callback
        self._app = app or QtCore.QCoreApplication.instance()
        if self._app is None:
            raise GlobalHotkeyUnsupportedError("Qt application instance is required for Windows global hotkeys.")
        self._apis = apis or _build_win32_apis()
        self._id_to_binding: dict[int, str] = {}
        self._binding_to_id: dict[str, int] = {}
        self._next_hotkey_id = 1
        self._event_filter = _Win32NativeEventFilter(activation_callback=self._on_hotkey_id_activated)
        self._app.installNativeEventFilter(self._event_filter)

    def register_hotkey(self, binding: GlobalHotkeyBinding) -> None:
        hotkey_id = self._allocate_hotkey_id(binding.binding_id)
        modifiers = win32_modifiers_for_hotkey(binding.hotkey_spec)
        vk = win32_vk_for_hotkey(binding.hotkey_spec)
        registered = int(self._apis.RegisterHotKey(None, hotkey_id, modifiers, vk))
        if not registered:
            self._binding_to_id.pop(binding.binding_id, None)
            self._id_to_binding.pop(hotkey_id, None)
            error_code = int(ctypes.get_last_error())
            raise GlobalHotkeyRegistrationError(
                f"RegisterHotKey failed binding={binding.binding_id!r} hotkey={binding.hotkey_spec.display_text!r} "
                f"error={error_code}"
            )

    def unregister_all(self) -> None:
        for hotkey_id in list(self._id_to_binding.keys()):
            try:
                self._apis.UnregisterHotKey(None, int(hotkey_id))
            except (AttributeError, TypeError, ValueError):
                continue
        self._id_to_binding.clear()
        self._binding_to_id.clear()

    def close(self) -> None:
        self.unregister_all()
        if self._app is not None:
            try:
                self._app.removeNativeEventFilter(self._event_filter)
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _allocate_hotkey_id(self, binding_id: str) -> int:
        existing_id = self._binding_to_id.get(binding_id)
        if existing_id is not None:
            return existing_id
        hotkey_id = int(self._next_hotkey_id)
        self._next_hotkey_id += 1
        self._binding_to_id[binding_id] = hotkey_id
        self._id_to_binding[hotkey_id] = binding_id
        return hotkey_id

    def _on_hotkey_id_activated(self, hotkey_id: int) -> None:
        binding_id = self._id_to_binding.get(int(hotkey_id))
        if binding_id:
            self._activation_callback(binding_id)


def x11_modifier_mask_for_hotkey(spec: GlobalHotkeySpec, x_module: Any) -> int:
    modifiers = 0
    if spec.shift:
        modifiers |= int(x_module.ShiftMask)
    if spec.ctrl:
        modifiers |= int(x_module.ControlMask)
    if spec.alt:
        modifiers |= int(x_module.Mod1Mask)
    if spec.meta:
        modifiers |= int(x_module.Mod4Mask)
    return modifiers


def _x11_keysym_name_for_hotkey(spec: GlobalHotkeySpec) -> str:
    key_name = str(spec.key_name or "")
    if len(key_name) == 1 and (key_name.isalpha() or key_name.isdigit()):
        return key_name
    return {
        "Backspace": "BackSpace",
        "Comma": "comma",
        "Delete": "Delete",
        "Down": "Down",
        "End": "End",
        "Enter": "Return",
        "Equal": "equal",
        "Escape": "Escape",
        "Home": "Home",
        "Insert": "Insert",
        "Left": "Left",
        "Minus": "minus",
        "PageDown": "Next",
        "PageUp": "Prior",
        "Period": "period",
        "Plus": "plus",
        "Quote": "apostrophe",
        "Right": "Right",
        "Semicolon": "semicolon",
        "Slash": "slash",
        "Space": "space",
        "Tab": "Tab",
        "Up": "Up",
    }.get(key_name, key_name)


def x11_keysym_for_hotkey(spec: GlobalHotkeySpec, xk_module: Any) -> int:
    keysym_name = _x11_keysym_name_for_hotkey(spec)
    keysym = int(xk_module.string_to_keysym(keysym_name))
    if not keysym:
        raise GlobalHotkeyRegistrationError(f"Unsupported X11 hotkey key: {spec.key_name!r}")
    return keysym


class X11GlobalHotkeyBackend:
    _BASE_MODIFIER_MASK = 0xFF

    def __init__(
        self,
        *,
        activation_callback: Callable[[str], None],
        display_factory: Callable[[], Any] | None = None,
        x_module: Any | None = None,
        xk_module: Any | None = None,
        error_module: Any | None = None,
        start_listener: bool = True,
    ) -> None:
        if display_factory is None or x_module is None or xk_module is None or error_module is None:
            from Xlib import X, XK, display, error

            display_factory = display.Display if display_factory is None else display_factory
            x_module = X if x_module is None else x_module
            xk_module = XK if xk_module is None else xk_module
            error_module = error if error_module is None else error_module
        self._activation_callback = activation_callback
        self._display = display_factory()
        self._x = x_module
        self._xk = xk_module
        self._error = error_module
        self._root = self._display.screen().root
        self._binding_grabs: dict[str, list[tuple[int, int]]] = {}
        self._event_bindings: dict[tuple[int, int], str] = {}
        self._stop_event = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._ignored_modifier_masks = self._build_ignored_modifier_masks()
        if start_listener:
            self._listener_thread = threading.Thread(target=self._event_loop, name="f8pystudio-x11-hotkeys", daemon=True)
            self._listener_thread.start()

    def register_hotkey(self, binding: GlobalHotkeyBinding) -> None:
        keysym = x11_keysym_for_hotkey(binding.hotkey_spec, self._xk)
        keycode = int(self._display.keysym_to_keycode(keysym))
        if keycode <= 0:
            raise GlobalHotkeyRegistrationError(
                f"Could not resolve X11 keycode for hotkey={binding.hotkey_spec.display_text!r}"
            )
        base_modifiers = x11_modifier_mask_for_hotkey(binding.hotkey_spec, self._x)
        grabs: list[tuple[int, int]] = []
        try:
            for ignored_mask in self._ignored_modifier_masks:
                event_modifiers = int(base_modifiers | ignored_mask)
                self._root.grab_key(
                    keycode,
                    event_modifiers,
                    True,
                    self._x.GrabModeAsync,
                    self._x.GrabModeAsync,
                )
                grabs.append((keycode, event_modifiers))
                self._event_bindings[(keycode, event_modifiers)] = binding.binding_id
            self._display.sync()
        except self._error.BadAccess as exc:  # type: ignore[attr-defined]
            for keycode_value, modifiers_value in grabs:
                self._root.ungrab_key(keycode_value, modifiers_value)
                self._event_bindings.pop((keycode_value, modifiers_value), None)
            self._display.sync()
            raise GlobalHotkeyRegistrationError(
                f"X11 grab failed binding={binding.binding_id!r} hotkey={binding.hotkey_spec.display_text!r}"
            ) from exc
        self._binding_grabs[binding.binding_id] = grabs

    def unregister_all(self) -> None:
        for binding_id, grabs in list(self._binding_grabs.items()):
            for keycode_value, modifiers_value in grabs:
                try:
                    self._root.ungrab_key(keycode_value, modifiers_value)
                except Exception:
                    continue
                self._event_bindings.pop((keycode_value, modifiers_value), None)
            self._binding_grabs.pop(binding_id, None)
        try:
            self._display.sync()
        except Exception:
            pass

    def close(self) -> None:
        self._stop_event.set()
        self.unregister_all()
        if self._listener_thread is not None and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=0.2)
        try:
            self._display.close()
        except Exception:
            pass

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if int(self._display.pending_events()) <= 0:
                    time.sleep(0.01)
                    continue
                event = self._display.next_event()
            except Exception:
                if self._stop_event.is_set():
                    return
                time.sleep(0.05)
                continue
            try:
                event_type = int(getattr(event, "type", -1))
                if event_type != int(self._x.KeyPress):
                    continue
                keycode = int(getattr(event, "detail", 0))
                state = int(getattr(event, "state", 0)) & self._BASE_MODIFIER_MASK
                binding_id = self._event_bindings.get((keycode, state))
                if binding_id:
                    self._activation_callback(binding_id)
            except Exception:
                continue

    def _build_ignored_modifier_masks(self) -> tuple[int, ...]:
        masks = [0, int(self._x.LockMask)]
        num_lock_mask = self._modifier_mask_for_keysym_name("Num_Lock")
        if num_lock_mask:
            masks.extend([int(mask | num_lock_mask) for mask in list(masks)])
        return tuple(sorted(set(int(mask) for mask in masks)))

    def _modifier_mask_for_keysym_name(self, keysym_name: str) -> int:
        keysym = int(self._xk.string_to_keysym(keysym_name))
        if not keysym:
            return 0
        keycode = int(self._display.keysym_to_keycode(keysym))
        if keycode <= 0:
            return 0
        try:
            modifier_map = self._display.get_modifier_mapping()
        except Exception:
            return 0
        masks = [
            int(getattr(self._x, "ShiftMask", 0)),
            int(getattr(self._x, "LockMask", 0)),
            int(getattr(self._x, "ControlMask", 0)),
            int(getattr(self._x, "Mod1Mask", 0)),
            int(getattr(self._x, "Mod2Mask", 0)),
            int(getattr(self._x, "Mod3Mask", 0)),
            int(getattr(self._x, "Mod4Mask", 0)),
            int(getattr(self._x, "Mod5Mask", 0)),
        ]
        for index, entries in enumerate(list(modifier_map or [])):
            for entry in list(entries or []):
                if int(entry or 0) == keycode:
                    if 0 <= index < len(masks):
                        return masks[index]
        return 0


def create_global_hotkey_backend(
    *,
    activation_callback: Callable[[str], None],
    platform_name: str | None = None,
) -> GlobalHotkeyBackend:
    current_platform = str(platform_name or platform.system() or "").strip().lower()
    if current_platform.startswith("win"):
        return Win32GlobalHotkeyBackend(activation_callback=activation_callback)
    if current_platform == "linux":
        session_type = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
        display_name = str(os.environ.get("DISPLAY") or "").strip()
        if session_type and session_type != "x11":
            raise GlobalHotkeyUnsupportedError(
                f"Linux global hotkeys require an X11 session; current session type is {session_type!r}."
            )
        if not display_name:
            raise GlobalHotkeyUnsupportedError("Linux global hotkeys require an X11 DISPLAY.")
        return X11GlobalHotkeyBackend(activation_callback=activation_callback)
    raise GlobalHotkeyUnsupportedError(f"Global hotkeys are unsupported on platform: {current_platform!r}")
