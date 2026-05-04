from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from f8pystudio.bridge.nats_lifecycle import NatsSingletonGuardResult, SINGLETON_GUARD_DIALOG_MESSAGE
from f8pystudio.bridge.runtime_session_controller import RuntimeSessionControllerMixin


class _FakeConnectionManager:
    nats_url = "nats://127.0.0.1:4222"

    async def connect(self, *, context: str, allow_reconnect: bool = True):
        assert context == "connect nats for singleton guard failed"
        assert allow_reconnect is False
        return "connection"

    async def singleton_guard(self, connection, *, studio_service_id: str, ping_timeout_s: float):
        assert connection == "connection"
        assert studio_service_id == "studio"
        assert ping_timeout_s == 0.2
        return NatsSingletonGuardResult(should_start=False, connection=None)


class _Controller(RuntimeSessionControllerMixin):
    def __init__(self) -> None:
        self._nats_connection_manager = _FakeConnectionManager()
        self._owned_nats_server_pid = None
        self._nc = None
        self._svc = None
        self._remote_state_watcher = None
        self._remote_state_gateway = None
        self._monitor_sub = None
        self._watch_targets_cache = None
        self._managed_active = True
        self.logged: list[str] = []
        self.reported: list[str] = []
        self.studio_service_id = "studio"

    def _emit_log_line(self, line: str) -> None:
        self.logged.append(str(line))

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.reported.append(f"{context}:{type(exc).__name__}")

def test_runtime_session_returns_block_message_when_singleton_detected(monkeypatch) -> None:
    ensured_urls: list[str] = []

    async def _no_owned_pid(*_args, **_kwargs):
        if _args:
            ensured_urls.append(str(_args[0]))
        return None

    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.ensure_nats_server_owned_pid",
        _no_owned_pid,
    )

    controller = _Controller()

    result = asyncio.run(controller._start_async())

    assert controller._svc is None
    assert controller._nc is None
    assert ensured_urls == ["nats://127.0.0.1:4222"]
    assert result == SINGLETON_GUARD_DIALOG_MESSAGE


def test_zenoh_singleton_guard_allows_start_when_liveliness_query_drains(monkeypatch) -> None:
    class _FakeZError(Exception):
        pass

    class _FakeConfig:
        pass

    class _FakeReplies:
        def try_recv(self):
            raise _FakeZError("channel is empty and closed")

    class _FakeLiveliness:
        def __init__(self) -> None:
            self.token = object()

        def get(self, key: str, *, timeout: float):
            assert key == "f8/live/studio/studio"
            assert timeout == 0.2
            return _FakeReplies()

        def declare_token(self, key: str):
            assert key == "f8/live/studio/studio"
            return self.token

    class _FakeSession:
        def __init__(self) -> None:
            self.liveliness_api = _FakeLiveliness()
            self.closed = False

        def liveliness(self) -> _FakeLiveliness:
            return self.liveliness_api

        def close(self) -> None:
            self.closed = True

    fake_session = _FakeSession()
    fake_zenoh = SimpleNamespace(Config=_FakeConfig, ZError=_FakeZError, open=lambda _config: fake_session)
    monkeypatch.setitem(sys.modules, "zenoh", fake_zenoh)

    controller = _Controller()
    controller._cfg = SimpleNamespace(
        bus_backend="zenoh",
        nats_url="nats://127.0.0.1:4222",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )
    controller._zenoh_singleton_session = None
    controller._zenoh_singleton_token = None

    result = asyncio.run(controller._run_zenoh_startup_preflight_async())

    assert result is None
    assert controller.reported == []
    assert controller._zenoh_singleton_session is fake_session
    assert controller._zenoh_singleton_token is fake_session.liveliness_api.token
    assert fake_session.closed is False
