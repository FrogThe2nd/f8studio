from __future__ import annotations

from typing import Any

from f8pystudio.bridge.nats_lifecycle import SINGLETON_GUARD_DIALOG_TITLE
from f8pystudio.pystudio_program import PyStudioProgram
from f8pystudio.pystudio_service_bridge import STARTUP_GATE_TIMEOUT_S


class _FakeApp:
    last_instance: "_FakeApp | None" = None

    def __init__(self, _args: list[str]) -> None:
        self.organization_name = ""
        self.application_name = ""
        self.window_icon: object | None = None
        self.process_events_called = False
        _FakeApp.last_instance = self

    def setOrganizationName(self, value: str) -> None:
        self.organization_name = str(value)

    def setApplicationName(self, value: str) -> None:
        self.application_name = str(value)

    def setWindowIcon(self, icon: object) -> None:
        self.window_icon = icon

    def processEvents(self) -> None:
        self.process_events_called = True

    def exec_(self) -> int:
        return 0


class _FakeMainWindow:
    last_instance: "_FakeMainWindow | None" = None

    def __init__(
        self,
        node_classes: list[type],
        parent: object | None = None,
        *,
        bridge: "_FakeBridge | None" = None,
    ) -> None:
        self.node_classes = list(node_classes)
        self.parent = parent
        self.bridge = bridge
        self.shown = False
        self.closed = False
        self.bridge_stopped = False
        self.window_icon: object | None = None
        self.discovery_logs: list[tuple[list[str], list[str]]] = []
        self.deferred_startup_scheduled = False
        _FakeMainWindow.last_instance = self

    def setWindowIcon(self, icon: object) -> None:
        self.window_icon = icon

    def start_bridge_and_wait_for_startup(self, *, timeout_s: float = 6.0) -> str | None:
        assert timeout_s == STARTUP_GATE_TIMEOUT_S
        assert self.bridge is not None
        return self.bridge.start_and_wait_for_startup(timeout_s=timeout_s)

    def show(self) -> None:
        self.shown = True

    def schedule_deferred_startup(self) -> None:
        self.deferred_startup_scheduled = True

    def close(self) -> None:
        self.closed = True

    def stop_bridge(self) -> None:
        self.bridge_stopped = True
        if self.bridge is not None:
            self.bridge.stop()

    def append_discovery_logs(self, *, timing_lines, error_lines) -> None:
        self.discovery_logs.append(([str(line) for line in timing_lines], [str(line) for line in error_lines]))


class _FakeBridge:
    last_instance: "_FakeBridge | None" = None
    preflight_message: str | None = None
    startup_message: str | None = None

    def __init__(self, _config: object, parent: object | None = None) -> None:
        self.parent = parent
        self.stopped = False
        self.preflight_timeout_s: float | None = None
        self.startup_timeout_s: float | None = None
        _FakeBridge.last_instance = self

    def wait_for_startup_preflight(self, *, timeout_s: float = 6.0) -> str | None:
        assert timeout_s == STARTUP_GATE_TIMEOUT_S
        self.preflight_timeout_s = float(timeout_s)
        return self.preflight_message

    def start_and_wait_for_startup(self, *, timeout_s: float = 6.0) -> str | None:
        assert timeout_s == STARTUP_GATE_TIMEOUT_S
        self.startup_timeout_s = float(timeout_s)
        return self.startup_message

    def stop(self) -> None:
        self.stopped = True


def _patch_program_dependencies(monkeypatch) -> list[tuple[object | None, str, str]]:
    warnings: list[tuple[object | None, str, str]] = []
    monkeypatch.setattr("qtpy.QtWidgets.QApplication", _FakeApp)
    monkeypatch.setattr(
        "qtpy.QtWidgets.QMessageBox.warning",
        lambda parent, title, message: warnings.append((parent, str(title), str(message))),
    )
    monkeypatch.setattr("f8pystudio.pystudio_program.PyStudioServiceBridge", _FakeBridge)
    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window.F8StudioMainWin", _FakeMainWindow)
    monkeypatch.setattr(PyStudioProgram, "_load_plugin_manifests", lambda self: [])
    monkeypatch.setattr(PyStudioProgram, "_apply_plugin_manifests_to_runtime_registry", lambda self, manifests, registry: None)
    monkeypatch.setattr(PyStudioProgram, "_apply_plugin_manifests_to_renderers", lambda self, manifests: None)
    monkeypatch.setattr(PyStudioProgram, "build_node_classes", lambda self: [])
    monkeypatch.setattr(PyStudioProgram, "_studio_icon_path", lambda self: None)
    monkeypatch.setattr("f8pystudio.pystudio_program.load_discovery_into_catalog", lambda **_kwargs: None)
    monkeypatch.setattr("f8pystudio.pystudio_program.last_discovery_timing_lines", lambda: ["timing-1"])
    monkeypatch.setattr("f8pystudio.pystudio_program.last_discovery_error_lines", lambda: ["error-1"])
    monkeypatch.setattr("f8pystudio.ui.support.qt_font_utils.normalize_application_font", lambda app: None)
    monkeypatch.setattr("f8pystudio.ui.support.webengine_utils.configure_default_webengine_profile", lambda: None)
    return warnings


def test_program_blocks_before_show_when_bridge_startup_is_blocked(monkeypatch, tmp_path) -> None:
    warnings = _patch_program_dependencies(monkeypatch)
    _FakeApp.last_instance = None
    _FakeMainWindow.last_instance = None
    _FakeBridge.preflight_message = "Another F8PyStudio instance is already running."
    _FakeBridge.startup_message = None
    dismiss_file = tmp_path / "launcher-dismiss.signal"
    monkeypatch.setenv("F8STUDIO_LAUNCH_DISMISS_FILE", str(dismiss_file))

    exit_code = PyStudioProgram().run()

    assert exit_code == 0
    assert warnings == [(None, SINGLETON_GUARD_DIALOG_TITLE, "Another F8PyStudio instance is already running.")]
    assert _FakeBridge.last_instance is not None
    assert _FakeBridge.last_instance.stopped is True
    assert _FakeMainWindow.last_instance is None
    assert dismiss_file.read_text(encoding="utf-8") == "dismiss\n"
    assert _FakeApp.last_instance is not None
    assert _FakeApp.last_instance.process_events_called is False


def test_program_shows_main_window_after_bridge_startup_passes(monkeypatch, tmp_path) -> None:
    warnings = _patch_program_dependencies(monkeypatch)
    _FakeApp.last_instance = None
    _FakeMainWindow.last_instance = None
    _FakeBridge.preflight_message = None
    _FakeBridge.startup_message = None
    ready_file = tmp_path / "launcher-ready.signal"
    monkeypatch.setenv("F8STUDIO_LAUNCH_READY_FILE", str(ready_file))

    exit_code = PyStudioProgram().run()

    assert exit_code == 0
    assert warnings == []
    assert _FakeMainWindow.last_instance is not None
    assert _FakeMainWindow.last_instance.bridge is _FakeBridge.last_instance
    assert _FakeMainWindow.last_instance.bridge_stopped is False
    assert _FakeMainWindow.last_instance.deferred_startup_scheduled is True
    assert _FakeMainWindow.last_instance.shown is True
    assert _FakeMainWindow.last_instance.closed is False
    assert _FakeMainWindow.last_instance.discovery_logs == [(["timing-1"], ["error-1"])]
    assert _FakeBridge.last_instance is not None
    assert _FakeBridge.last_instance.stopped is False
    assert ready_file.read_text(encoding="utf-8") == "ready\n"
    assert _FakeApp.last_instance is not None
    assert _FakeApp.last_instance.process_events_called is True
