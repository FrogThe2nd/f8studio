from __future__ import annotations

import asyncio
from typing import Any, Callable

from f8pystudio.bridge.nats_lifecycle import (
    RuntimeConnectionManager,
    ensure_nats_server_owned_pid,
    stop_owned_nats_server,
)


class _FakeConnection:
    def __init__(self, *, request_exc: Exception | None = None) -> None:
        self._request_exc = request_exc
        self.closed = False
        self.requests: list[tuple[str, bytes, float]] = []

    async def request(self, subject: str, payload: bytes, timeout: float) -> bytes:
        self.requests.append((str(subject), bytes(payload), float(timeout)))
        if self._request_exc is not None:
            raise self._request_exc
        return b"ok"

    async def close(self) -> None:
        self.closed = True


def test_connect_failure_reports_context() -> None:
    reported: list[str] = []
    reconnect_flags: list[bool] = []

    async def _connect(
        _nats_url: str,
        _error_cb: Callable[[Exception], Any],
        _timeout_s: float,
        allow_reconnect: bool,
    ) -> Any:
        reconnect_flags.append(bool(allow_reconnect))
        raise RuntimeError("connect-failed")

    manager = RuntimeConnectionManager(
        nats_url="nats://127.0.0.1:4222",
        emit_log=lambda _line: None,
        report_exception=lambda context, exc: reported.append(f"{context}:{type(exc).__name__}"),
        _connect_func=_connect,
    )

    nc = asyncio.run(manager.connect(context="ensure nats connection failed"))

    assert nc is None
    assert reported == ["ensure nats connection failed:RuntimeError"]
    assert reconnect_flags == [True]


def test_connect_error_callback_throttles_logs() -> None:
    logs: list[str] = []
    callbacks: list[Callable[[Exception], Any]] = []
    reconnect_flags: list[bool] = []
    fake_connection = _FakeConnection()

    async def _connect(
        _nats_url: str,
        error_cb: Callable[[Exception], Any],
        _timeout_s: float,
        allow_reconnect: bool,
    ) -> Any:
        callbacks.append(error_cb)
        reconnect_flags.append(bool(allow_reconnect))
        return fake_connection

    manager = RuntimeConnectionManager(
        nats_url="nats://127.0.0.1:4222",
        emit_log=lambda line: logs.append(str(line)),
        report_exception=lambda _context, _exc: None,
        error_log_interval_s=30.0,
        _connect_func=_connect,
    )

    nc = asyncio.run(manager.connect(context="connect nats failed"))
    assert nc is fake_connection
    assert len(callbacks) == 1

    asyncio.run(callbacks[0](RuntimeError("first")))
    asyncio.run(callbacks[0](RuntimeError("second")))
    assert reconnect_flags == [True]
    assert len(logs) == 1
    assert "NATS not reachable" in logs[0]
    assert "(will retry)" in logs[0]


def test_connect_error_callback_without_reconnect_omits_retry_suffix() -> None:
    logs: list[str] = []
    callbacks: list[Callable[[Exception], Any]] = []
    reconnect_flags: list[bool] = []
    fake_connection = _FakeConnection()

    async def _connect(
        _nats_url: str,
        error_cb: Callable[[Exception], Any],
        _timeout_s: float,
        allow_reconnect: bool,
    ) -> Any:
        callbacks.append(error_cb)
        reconnect_flags.append(bool(allow_reconnect))
        return fake_connection

    manager = RuntimeConnectionManager(
        nats_url="nats://127.0.0.1:4222",
        emit_log=lambda line: logs.append(str(line)),
        report_exception=lambda _context, _exc: None,
        _connect_func=_connect,
    )

    nc = asyncio.run(manager.connect(context="connect nats for singleton guard failed", allow_reconnect=False))
    assert nc is fake_connection
    assert reconnect_flags == [False]

    asyncio.run(callbacks[0](RuntimeError("offline")))
    assert logs == ["NATS not reachable at 'nats://127.0.0.1:4222': RuntimeError: offline"]


def test_singleton_guard_blocks_start_when_ping_responds() -> None:
    fake_connection = _FakeConnection()
    logs: list[str] = []

    manager = RuntimeConnectionManager(
        nats_url="nats://127.0.0.1:4222",
        emit_log=lambda line: logs.append(str(line)),
        report_exception=lambda _context, _exc: None,
    )

    outcome = asyncio.run(
        manager.singleton_guard(
            fake_connection,
            studio_service_id="studio",
            ping_timeout_s=0.2,
        )
    )

    assert outcome.should_start is False
    assert outcome.connection is None
    assert fake_connection.closed is True
    assert logs == ["Another PyStudio instance is already running (micro service ping responded)."]


def test_singleton_guard_timeout_allows_start_without_error_report() -> None:
    fake_connection = _FakeConnection(request_exc=TimeoutError("no responder"))
    reports: list[str] = []

    manager = RuntimeConnectionManager(
        nats_url="nats://127.0.0.1:4222",
        emit_log=lambda _line: None,
        report_exception=lambda context, exc: reports.append(f"{context}:{type(exc).__name__}"),
    )

    outcome = asyncio.run(
        manager.singleton_guard(
            fake_connection,
            studio_service_id="studio",
            ping_timeout_s=0.2,
        )
    )

    assert outcome.should_start is True
    assert outcome.connection is fake_connection
    assert reports == []


def test_nats_server_bootstrap_and_stop_helpers() -> None:
    bootstrap_calls: list[str] = []
    stop_calls: list[int] = []

    class _BootstrapResult:
        started_by_current_process = True
        started_pid = 4321

    def _bootstrap(nats_url: str, *, log_cb: Callable[[str], None]) -> Any:
        _ = log_cb
        bootstrap_calls.append(str(nats_url))
        return _BootstrapResult()

    def _stop(pid: int, *, log_cb: Callable[[str], None]) -> bool:
        _ = log_cb
        stop_calls.append(int(pid))
        return True

    pid = asyncio.run(
        ensure_nats_server_owned_pid(
            "nats://127.0.0.1:4222",
            emit_log=lambda _line: None,
            report_exception=lambda _context, _exc: None,
            bootstrap_func=_bootstrap,
        )
    )
    assert pid == 4321
    assert bootstrap_calls == ["nats://127.0.0.1:4222"]

    stopped = asyncio.run(
        stop_owned_nats_server(
            4321,
            emit_log=lambda _line: None,
            report_exception=lambda _context, _exc: None,
            stop_func=_stop,
        )
    )
    assert stopped is True
    assert stop_calls == [4321]
