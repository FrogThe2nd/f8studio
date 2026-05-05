from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from f8pystudio.bridge import service_lifecycle_controller as lifecycle_module
from f8pystudio.bridge.service_lifecycle_controller import ServiceLifecycleControllerMixin


class _MonitorCenter:
    def __init__(self) -> None:
        self.status_updates: list[dict[str, Any]] = []
        self.dropped_services: list[str] = []

    def update_service_status(self, *, service_id: str, active: bool | None = None, alive: bool | None = None) -> None:
        self.status_updates.append(
            {
                "service_id": str(service_id),
                "active": active,
                "alive": alive,
            }
        )

    def drop_service(self, *, service_id: str) -> None:
        self.dropped_services.append(str(service_id))


class _ProcessGateway:
    def __init__(self) -> None:
        self.running_by_service: dict[str, bool] = {}
        self.start_calls: list[Any] = []
        self.stop_calls: list[Any] = []

    def start(self, request: Any) -> None:
        self.start_calls.append(request)
        self.running_by_service[str(request.config.service_id)] = True

    def stop(self, request: Any) -> Any:
        self.stop_calls.append(request)
        self.running_by_service[str(request.service_id)] = False
        return SimpleNamespace(success=True)

    def is_running(self, service_id: str) -> bool:
        return bool(self.running_by_service.get(str(service_id), False))


class _ProcessActions:
    def __init__(self) -> None:
        self.cancelled_services: list[str] = []
        self.scheduled_stops: list[str] = []
        self.scheduled_restarts: list[str] = []

    def cancel(self, service_id: str) -> None:
        self.cancelled_services.append(str(service_id))

    def schedule_stop(self, *, service_id: str, grace_s: float) -> None:
        _ = grace_s
        self.scheduled_stops.append(str(service_id))

    def schedule_restart(self, *, service_id: str, service_class: str, grace_s: float) -> None:
        _ = (service_class, grace_s)
        self.scheduled_restarts.append(str(service_id))


class _Harness(ServiceLifecycleControllerMixin):
    def __init__(self) -> None:
        self._cfg = SimpleNamespace()
        self.studio_service_id = "studio_default"
        self._managed_active = True
        self._managed_service_ids: set[str] = set()
        self._managed_service_classes: dict[str, str] = {}
        self._service_status_cache: dict[str, tuple[bool | None, float]] = {}
        self._service_alive_cache: dict[str, tuple[bool, float]] = {}
        self._service_status_inflight: set[str] = set()
        self._service_status_req_s: dict[str, float] = {}
        self._monitor_center = _MonitorCenter()
        self._process_gateway = _ProcessGateway()
        self._process_actions = _ProcessActions()
        self._submitted: list[tuple[Any, str]] = []
        self.logs: list[str] = []
        self.exceptions: list[str] = []
        self.emitted_states: list[tuple[str, bool]] = []
        self._ensure_requester_value: object | None = object()

    def _emit_log_line(self, line: str) -> None:
        self.logs.append(str(line))

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.exceptions.append(f"{context}:{type(exc).__name__}")

    def _emit_service_process_state_safe(self, service_id: str, running: bool) -> None:
        self.emitted_states.append((str(service_id), bool(running)))

    def _submit_async(self, coro: Any, *, context: str) -> bool:
        self._submitted.append((coro, str(context)))
        return True

    async def _ensure_requester(self) -> object | None:
        return self._ensure_requester_value


def test_request_service_status_dedupes_inflight(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        return {"active": service_id == "svc_alpha"}

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)

    bridge = _Harness()

    bridge.request_service_status("svc_alpha")
    bridge.request_service_status("svc_alpha")

    assert len(bridge._submitted) == 1
    assert "svc_alpha" in bridge._service_status_inflight

    submitted_coro, _context = bridge._submitted[0]
    asyncio.run(submitted_coro)

    assert "svc_alpha" not in bridge._service_status_inflight
    assert bridge.get_cached_service_active("svc_alpha") is True
    assert bridge._service_alive_cache["svc_alpha"][0] is True


def test_start_service_skips_when_already_running() -> None:
    bridge = _Harness()
    bridge._managed_service_classes["svc_beta"] = "f8.tests.beta"
    bridge._process_gateway.running_by_service["svc_beta"] = True

    bridge.start_service("svc_beta")

    assert bridge._process_gateway.start_calls == []
    assert any("already running" in line for line in bridge.logs)


def test_set_service_active_async_updates_cache_on_success(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def _fake_request_set_service_active(
        _nc: object,
        *,
        service_id: str,
        active: bool,
        attempts: int,
        timeout_s: float,
        retry_sleep_s: float,
    ) -> bool:
        _ = (attempts, timeout_s, retry_sleep_s)
        calls.append((str(service_id), bool(active)))
        return True

    monkeypatch.setattr(lifecycle_module, "request_set_service_active", _fake_request_set_service_active)

    bridge = _Harness()
    ok = asyncio.run(bridge._set_service_active_async("svc_gamma", True))

    assert ok is True
    assert calls == [("svc_gamma", True)]
    assert bridge.get_cached_service_active("svc_gamma") is True


def test_unmanage_service_clears_local_state() -> None:
    bridge = _Harness()
    bridge._managed_service_ids.add("svc_delta")
    bridge._managed_service_classes["svc_delta"] = "f8.tests.delta"
    bridge._service_status_cache["svc_delta"] = (True, 0.0)
    bridge._service_alive_cache["svc_delta"] = (True, 0.0)
    bridge._service_status_req_s["svc_delta"] = 1.0
    bridge._service_status_inflight.add("svc_delta")

    bridge.unmanage_service("svc_delta")

    assert "svc_delta" not in bridge._managed_service_ids
    assert "svc_delta" not in bridge._managed_service_classes
    assert "svc_delta" not in bridge._service_status_cache
    assert "svc_delta" not in bridge._service_alive_cache
    assert "svc_delta" not in bridge._service_status_req_s
    assert "svc_delta" not in bridge._service_status_inflight
    assert bridge._monitor_center.dropped_services == ["svc_delta"]
    assert bridge._process_actions.cancelled_services == ["svc_delta"]
