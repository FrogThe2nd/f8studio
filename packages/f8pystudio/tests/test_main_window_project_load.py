from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.assets.projects.project_models import F8ProjectRecord  # noqa: E402
from f8pystudio.ui.mainwin.main_window import F8StudioMainWin  # noqa: E402


class _FakeSignal:
    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    created: list["_FakeWorker"] = []

    def __init__(self) -> None:
        self.loaded = _FakeSignal()
        self.failed = _FakeSignal()
        self.started = False
        _FakeWorker.created.append(self)

    def start(self) -> None:
        self.started = True


class _FakeStudioGraph:
    def __init__(self) -> None:
        self.loaded_payloads: list[object] = []

    def load_session_payload(self, payload: object) -> None:
        self.loaded_payloads.append(payload)


class _FakeLogDock:
    def __init__(self) -> None:
        self.exceptions: list[tuple[str, str, str]] = []
        self.lines: list[tuple[str, str]] = []

    def report_exception(self, channel: str, context: str, exc: Exception) -> None:
        self.exceptions.append((str(channel), str(context), str(exc)))

    def append(self, channel: str, line: str) -> None:
        self.lines.append((str(channel), str(line)))


class _FakeHotkeyController:
    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_bindings(self) -> None:
        self.refresh_calls += 1


class _FakeMain:
    _auto_load_project = F8StudioMainWin._auto_load_project
    _finalize_auto_load_project = F8StudioMainWin._finalize_auto_load_project
    _on_auto_load_project_loaded = F8StudioMainWin._on_auto_load_project_loaded
    _on_auto_load_project_failed = F8StudioMainWin._on_auto_load_project_failed
    _on_deferred_auto_deploy_fingerprint_timeout = F8StudioMainWin._on_deferred_auto_deploy_fingerprint_timeout

    def __init__(self) -> None:
        self._auto_load_worker: _FakeWorker | None = None
        self._closing = False
        self.studio_graph = _FakeStudioGraph()
        self._log_dock = _FakeLogDock()
        self._global_hotkey_controller = _FakeHotkeyController()
        self._last_auto_deploy_fingerprint = "old"
        self.mark_session_saved_calls = 0
        self.mark_auto_deploy_observed_calls = 0
        self.schedule_deferred_auto_deploy_fingerprint_refresh_calls = 0
        self.refresh_auto_deploy_fingerprint_calls = 0

    def _mark_session_saved(self) -> None:
        self.mark_session_saved_calls += 1

    def _mark_auto_deploy_observed(self) -> None:
        self.mark_auto_deploy_observed_calls += 1

    def _schedule_deferred_auto_deploy_fingerprint_refresh(self) -> None:
        self.schedule_deferred_auto_deploy_fingerprint_refresh_calls += 1

    def _refresh_auto_deploy_fingerprint(self) -> None:
        self.refresh_auto_deploy_fingerprint_calls += 1


class _FakePrepareBeforeShowMain:
    def __init__(self) -> None:
        self.ensure_ai_assist_calls = 0
        self.reported_exceptions: list[tuple[str, str]] = []

    def _ensure_ai_assist_sidebar(self) -> None:
        self.ensure_ai_assist_calls += 1


class _FakePrepareBeforeShowFailureMain(_FakePrepareBeforeShowMain):
    def _ensure_ai_assist_sidebar(self) -> None:
        raise RuntimeError("boom")

    class _LogDock:
        def __init__(self, owner: "_FakePrepareBeforeShowFailureMain") -> None:
            self._owner = owner

        def report_exception(self, channel: str, context: str, exc: Exception) -> None:
            self._owner.reported_exceptions.append((str(channel), f"{context}:{exc}"))

    def __init__(self) -> None:
        super().__init__()
        self._log_dock = self._LogDock(self)


def test_auto_load_project_starts_background_worker_once(monkeypatch) -> None:
    _FakeWorker.created = []
    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window._ProjectAutoLoadWorker", _FakeWorker)
    fake_main = _FakeMain()

    F8StudioMainWin._auto_load_project(fake_main)
    F8StudioMainWin._auto_load_project(fake_main)

    assert len(_FakeWorker.created) == 1
    assert fake_main._auto_load_worker is _FakeWorker.created[0]
    assert _FakeWorker.created[0].started is True


def test_auto_load_project_loaded_applies_payload_and_finalizes() -> None:
    fake_main = _FakeMain()
    fake_main._auto_load_worker = _FakeWorker()
    record = F8ProjectRecord(projectId="proj-1", name="Test", content={"layout": {"nodes": {}}})

    F8StudioMainWin._on_auto_load_project_loaded(fake_main, record)

    assert fake_main._auto_load_worker is None
    assert fake_main.studio_graph.loaded_payloads == [{"layout": {"nodes": {}}}]
    assert fake_main.mark_session_saved_calls == 1
    assert fake_main.mark_auto_deploy_observed_calls == 1
    assert fake_main.schedule_deferred_auto_deploy_fingerprint_refresh_calls == 1
    assert fake_main._last_auto_deploy_fingerprint == ""
    assert fake_main._global_hotkey_controller.refresh_calls == 1
    assert fake_main._log_dock.exceptions == []


def test_auto_load_project_failure_reports_and_finalizes() -> None:
    fake_main = _FakeMain()
    fake_main._auto_load_worker = _FakeWorker()

    F8StudioMainWin._on_auto_load_project_failed(fake_main, "session auto-load failed", RuntimeError("boom"))

    assert fake_main._auto_load_worker is None
    assert fake_main.studio_graph.loaded_payloads == []
    assert fake_main.mark_session_saved_calls == 1
    assert fake_main.mark_auto_deploy_observed_calls == 1
    assert fake_main.schedule_deferred_auto_deploy_fingerprint_refresh_calls == 1
    assert fake_main._last_auto_deploy_fingerprint == ""
    assert fake_main._global_hotkey_controller.refresh_calls == 1
    assert fake_main._log_dock.exceptions == [("studio", "session auto-load failed", "boom")]


def test_deferred_auto_deploy_fingerprint_refresh_runs_only_when_open() -> None:
    fake_main = _FakeMain()

    F8StudioMainWin._on_deferred_auto_deploy_fingerprint_timeout(fake_main)
    assert fake_main.refresh_auto_deploy_fingerprint_calls == 1

    fake_main._closing = True
    F8StudioMainWin._on_deferred_auto_deploy_fingerprint_timeout(fake_main)
    assert fake_main.refresh_auto_deploy_fingerprint_calls == 1


def test_prepare_before_show_initializes_ai_assist() -> None:
    fake_main = _FakePrepareBeforeShowMain()

    F8StudioMainWin.prepare_before_show(fake_main)

    assert fake_main.ensure_ai_assist_calls == 1


def test_prepare_before_show_reports_failures() -> None:
    fake_main = _FakePrepareBeforeShowFailureMain()

    F8StudioMainWin.prepare_before_show(fake_main)

    assert fake_main.reported_exceptions == [("studio", "prepare AI Assist before show failed:boom")]
