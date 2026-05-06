from __future__ import annotations

import threading
import time

from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge, PyStudioServiceBridgeConfig


class _BlockingZenohSession:
    def __init__(self, *, entered: threading.Event, release: threading.Event) -> None:
        self._entered = entered
        self._release = release

    def close(self) -> None:
        self._entered.set()
        self._release.wait(timeout=5.0)


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
