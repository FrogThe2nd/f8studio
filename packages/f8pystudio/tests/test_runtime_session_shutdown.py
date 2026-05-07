from __future__ import annotations

import threading
import time
from typing import Any

from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge, PyStudioServiceBridgeConfig


class _BlockingZenohSession:
    def __init__(self, *, entered: threading.Event, release: threading.Event) -> None:
        self._entered = entered
        self._release = release

    def close(self) -> None:
        self._entered.set()
        self._release.wait(timeout=5.0)


class _FakeProcessGateway:
    def __init__(self) -> None:
        self.stop_calls: list[str] = []

    def service_ids(self) -> list[str]:
        return ["svc_detached"]

    def is_running(self, service_id: str) -> bool:
        return str(service_id) == "svc_detached"

    def start(self, req: Any) -> None:
        del req

    def stop(self, req: Any) -> Any:
        self.stop_calls.append(str(req.service_id))
        return True


def test_bridge_stop_does_not_wait_forever_for_blocking_zenoh_session_close() -> None:
    entered = threading.Event()
    release = threading.Event()
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())
    bridge._ensure_async_runtime_started()
    bridge._zenoh_singleton_session = _BlockingZenohSession(entered=entered, release=release)

    started = time.perf_counter()
    try:
        bridge.stop()
    finally:
        release.set()
    elapsed_s = time.perf_counter() - started

    assert entered.wait(timeout=0.5)
    assert elapsed_s < 2.0
    assert bridge._async_started is False


def test_bridge_stop_preserves_detached_processes() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig(supervision_mode="detached"))
    bridge._ensure_async_runtime_started()
    fake_gateway = _FakeProcessGateway()
    bridge._process_gateway = fake_gateway

    bridge.stop()

    assert fake_gateway.stop_calls == []
