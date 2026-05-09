from __future__ import annotations

import threading
import time

from f8pysdk.zenoh_shutdown import close_zenoh_session_best_effort
from f8pysdk import zenoh_shutdown


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


class _PanicSession:
    def close(self) -> None:
        raise BaseException("native close panic")


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


def test_close_zenoh_session_best_effort_timeout_warning_is_deduped(caplog: object) -> None:
    import logging

    entered_a = threading.Event()
    entered_b = threading.Event()
    release = threading.Event()
    session_a = _BlockingSession(release=release, entered=entered_a)
    session_b = _BlockingSession(release=release, entered=entered_b)

    with caplog.at_level(logging.WARNING, logger="f8pysdk.zenoh_shutdown"):  # type: ignore[attr-defined]
        first_ok = close_zenoh_session_best_effort(session_a, context="test-dedupe-close", timeout_s=0.01)
        second_ok = close_zenoh_session_best_effort(session_b, context="test-dedupe-close", timeout_s=0.01)
    release.set()

    assert entered_a.wait(timeout=0.5)
    assert entered_b.wait(timeout=0.5)
    assert first_ok is False
    assert second_ok is False
    warnings = [
        record.message  # type: ignore[attr-defined]
        for record in caplog.records  # type: ignore[attr-defined]
        if "test-dedupe-close" in record.message  # type: ignore[attr-defined]
    ]
    assert len(warnings) == 1


def test_close_zenoh_session_best_effort_contains_base_exception() -> None:
    session = _PanicSession()

    ok = close_zenoh_session_best_effort(session, context="test-panic-close", timeout_s=0.5)

    assert ok is False


def test_close_zenoh_session_best_effort_can_skip_native_close() -> None:
    session = _CloseableSession()

    ok = close_zenoh_session_best_effort(session, context="test-skip-close", native_close=False)

    assert ok is True
    assert session.closed is False
    assert zenoh_shutdown._abandoned_sessions[-1] is session
