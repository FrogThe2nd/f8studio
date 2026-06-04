from __future__ import annotations

from f8pystudio.ui.mainwin.main_window import F8StudioMainWin


class _FakeHotkeyController:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def close(self) -> None:
        self._events.append("hotkeys")


class _ShutdownHarness:
    _run_shutdown_step = F8StudioMainWin._run_shutdown_step
    shutdown_for_app_exit = F8StudioMainWin.shutdown_for_app_exit

    def __init__(self) -> None:
        self.events: list[str] = []
        self._shutdown_started = False
        self._closing = False
        self._periodic_auto_save_timer = None
        self._auto_deploy_timer = None
        self._deferred_auto_deploy_fingerprint_timer = None
        self._studio_runtime_sync_timer = None
        self._asset_cloud_last_sync_timer = None
        self._subscription_sync_service = None
        self._global_hotkey_controller = _FakeHotkeyController(self.events)

    def _clear_asset_cache_changed_subscription(self) -> None:
        self.events.append("asset-cache")

    def _stop_shutdown_timer(self, _timer: object, *, context: str) -> None:
        self.events.append(f"timer:{context}")

    def _save_window_layout(self) -> None:
        self.events.append("save-layout")
        raise RuntimeError("layout failed")

    def _auto_save_project(self) -> None:
        self.events.append("auto-save")

    def _shutdown_ai_assist_sidebar(self) -> None:
        self.events.append("ai-sidebar")

    def _shutdown_agent_debug_widget(self) -> None:
        self.events.append("agent-debug")

    def _teardown_graph_nodes_for_exit(self) -> None:
        self.events.append("graph-teardown")

    def stop_bridge(self) -> None:
        self.events.append("bridge")

    def _request_qt_app_quit(self) -> None:
        self.events.append("qt-quit")


class _FakeLogDock:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def append(self, service_id: str, line: str) -> None:
        self.lines.append((service_id, line))


class _FailingLogDock:
    def append(self, _service_id: str, _line: str) -> None:
        raise RuntimeError("dock closed")


class _DiscoveryLogHarness:
    append_discovery_logs = F8StudioMainWin.append_discovery_logs

    def __init__(self, log_dock: object) -> None:
        self._log_dock = log_dock


class _StopBridgeHarness:
    stop_bridge = F8StudioMainWin.stop_bridge

    class _Bridge:
        def __init__(self) -> None:
            self.stop_calls = 0

        def stop(self) -> None:
            self.stop_calls += 1
            raise RuntimeError("bridge stop failed")

    class _LogDock:
        def __init__(self) -> None:
            self.reported: list[tuple[str, str, str]] = []

        def report_exception(self, channel: str, context: str, exc: Exception) -> None:
            self.reported.append((str(channel), str(context), str(exc)))

    def __init__(self) -> None:
        self._bridge_stopped = False
        self._bridge = self._Bridge()
        self._log_dock = self._LogDock()


def test_shutdown_for_app_exit_continues_after_step_failure() -> None:
    host = _ShutdownHarness()

    host.shutdown_for_app_exit()

    assert host._closing is True
    assert host.events == [
        "asset-cache",
        "timer:periodic-auto-save",
        "timer:auto-deploy",
        "timer:auto-deploy-fingerprint",
        "timer:studio-runtime-sync",
        "timer:asset-cloud-last-sync",
        "save-layout",
        "auto-save",
        "hotkeys",
        "ai-sidebar",
        "agent-debug",
        "graph-teardown",
        "bridge",
        "qt-quit",
    ]


def test_shutdown_for_app_exit_is_idempotent() -> None:
    host = _ShutdownHarness()

    host.shutdown_for_app_exit()
    first_events = list(host.events)
    host.shutdown_for_app_exit()

    assert host.events == first_events


def test_append_discovery_logs_emits_timing_and_error_lines() -> None:
    log_dock = _FakeLogDock()
    host = _DiscoveryLogHarness(log_dock)

    host.append_discovery_logs(timing_lines=["timing\n"], error_lines=["error\n"])

    assert log_dock.lines == [
        ("studio", "timing\n"),
        ("studio", "error\n"),
    ]


def test_append_discovery_logs_skips_duplicate_error_summary() -> None:
    log_dock = _FakeLogDock()
    host = _DiscoveryLogHarness(log_dock)

    host.append_discovery_logs(
        timing_lines=["discovery took 0.1s\n", "discovery errors: 2\n"],
        error_lines=["detail should already be included\n"],
    )

    assert log_dock.lines == [
        ("studio", "discovery took 0.1s\n"),
        ("studio", "discovery errors: 2\n"),
    ]


def test_append_discovery_logs_reports_log_dock_failures(caplog) -> None:
    host = _DiscoveryLogHarness(_FailingLogDock())

    host.append_discovery_logs(timing_lines=["timing\n"], error_lines=[])

    assert "Failed to emit discovery logs to studio log dock" in caplog.text


def test_stop_bridge_reports_runtime_failures_without_marking_stopped() -> None:
    host = _StopBridgeHarness()

    host.stop_bridge()

    assert host._bridge.stop_calls == 1
    assert host._bridge_stopped is False
    assert host._log_dock.reported == [("studio", "bridge.stop failed", "bridge stop failed")]
