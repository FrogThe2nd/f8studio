from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from f8pystudio.bridge.nats_lifecycle import NatsSingletonGuardResult, SINGLETON_GUARD_DIALOG_MESSAGE
from f8pystudio.bridge.runtime_session_controller import (
    RuntimeSessionControllerMixin,
    _service_id_from_zenoh_liveliness_key,
)


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


class _FakeMonitorCenter:
    def __init__(self) -> None:
        self.ready_updates: list[tuple[str, bool]] = []

    def update_service_status(self, *, service_id: str, ready: bool) -> None:
        self.ready_updates.append((str(service_id), bool(ready)))


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
        self._zenoh_service_liveliness_session = None
        self._zenoh_service_liveliness_sub = None
        self._managed_active = True
        self.logged: list[str] = []
        self.reported: list[str] = []
        self.alive_updates: list[tuple[str, bool]] = []
        self.active_updates: list[tuple[str, bool | None]] = []
        self.status_requests: list[str] = []
        self._monitor_center = _FakeMonitorCenter()
        self.studio_service_id = "studio"

    def _emit_log_line(self, line: str) -> None:
        self.logged.append(str(line))

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.reported.append(f"{context}:{type(exc).__name__}")

    def _cache_service_alive(self, service_id: str, alive: bool) -> None:
        self.alive_updates.append((str(service_id), bool(alive)))

    def _cache_service_active(self, service_id: str, active: bool | None) -> None:
        self.active_updates.append((str(service_id), active))

    def request_service_status(self, service_id: str) -> None:
        self.status_requests.append(str(service_id))


def test_zenoh_liveliness_key_extracts_service_id() -> None:
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/engine") == "engine"
    assert _service_id_from_zenoh_liveliness_key("/f8/live/svc/detector/") == "detector"
    assert _service_id_from_zenoh_liveliness_key("f8/live/studio/studio") is None
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/") is None
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/bad/path") is None


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
    controller._cfg = SimpleNamespace(bus_backend="nats", nats_url="nats://127.0.0.1:4222")

    result = asyncio.run(controller._start_async())

    assert controller._svc is None
    assert controller._nc is None
    assert ensured_urls == ["nats://127.0.0.1:4222"]
    assert result == SINGLETON_GUARD_DIALOG_MESSAGE


def test_runtime_session_defaults_to_zenoh_backend() -> None:
    controller = _Controller()

    assert controller._runtime_bus_backend() == "zenoh"


def test_runtime_session_mem_preflight_skips_zenoh(monkeypatch) -> None:
    class _FakeZenoh:
        @staticmethod
        def open(_config):
            raise AssertionError("mem preflight must not open zenoh")

    monkeypatch.setitem(sys.modules, "zenoh", _FakeZenoh)
    controller = _Controller()
    controller._cfg = SimpleNamespace(bus_backend="mem", nats_url="nats://127.0.0.1:4222")

    result = asyncio.run(controller._run_startup_preflight_async())

    assert result is None
    assert controller.reported == []


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


def test_zenoh_service_liveliness_watch_updates_alive_cache(monkeypatch) -> None:
    class _FakeSampleKind:
        PUT = "put"
        DELETE = "delete"

    class _FakeConfig:
        pass

    class _FakeSample:
        def __init__(self, key_expr: str, kind: str) -> None:
            self.key_expr = key_expr
            self.kind = kind

    class _FakeSubscriber:
        def __init__(self) -> None:
            self.undeclared = False

        def undeclare(self) -> None:
            self.undeclared = True

    class _FakeLiveliness:
        def __init__(self) -> None:
            self.subscriber = _FakeSubscriber()
            self.callback = None

        def declare_subscriber(self, key_expr: str, callback, *, history: bool):
            assert key_expr == "f8/live/svc/**"
            assert history is True
            self.callback = callback
            return self.subscriber

    class _FakeSession:
        def __init__(self) -> None:
            self.liveliness_api = _FakeLiveliness()

        def liveliness(self) -> _FakeLiveliness:
            return self.liveliness_api

    fake_session = _FakeSession()
    fake_zenoh = SimpleNamespace(Config=_FakeConfig, SampleKind=_FakeSampleKind, open=lambda _config: fake_session)
    monkeypatch.setitem(sys.modules, "zenoh", fake_zenoh)

    async def _run() -> _Controller:
        controller = _Controller()
        controller._cfg = SimpleNamespace(
            bus_backend="zenoh",
            nats_url="nats://127.0.0.1:4222",
            zenoh_config_path=None,
            zenoh_connect=(),
            zenoh_listen=(),
            zenoh_shm_pool_bytes=256 * 1024 * 1024,
        )
        controller._zenoh_singleton_session = fake_session
        await controller._start_zenoh_service_liveliness_watch_async()

        callback = fake_session.liveliness_api.callback
        assert callback is not None
        callback(_FakeSample("f8/live/svc/engine", _FakeSampleKind.PUT))
        await asyncio.sleep(0)
        callback(_FakeSample("f8/live/svc/engine", _FakeSampleKind.DELETE))
        await asyncio.sleep(0)
        return controller

    controller = asyncio.run(_run())

    assert controller._zenoh_service_liveliness_sub is fake_session.liveliness_api.subscriber
    assert controller.alive_updates == [("engine", True), ("engine", False)]
    assert controller.status_requests == ["engine"]
    assert controller.active_updates == [("engine", None)]
    assert controller._monitor_center.ready_updates == [("engine", False)]
    assert controller.reported == []
