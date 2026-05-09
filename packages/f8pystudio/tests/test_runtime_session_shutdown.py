from __future__ import annotations

import threading
import time
from types import SimpleNamespace
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
        self.terminate_external_calls: list[str] = []
        self.running_by_service: dict[str, bool] = {"svc_detached": True}

    def service_ids(self) -> list[str]:
        return ["svc_detached"]

    def is_running(self, service_id: str) -> bool:
        return bool(self.running_by_service.get(str(service_id), False))

    def terminate_external_processes(self, service_id: str) -> Any:
        sid = str(service_id)
        self.terminate_external_calls.append(sid)
        self.running_by_service[sid] = False
        return SimpleNamespace(success=True, matched_pids=(1234,), terminated_pids=(1234,), remaining_pids=())

    def start(self, req: Any) -> None:
        del req

    def stop(self, req: Any) -> Any:
        sid = str(req.service_id)
        self.stop_calls.append(sid)
        self.running_by_service[sid] = False
        return SimpleNamespace(success=True)


def test_bridge_stop_skips_blocking_zenoh_singleton_native_close() -> None:
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

    assert not entered.is_set()
    assert elapsed_s < 2.0
    assert bridge._async_started is False
    assert bridge._zenoh_singleton_session is None


def test_bridge_stop_preserves_detached_processes() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig(kill_managed_services_on_exit=False))
    bridge._ensure_async_runtime_started()
    fake_gateway = _FakeProcessGateway()
    bridge._process_gateway = fake_gateway

    bridge.stop()

    assert fake_gateway.stop_calls == []
    assert bridge._cfg.supervision_mode == "studio_owned"


def test_bridge_stop_stops_known_managed_services_not_only_tracked_processes() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig(kill_managed_services_on_exit=True))
    bridge._ensure_async_runtime_started()
    fake_gateway = _FakeProcessGateway()
    fake_gateway.running_by_service.update(
        {
            "svc_managed": True,
            "svc_live": True,
            "svc_status": True,
        }
    )
    bridge._process_gateway = fake_gateway
    bridge._managed_service_ids.add("svc_managed")
    bridge._service_liveliness_instances_by_service["svc_live"] = {"inst_live"}
    bridge._service_status_cache["svc_status"] = (True, 0.0)

    async def _request_service_terminate_async(service_id: str) -> bool:
        _ = service_id
        return True

    bridge._request_service_terminate_async = _request_service_terminate_async  # type: ignore[method-assign]

    bridge.stop()

    assert fake_gateway.stop_calls == ["svc_detached", "svc_live", "svc_managed", "svc_status"]
    assert fake_gateway.terminate_external_calls == ["svc_live"]
    assert bridge._service_liveliness_instances_by_service == {}
    assert bridge._service_alive_cache["svc_live"][0] is False
