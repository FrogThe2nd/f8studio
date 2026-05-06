from __future__ import annotations

import threading
import time

from f8pysdk.zenoh_shutdown import close_zenoh_session_best_effort


class _CloseableSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _BlockingSession:
    def __init__(self, *, release: threading.Event, entered: threading.Event) -> None:
        self.release = release
        self.entered = entered

    def close(self) -> None:
        self.entered.set()
        self.release.wait(timeout=5.0)


def test_close_zenoh_session_best_effort_closes_session() -> None:
    session = _CloseableSession()

    ok = close_zenoh_session_best_effort(session, context="test-close", timeout_s=0.5)

    assert ok is True
    assert session.closed is True


def test_close_zenoh_session_best_effort_timeout_does_not_wait_for_blocking_close() -> None:
    entered = threading.Event()
    release = threading.Event()
    session = _BlockingSession(release=release, entered=entered)

    started = time.perf_counter()
    ok = close_zenoh_session_best_effort(session, context="test-blocking-close", timeout_s=0.01)
    elapsed_s = time.perf_counter() - started
    release.set()

    assert entered.wait(timeout=0.5)
    assert ok is False
    assert elapsed_s < 0.5
