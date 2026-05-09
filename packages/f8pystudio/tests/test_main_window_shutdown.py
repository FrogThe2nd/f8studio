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

    def _teardown_graph_nodes_for_exit(self) -> None:
        self.events.append("graph-teardown")

    def stop_bridge(self) -> None:
        self.events.append("bridge")

    def _request_qt_app_quit(self) -> None:
        self.events.append("qt-quit")


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
