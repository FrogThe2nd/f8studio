from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from f8pystudio.bridge.runtime_lifecycle import SINGLETON_GUARD_DIALOG_MESSAGE
from f8pysdk import zenoh_shutdown
from f8pystudio.bridge.runtime_session_controller import (
    ServiceLivelinessIdentity,
    RuntimeSessionControllerMixin,
    _service_liveliness_identity_from_zenoh_key,
    _service_id_from_zenoh_liveliness_key,
)


class _FakeMonitorCenter:
    def __init__(self) -> None:
        self.ready_updates: list[tuple[str, bool]] = []

    def update_service_status(self, *, service_id: str, ready: bool) -> None:
        self.ready_updates.append((str(service_id), bool(ready)))


class _FakeStudioService:
    instances: list["_FakeStudioService"] = []
    start_delay_s = 0.0
    fail_start = False

    def __init__(self, cfg: object, *, registry: object) -> None:
        self.cfg = cfg
        self.registry = registry
        self.bus = object()
        self.started = False
        self.stopped = False
        self.on_ui_command = None
        self.__class__.instances.append(self)

    async def start(self, *, on_ui_command: object) -> None:
        if self.start_delay_s > 0:
            await asyncio.sleep(float(self.start_delay_s))
        if self.fail_start:
            raise RuntimeError("start failed")
        self.on_ui_command = on_ui_command
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _Controller(RuntimeSessionControllerMixin):
    def __init__(self) -> None:
        self._svc = None
        self._studio_service_lock = None
        self._studio_service_lock_loop = None
        self._remote_state_watcher = None
        self._remote_state_gateway = None
        self._monitor_sub = None
        self._watch_targets_cache = None
        self._last_compiled = None
        self.refresh_calls: list[object] = []
        self.set_active_calls: list[bool] = []
        self._zenoh_service_liveliness_session = None
        self._zenoh_service_liveliness_sub = None
        self._service_liveliness_instances_by_service: dict[str, set[str]] = {}
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

    async def _refresh_studio_runtime_async(self, *, compiled: object | None = None) -> None:
        self.refresh_calls.append(compiled)

    async def _set_managed_active_async(self, active: bool) -> bool:
        self.set_active_calls.append(bool(active))
        return True


def test_ensure_studio_runtime_starts_missing_builtin_service(monkeypatch) -> None:
    _FakeStudioService.instances.clear()
    _FakeStudioService.start_delay_s = 0.0
    _FakeStudioService.fail_start = False
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.PyStudioService",
        _FakeStudioService,
    )
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.shared_pystudio_registry",
        lambda: object(),
    )

    controller = _Controller()
    controller._cfg = SimpleNamespace(
        bus_backend="mem",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )

    ok = asyncio.run(controller._ensure_studio_runtime_async(timeout_s=0.0))

    assert ok is True
    assert len(_FakeStudioService.instances) == 1
    assert _FakeStudioService.instances[0].started is True
    assert controller._svc is _FakeStudioService.instances[0]
    assert controller.alive_updates == [("studio", True)]
    assert controller.active_updates == [("studio", True)]
    assert controller._monitor_center.ready_updates == [("studio", True)]
    assert controller.logged == []


def test_ensure_studio_runtime_serializes_concurrent_builtin_service_start(monkeypatch) -> None:
    _FakeStudioService.instances.clear()
    _FakeStudioService.start_delay_s = 0.02
    _FakeStudioService.fail_start = False
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.PyStudioService",
        _FakeStudioService,
    )
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.shared_pystudio_registry",
        lambda: object(),
    )

    controller = _Controller()
    controller._cfg = SimpleNamespace(
        bus_backend="mem",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )

    async def _run() -> tuple[bool, bool]:
        first, second = await asyncio.gather(
            controller._ensure_studio_runtime_async(timeout_s=0.0),
            controller._ensure_studio_runtime_async(timeout_s=0.0),
        )
        return bool(first), bool(second)

    try:
        assert asyncio.run(_run()) == (True, True)
    finally:
        _FakeStudioService.start_delay_s = 0.0

    assert len(_FakeStudioService.instances) == 1
    assert _FakeStudioService.instances[0].started is True
    assert controller.alive_updates == [("studio", True)]
    assert controller.active_updates == [("studio", True)]
    assert controller._monitor_center.ready_updates == [("studio", True)]


def test_studio_service_lock_recreates_for_new_event_loop() -> None:
    controller = _Controller()

    async def _capture_lock() -> tuple[asyncio.Lock, asyncio.AbstractEventLoop | None]:
        lock = controller._studio_service_lock_for_loop()
        return lock, controller._studio_service_lock_loop

    first_lock, first_loop = asyncio.run(_capture_lock())
    second_lock, second_loop = asyncio.run(_capture_lock())

    assert first_loop is not None
    assert second_loop is not None
    assert first_loop is not second_loop
    assert first_lock is not second_lock
    assert controller._studio_service_lock is second_lock
    assert controller._studio_service_lock_loop is second_loop


def test_ensure_studio_runtime_cleans_up_failed_builtin_service_start(monkeypatch) -> None:
    _FakeStudioService.instances.clear()
    _FakeStudioService.start_delay_s = 0.0
    _FakeStudioService.fail_start = True
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.PyStudioService",
        _FakeStudioService,
    )
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.shared_pystudio_registry",
        lambda: object(),
    )

    controller = _Controller()
    controller._cfg = SimpleNamespace(
        bus_backend="mem",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )

    try:
        ok = asyncio.run(controller._ensure_studio_runtime_async(timeout_s=0.0))
    finally:
        _FakeStudioService.fail_start = False

    assert ok is False
    assert len(_FakeStudioService.instances) == 1
    assert _FakeStudioService.instances[0].stopped is True
    assert controller._svc is None
    assert controller.alive_updates == [("studio", False)]
    assert controller.active_updates == [("studio", None)]
    assert controller._monitor_center.ready_updates == [("studio", False)]
    assert any("studio runtime start failed" in line for line in controller.logged)


def test_start_after_preflight_refreshes_last_compiled_studio_runtime(monkeypatch) -> None:
    _FakeStudioService.instances.clear()
    _FakeStudioService.start_delay_s = 0.0
    _FakeStudioService.fail_start = False
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.PyStudioService",
        _FakeStudioService,
    )
    monkeypatch.setattr(
        "f8pystudio.bridge.runtime_session_controller.shared_pystudio_registry",
        lambda: object(),
    )

    controller = _Controller()
    compiled = object()
    controller._last_compiled = compiled
    controller._cfg = SimpleNamespace(
        bus_backend="mem",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )

    result = asyncio.run(controller._start_after_preflight_async())

    assert result is None
    assert controller.refresh_calls == [compiled]
    assert controller.set_active_calls == [True]
    assert len(_FakeStudioService.instances) == 1


def test_zenoh_liveliness_key_extracts_service_id() -> None:
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/engine/instances/inst1") == "engine"
    assert _service_id_from_zenoh_liveliness_key("/f8/live/svc/detector/instances/inst2/") == "detector"
    assert _service_id_from_zenoh_liveliness_key("f8/live/studio/studio") is None
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/") is None
    assert _service_id_from_zenoh_liveliness_key("f8/live/svc/bad/path") is None


def test_zenoh_liveliness_key_extracts_instance_identity() -> None:
    assert _service_liveliness_identity_from_zenoh_key("f8/live/svc/engine/instances/inst1") == (
        ServiceLivelinessIdentity(service_id="engine", runtime_instance_id="inst1")
    )
    assert _service_liveliness_identity_from_zenoh_key("f8/live/svc/engine") is None


def test_runtime_session_returns_block_message_when_singleton_detected(monkeypatch) -> None:
    class _FakeConfig:
        pass

    class _FakeReplies:
        def __init__(self) -> None:
            self._delivered = False

        def try_recv(self):
            if self._delivered:
                return None
            self._delivered = True
            return SimpleNamespace(ok=object())

    class _FakeLiveliness:
        def get(self, key: str, *, timeout: float):
            assert key == "f8/live/studio/studio"
            assert timeout == 0.2
            return _FakeReplies()

    class _FakeSession:
        def __init__(self) -> None:
            self.liveliness_api = _FakeLiveliness()
            self.closed = False

        def liveliness(self) -> _FakeLiveliness:
            return self.liveliness_api

        def close(self) -> None:
            self.closed = True

    fake_session = _FakeSession()
    fake_zenoh = SimpleNamespace(Config=_FakeConfig, open=lambda _config: fake_session)
    monkeypatch.setitem(sys.modules, "zenoh", fake_zenoh)

    controller = _Controller()
    controller._cfg = SimpleNamespace(
        bus_backend="zenoh",
        zenoh_config_path=None,
        zenoh_connect=(),
        zenoh_listen=(),
        zenoh_shm_pool_bytes=256 * 1024 * 1024,
    )

    result = asyncio.run(controller._run_startup_preflight_async())

    assert controller._svc is None
    assert result == SINGLETON_GUARD_DIALOG_MESSAGE
    assert fake_session.closed is False
    assert zenoh_shutdown._abandoned_sessions[-1] is fake_session


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
    controller._cfg = SimpleNamespace(bus_backend="mem")

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
            zenoh_config_path=None,
            zenoh_connect=(),
            zenoh_listen=(),
            zenoh_shm_pool_bytes=256 * 1024 * 1024,
        )
        controller._zenoh_singleton_session = fake_session
        await controller._start_zenoh_service_liveliness_watch_async()

        callback = fake_session.liveliness_api.callback
        assert callback is not None
        callback(_FakeSample("f8/live/svc/engine/instances/inst1", _FakeSampleKind.PUT))
        await asyncio.sleep(0)
        callback(_FakeSample("f8/live/svc/engine/instances/inst1", _FakeSampleKind.DELETE))
        await asyncio.sleep(0)
        return controller

    controller = asyncio.run(_run())

    assert controller._zenoh_service_liveliness_sub is fake_session.liveliness_api.subscriber
    assert controller.alive_updates == [("engine", True), ("engine", False)]
    assert controller.status_requests == ["engine"]
    assert controller.active_updates == [("engine", None)]
    assert controller._monitor_center.ready_updates == [("engine", False)]
    assert controller.reported == []


def test_zenoh_service_liveliness_query_clears_stale_cache(monkeypatch) -> None:
    class _FakeZError(Exception):
        pass

    class _FakeReplies:
        def try_recv(self):
            raise _FakeZError("channel is empty and closed")

    class _FakeLiveliness:
        def get(self, key_expr: str, *, timeout: float):
            assert key_expr == "f8/live/svc/engine/instances/**"
            assert timeout == 0.25
            return _FakeReplies()

    class _FakeSession:
        def __init__(self) -> None:
            self.liveliness_api = _FakeLiveliness()

        def liveliness(self) -> _FakeLiveliness:
            return self.liveliness_api

    fake_session = _FakeSession()
    fake_zenoh = SimpleNamespace(ZError=_FakeZError)
    monkeypatch.setitem(sys.modules, "zenoh", fake_zenoh)

    async def _run() -> _Controller:
        controller = _Controller()
        controller._cfg = SimpleNamespace(bus_backend="zenoh")
        controller._zenoh_singleton_session = fake_session
        controller._service_liveliness_instances_by_service["engine"] = {"stale_inst"}
        instances = await controller._query_zenoh_service_liveliness_instances_async("engine")
        assert instances == set()
        return controller

    controller = asyncio.run(_run())

    assert "engine" not in controller._service_liveliness_instances_by_service
    assert controller.reported == []


def test_zenoh_service_liveliness_queries_do_not_block_event_loop(monkeypatch) -> None:
    class _FakeZError(Exception):
        pass

    class _FakeReplies:
        def try_recv(self):
            time.sleep(0.08)
            raise _FakeZError("channel is empty and closed")

    class _FakeLiveliness:
        def get(self, key_expr: str, *, timeout: float):
            assert key_expr.startswith("f8/live/svc/")
            assert timeout == 0.25
            return _FakeReplies()

    class _FakeSession:
        def __init__(self) -> None:
            self.liveliness_api = _FakeLiveliness()

        def liveliness(self) -> _FakeLiveliness:
            return self.liveliness_api

    fake_session = _FakeSession()
    fake_zenoh = SimpleNamespace(ZError=_FakeZError)
    monkeypatch.setitem(sys.modules, "zenoh", fake_zenoh)

    async def _run() -> float:
        controller = _Controller()
        controller._cfg = SimpleNamespace(bus_backend="zenoh")
        controller._zenoh_singleton_session = fake_session

        async def _tick() -> float:
            started = time.perf_counter()
            await asyncio.sleep(0.005)
            return time.perf_counter() - started

        query_task = asyncio.create_task(controller._query_zenoh_service_liveliness_instances_async("engine"))
        tick_task = asyncio.create_task(_tick())
        tick_elapsed_s = await tick_task
        await query_task
        return tick_elapsed_s

    tick_elapsed_s = asyncio.run(_run())

    assert tick_elapsed_s < 0.05
