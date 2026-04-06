from __future__ import annotations

import queue
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk.generated import F8StateAccess, F8StateSpec
from f8pysdk.schema_helpers import integer_schema, number_schema, string_schema

from f8pystudio.global_hotkeys.backend import (
    WM_HOTKEY,
    Win32GlobalHotkeyBackend,
    _Win32Apis,
    X11GlobalHotkeyBackend,
    win32_modifiers_for_hotkey,
    win32_vk_for_hotkey,
    x11_modifier_mask_for_hotkey,
)
from f8pystudio.global_hotkeys.controller import ControlPanelGlobalHotkeyController
from f8pystudio.global_hotkeys.models import GlobalHotkeyBinding
from f8pystudio.global_hotkeys.parser import parse_global_hotkey
from f8pystudio.nodegraph.node_model import F8StudioNodeModel
from f8pystudio.ui.widgets import node_property_panel as npw
from f8pystudio.nodegraph.ui_state_mutations import (
    set_state_field_global_hotkey_override,
    state_field_global_hotkey,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _UiOverrideNodeStub:
    def __init__(self) -> None:
        self._ui_state: dict[str, object] = {}

    def ui_state(self) -> dict[str, object]:
        return dict(self._ui_state)

    def set_ui_state(self, value: dict[str, object] | None) -> None:
        self._ui_state = dict(value or {})


class _HotkeyNodeStub:
    def __init__(
        self,
        *,
        node_id: str,
        field: F8StateSpec,
        hotkey: str,
        value: Any,
    ) -> None:
        self.id = node_id
        self._node_name = f"Node {node_id}"
        self._fields = [field]
        self._values = {str(field.name): value}
        self._ui_state = {"stateFieldHotkeys": {str(field.name): hotkey}}
        self.set_calls: list[tuple[str, Any, bool]] = []

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self._fields)

    def ui_state(self) -> dict[str, object]:
        return dict(self._ui_state)

    def get_property(self, name: str) -> Any:
        return self._values[name]

    def set_property(self, name: str, value: Any, *, push_undo: bool = True) -> None:
        self._values[name] = value
        self.set_calls.append((name, value, bool(push_undo)))

    def name(self) -> str:
        return self._node_name

    @property
    def spec(self) -> Any:
        return SimpleNamespace(label=f"Label {self.id}")


class _GraphStub:
    def __init__(self, nodes: list[_HotkeyNodeStub]) -> None:
        self._nodes = {str(node.id): node for node in nodes}

    def all_nodes(self) -> list[_HotkeyNodeStub]:
        return list(self._nodes.values())

    def get_node_by_id(self, node_id: str) -> _HotkeyNodeStub | None:
        return self._nodes.get(str(node_id))


class _BackendStub:
    def __init__(self) -> None:
        self.registered: list[GlobalHotkeyBinding] = []
        self.unregister_calls = 0
        self.closed = False

    def register_hotkey(self, binding: GlobalHotkeyBinding) -> None:
        self.registered.append(binding)

    def unregister_all(self) -> None:
        self.unregister_calls += 1
        self.registered = []

    def close(self) -> None:
        self.closed = True


class _FakeX:
    ShiftMask = 0x01
    LockMask = 0x02
    ControlMask = 0x04
    Mod1Mask = 0x08
    Mod2Mask = 0x10
    Mod3Mask = 0x20
    Mod4Mask = 0x40
    Mod5Mask = 0x80
    KeyPress = 2
    GrabModeAsync = 1


class _FakeBadAccess(Exception):
    pass


class _FakeRoot:
    def __init__(self) -> None:
        self.grab_calls: list[tuple[int, int]] = []
        self.ungrab_calls: list[tuple[int, int]] = []

    def grab_key(self, keycode: int, modifiers: int, owner_events: bool, pointer_mode: int, keyboard_mode: int) -> None:
        _ = (owner_events, pointer_mode, keyboard_mode)
        self.grab_calls.append((int(keycode), int(modifiers)))

    def ungrab_key(self, keycode: int, modifiers: int) -> None:
        self.ungrab_calls.append((int(keycode), int(modifiers)))


class _FakeDisplay:
    def __init__(self) -> None:
        self.root = _FakeRoot()
        self.closed = False
        self.sync_calls = 0
        self._keysym_to_keycode = {42: 12, 77: 99}

    def screen(self) -> Any:
        return SimpleNamespace(root=self.root)

    def keysym_to_keycode(self, keysym: int) -> int:
        return self._keysym_to_keycode.get(int(keysym), int(keysym))

    def get_modifier_mapping(self) -> list[list[int]]:
        return [[], [], [], [], [99], [], [], []]

    def sync(self) -> None:
        self.sync_calls += 1

    def pending_events(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class _FakeXK:
    _KEYSYM_MAP = {
        "P": 42,
        "Num_Lock": 77,
    }

    @classmethod
    def string_to_keysym(cls, name: str) -> int:
        return int(cls._KEYSYM_MAP.get(str(name), 0))


class _FakeWin32Apis:
    def __init__(self) -> None:
        self.register_calls: list[tuple[int, int, int]] = []
        self.unregister_calls: list[int] = []
        self._messages: queue.Queue[tuple[int, int, int, int]] = queue.Queue()
        self._thread_id = 0
        self._register_error = 0
        self._last_error = 0

    def RegisterHotKey(self, hwnd: Any, hotkey_id: int, modifiers: int, vk: int) -> int:
        _ = hwnd
        self.register_calls.append((int(hotkey_id), int(modifiers), int(vk)))
        if self._register_error:
            self._last_error = int(self._register_error)
            return 0
        self._last_error = 0
        return 1

    def UnregisterHotKey(self, hwnd: Any, hotkey_id: int) -> int:
        _ = hwnd
        self.unregister_calls.append(int(hotkey_id))
        return 1

    def PeekMessageW(self, msg: Any, hwnd: Any, min_filter: int, max_filter: int, remove_msg: int) -> int:
        _ = (msg, hwnd, min_filter, max_filter, remove_msg)
        self._thread_id = int(threading.get_ident())
        return 0

    def GetMessageW(self, msg: Any, hwnd: Any, min_filter: int, max_filter: int) -> int:
        _ = (hwnd, min_filter, max_filter)
        message, w_param, l_param, result = self._messages.get(timeout=2.0)
        msg._obj.message = int(message)
        msg._obj.wParam = int(w_param)
        msg._obj.lParam = int(l_param)
        return int(result)

    def PostThreadMessageW(self, thread_id: int, message: int, w_param: int, l_param: int) -> int:
        if int(thread_id) != int(self._thread_id):
            return 0
        self._messages.put((int(message), int(w_param), int(l_param), 1))
        return 1

    def GetCurrentThreadId(self) -> int:
        return int(threading.get_ident())

    def SetLastError(self, value: int) -> None:
        self._last_error = int(value)

    def GetLastError(self) -> int:
        return int(self._last_error)

    def emit_hotkey(self, hotkey_id: int) -> None:
        self._messages.put((WM_HOTKEY, int(hotkey_id), 0, 1))


def _fake_win32_api_bundle(fake: _FakeWin32Apis) -> _Win32Apis:
    return _Win32Apis(
        RegisterHotKey=fake.RegisterHotKey,
        UnregisterHotKey=fake.UnregisterHotKey,
        PeekMessageW=fake.PeekMessageW,
        GetMessageW=fake.GetMessageW,
        PostThreadMessageW=fake.PostThreadMessageW,
        GetCurrentThreadId=fake.GetCurrentThreadId,
        SetLastError=fake.SetLastError,
        GetLastError=fake.GetLastError,
    )


def test_parse_global_hotkey_normalizes_qt_style_text() -> None:
    spec = parse_global_hotkey(" ctrl + alt + p ")

    assert spec.key_name == "P"
    assert spec.ctrl is True
    assert spec.alt is True
    assert spec.shift is False
    assert spec.meta is False
    assert spec.display_text == "Ctrl+Alt+P"


def test_parse_global_hotkey_rejects_multiple_non_modifier_keys() -> None:
    with pytest.raises(Exception, match="single-key shortcuts"):
        parse_global_hotkey("Ctrl+A+B")


def test_state_field_global_hotkey_override_round_trip() -> None:
    node = _UiOverrideNodeStub()

    set_state_field_global_hotkey_override(node, field_name="trigger", hotkey="Ctrl+Alt+P")
    assert state_field_global_hotkey(node, "trigger") == "Ctrl+Alt+P"

    set_state_field_global_hotkey_override(node, field_name="trigger", hotkey="")
    assert state_field_global_hotkey(node, "trigger") == ""
    assert node.ui_state() == {}


def test_node_model_serializes_global_hotkey_ui_state() -> None:
    model = F8StudioNodeModel()
    model.id = "nodeA"  # type: ignore[attr-defined]
    model.set_property("f8_ui_state", {"stateFieldHotkeys": {"trigger": "Ctrl+Alt+P"}})

    serialized = model.to_dict["nodeA"]

    assert serialized["f8_ui_state"]["stateFieldHotkeys"]["trigger"] == "Ctrl+Alt+P"


def test_win32_hotkey_mapping_returns_expected_values() -> None:
    spec = parse_global_hotkey("Ctrl+Alt+F5")

    assert win32_modifiers_for_hotkey(spec) == 0x0001 | 0x0002
    assert win32_vk_for_hotkey(spec) == 0x74


def test_win32_backend_dispatches_hotkey_activation_from_worker_thread() -> None:
    _ensure_app()
    fake_apis = _FakeWin32Apis()
    activated: list[str] = []
    backend = Win32GlobalHotkeyBackend(
        activation_callback=activated.append,
        apis=_fake_win32_api_bundle(fake_apis),
    )
    binding = GlobalHotkeyBinding(
        binding_id="nodeA:trigger",
        node_id="nodeA",
        node_label="Node A",
        field_name="trigger",
        control_label="Trigger",
        hotkey_text="Ctrl+Alt+P",
        hotkey_spec=parse_global_hotkey("Ctrl+Alt+P"),
        numeric_type="integer",
    )

    backend.register_hotkey(binding)
    fake_apis.emit_hotkey(1)
    QtWidgets.QApplication.processEvents()
    QtCore.QThread.msleep(10)
    QtWidgets.QApplication.processEvents()

    assert activated == ["nodeA:trigger"]
    backend.close()


def test_win32_backend_unregister_all_ignores_stale_hotkey_ids() -> None:
    _ensure_app()
    fake_apis = _FakeWin32Apis()
    activated: list[str] = []
    backend = Win32GlobalHotkeyBackend(
        activation_callback=activated.append,
        apis=_fake_win32_api_bundle(fake_apis),
    )
    binding = GlobalHotkeyBinding(
        binding_id="nodeA:trigger",
        node_id="nodeA",
        node_label="Node A",
        field_name="trigger",
        control_label="Trigger",
        hotkey_text="Ctrl+Alt+P",
        hotkey_spec=parse_global_hotkey("Ctrl+Alt+P"),
        numeric_type="integer",
    )

    backend.register_hotkey(binding)
    backend.unregister_all()
    fake_apis.emit_hotkey(1)
    QtWidgets.QApplication.processEvents()
    QtCore.QThread.msleep(10)
    QtWidgets.QApplication.processEvents()

    assert activated == []
    assert fake_apis.unregister_calls == [1]
    backend.close()


def test_win32_backend_close_stops_worker_and_unregisters_bindings() -> None:
    _ensure_app()
    fake_apis = _FakeWin32Apis()
    backend = Win32GlobalHotkeyBackend(
        activation_callback=lambda binding_id: None,
        apis=_fake_win32_api_bundle(fake_apis),
    )
    binding = GlobalHotkeyBinding(
        binding_id="nodeA:trigger",
        node_id="nodeA",
        node_label="Node A",
        field_name="trigger",
        control_label="Trigger",
        hotkey_text="Ctrl+Alt+P",
        hotkey_spec=parse_global_hotkey("Ctrl+Alt+P"),
        numeric_type="integer",
    )

    backend.register_hotkey(binding)
    backend.close()

    assert fake_apis.unregister_calls == [1]
    assert backend._worker._thread.is_alive() is False


def test_win32_backend_register_failure_preserves_error_message() -> None:
    _ensure_app()
    fake_apis = _FakeWin32Apis()
    fake_apis._register_error = 5
    backend = Win32GlobalHotkeyBackend(
        activation_callback=lambda binding_id: None,
        apis=_fake_win32_api_bundle(fake_apis),
    )
    binding = GlobalHotkeyBinding(
        binding_id="nodeA:trigger",
        node_id="nodeA",
        node_label="Node A",
        field_name="trigger",
        control_label="Trigger",
        hotkey_text="Ctrl+Alt+P",
        hotkey_spec=parse_global_hotkey("Ctrl+Alt+P"),
        numeric_type="integer",
    )

    with pytest.raises(Exception, match=r"RegisterHotKey failed binding='nodeA:trigger' hotkey='Ctrl\+Alt\+P' error=5"):
        backend.register_hotkey(binding)

    backend.close()


def test_state_field_dialog_captures_hotkey_from_key_sequence_editor() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="button",
        ),
        global_hotkey="",
    )

    dialog._global_hotkey._editor.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+P"))
    dialog._global_hotkey._normalize_sequence()

    assert dialog.global_hotkey() == "Ctrl+Alt+P"
    assert dialog._global_hotkey._editor.maximumSequenceLength() == 1


def test_state_field_dialog_ignores_hotkey_for_non_button_controls() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="label",
            valueSchema=string_schema(default=""),
            access=F8StateAccess.rw,
            uiControl="wrapline",
        ),
        global_hotkey="Ctrl+Alt+P",
    )

    assert dialog._global_hotkey.isEnabled() is False
    assert dialog.global_hotkey() == ""


def test_state_field_dialog_field_preserves_explicit_empty_ui_control() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="label",
            valueSchema=string_schema(default=""),
            access=F8StateAccess.rw,
            uiControl="dial",
        ),
        global_hotkey="",
    )

    dialog._ui_control.setText("")

    assert str(dialog.field().uiControl or "") == ""


def test_state_field_dialog_enables_hotkey_for_button_ui_control_with_whitespace() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="  button  ",
        ),
        global_hotkey="Ctrl+Alt+P",
    )

    assert dialog._global_hotkey.isEnabled() is True
    assert dialog.global_hotkey() == "Ctrl+Alt+P"


def test_state_field_dialog_shows_conflict_for_existing_hotkey() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="button",
        ),
        global_hotkey="Ctrl+Alt+P",
        hotkey_conflict_lookup=lambda hotkey, exclude_binding_id: [
            SimpleNamespace(
                node_id="nodeA",
                node_label="Label nodeA",
                control_label="Trigger",
                field_name="trigger",
            )
        ],
    )

    assert "Already used by nodeA" in dialog._global_hotkey._status.text()
    assert dialog._buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled() is False


def test_state_field_dialog_commit_button_only_enabled_during_capture() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="button",
        ),
        global_hotkey="",
    )

    assert dialog._global_hotkey._commit_btn.isEnabled() is False
    dialog._global_hotkey._on_capture_started()
    assert dialog._global_hotkey._commit_btn.isEnabled() is False
    dialog._global_hotkey._editor.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+P"))
    dialog._global_hotkey._normalize_sequence()
    assert dialog._global_hotkey._commit_btn.isEnabled() is True
    dialog._global_hotkey.release_capture()
    assert dialog._global_hotkey._commit_btn.isEnabled() is False


def test_state_field_dialog_commit_button_submits_current_hotkey() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="button",
        ),
        global_hotkey="",
    )
    accepted: list[int] = []
    dialog.accepted.connect(lambda: accepted.append(1))  # type: ignore[attr-defined]

    dialog._global_hotkey._on_capture_started()
    dialog._global_hotkey._editor.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+P"))
    dialog._global_hotkey._commit_btn.click()

    assert accepted == [1]


def test_state_field_dialog_conflicting_hotkey_disables_commit_controls() -> None:
    _ensure_app()
    dialog = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(
            name="trigger",
            valueSchema=integer_schema(default=0),
            access=F8StateAccess.rw,
            uiControl="button",
        ),
        global_hotkey="",
        hotkey_conflict_lookup=lambda hotkey, exclude_binding_id: [
            SimpleNamespace(
                node_id="nodeA",
                node_label="Label nodeA",
                control_label="Trigger",
                field_name="trigger",
            )
        ]
        if str(hotkey).strip() == "Ctrl+Alt+P"
        else [],
    )

    dialog._global_hotkey._on_capture_started()
    dialog._global_hotkey._editor.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+P"))
    dialog._global_hotkey._normalize_sequence()

    assert dialog._global_hotkey._commit_btn.isEnabled() is False
    assert dialog._buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled() is False


def test_x11_backend_registers_and_unregisters_modifier_variants() -> None:
    display = _FakeDisplay()
    backend = X11GlobalHotkeyBackend(
        activation_callback=lambda binding_id: None,
        display_factory=lambda: display,
        x_module=_FakeX,
        xk_module=_FakeXK,
        error_module=SimpleNamespace(BadAccess=_FakeBadAccess),
        start_listener=False,
    )
    binding = GlobalHotkeyBinding(
        binding_id="nodeA:trigger",
        node_id="nodeA",
        node_label="Node A",
        field_name="trigger",
        control_label="Trigger",
        hotkey_text="Ctrl+Alt+P",
        hotkey_spec=parse_global_hotkey("Ctrl+Alt+P"),
        numeric_type="integer",
    )

    backend.register_hotkey(binding)

    expected_base = x11_modifier_mask_for_hotkey(binding.hotkey_spec, _FakeX)
    expected_modifiers = {
        expected_base,
        expected_base | _FakeX.LockMask,
        expected_base | _FakeX.Mod2Mask,
        expected_base | _FakeX.LockMask | _FakeX.Mod2Mask,
    }
    assert {modifiers for _keycode, modifiers in display.root.grab_calls} == expected_modifiers

    backend.unregister_all()
    assert {modifiers for _keycode, modifiers in display.root.ungrab_calls} == expected_modifiers
    backend.close()
    assert display.closed is True


def test_controller_discovers_valid_button_bindings_and_triggers_increment() -> None:
    _ensure_app()
    valid_field = F8StateSpec(
        name="trigger",
        valueSchema=integer_schema(default=0),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    invalid_field = F8StateSpec(
        name="label",
        valueSchema=string_schema(default=""),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    valid_node = _HotkeyNodeStub(node_id="nodeA", field=valid_field, hotkey="Ctrl+Alt+P", value=0)
    invalid_node = _HotkeyNodeStub(node_id="nodeB", field=invalid_field, hotkey="Ctrl+Alt+L", value="x")
    backend = _BackendStub()
    graph = _GraphStub([valid_node, invalid_node])
    controller = ControlPanelGlobalHotkeyController(studio_graph=graph, backend=backend)

    controller.refresh_bindings()

    assert [binding.binding_id for binding in backend.registered] == ["nodeA:trigger"]
    controller.binding_activated.emit("nodeA:trigger")
    assert valid_node.set_calls == [("trigger", 1, False)]
    controller.close()
    assert backend.closed is True


def test_controller_registry_marks_duplicate_hotkeys_as_conflict() -> None:
    _ensure_app()
    field = F8StateSpec(
        name="trigger",
        label="Trigger",
        valueSchema=integer_schema(default=0),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    first_node = _HotkeyNodeStub(node_id="nodeA", field=field, hotkey="Ctrl+Alt+P", value=0)
    second_node = _HotkeyNodeStub(node_id="nodeB", field=field, hotkey="Ctrl+Alt+P", value=0)
    backend = _BackendStub()
    graph = _GraphStub([first_node, second_node])
    controller = ControlPanelGlobalHotkeyController(studio_graph=graph, backend=backend)

    controller.refresh_bindings()

    entries = controller.registry_entries()
    assert [binding.binding_id for binding in backend.registered] == ["nodeA:trigger"]
    assert [entry.status for entry in entries] == ["registered", "conflict"]
    assert controller.entries_for_hotkey("Ctrl+Alt+P", exclude_binding_id="nodeA:trigger")[0].binding_id == "nodeB:trigger"


def test_controller_entries_for_hotkey_scans_live_bindings_without_refresh() -> None:
    _ensure_app()
    field = F8StateSpec(
        name="trigger",
        label="Trigger",
        valueSchema=integer_schema(default=0),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    first_node = _HotkeyNodeStub(node_id="nodeA", field=field, hotkey="Ctrl+Alt+P", value=0)
    second_node = _HotkeyNodeStub(node_id="nodeB", field=field, hotkey="Ctrl+Alt+P", value=0)
    controller = ControlPanelGlobalHotkeyController(studio_graph=_GraphStub([first_node, second_node]), backend=_BackendStub())

    matches = controller.entries_for_hotkey(" ctrl + alt + p ", exclude_binding_id="nodeA:trigger")

    assert [entry.binding_id for entry in matches] == ["nodeB:trigger"]
    assert matches[0].status == "configured"


def test_controller_suspend_unregisters_and_marks_entries_paused() -> None:
    _ensure_app()
    field = F8StateSpec(
        name="trigger",
        valueSchema=integer_schema(default=0),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    node = _HotkeyNodeStub(node_id="nodeA", field=field, hotkey="Ctrl+Alt+P", value=0)
    backend = _BackendStub()
    graph = _GraphStub([node])
    controller = ControlPanelGlobalHotkeyController(studio_graph=graph, backend=backend)

    controller.refresh_bindings()
    controller.suspend_hotkeys()

    assert backend.unregister_calls >= 2
    assert controller.registry_entries()[0].status == "paused"

    controller.resume_hotkeys()
    assert controller.registry_entries()[0].status == "registered"


def test_controller_discovers_button_bindings_from_canonical_ui_control_text() -> None:
    _ensure_app()
    valid_field = F8StateSpec(
        name="trigger",
        valueSchema=integer_schema(default=0),
        access=F8StateAccess.rw,
        uiControl=" button ",
    )
    valid_node = _HotkeyNodeStub(node_id="nodeA", field=valid_field, hotkey="Ctrl+Alt+P", value=0)
    backend = _BackendStub()
    graph = _GraphStub([valid_node])
    controller = ControlPanelGlobalHotkeyController(studio_graph=graph, backend=backend)

    controller.refresh_bindings()

    assert [binding.binding_id for binding in backend.registered] == ["nodeA:trigger"]


def test_controller_increments_float_bindings_and_ignores_invalid_hotkeys() -> None:
    _ensure_app()
    float_field = F8StateSpec(
        name="pulse",
        valueSchema=number_schema(default=0.0),
        access=F8StateAccess.rw,
        uiControl="button",
    )
    float_node = _HotkeyNodeStub(node_id="nodeA", field=float_field, hotkey="Ctrl+Shift+P", value=1.5)
    bad_node = _HotkeyNodeStub(node_id="nodeB", field=float_field, hotkey="Ctrl++", value=0.0)
    backend = _BackendStub()
    graph = _GraphStub([float_node, bad_node])
    controller = ControlPanelGlobalHotkeyController(studio_graph=graph, backend=backend)

    controller.refresh_bindings()

    assert [binding.binding_id for binding in backend.registered] == ["nodeA:pulse"]
    controller.binding_activated.emit("nodeA:pulse")
    assert float_node.set_calls == [("pulse", 2.5, False)]
