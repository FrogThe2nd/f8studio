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
        self._cfg = SimpleNamespace(supervision_mode="studio_owned")
        self.studio_service_id = "studio_default"
        self._managed_active = True
        self._managed_service_ids: set[str] = set()
        self._managed_service_classes: dict[str, str] = {}
        self._service_status_cache: dict[str, tuple[bool | None, float]] = {}
        self._service_alive_cache: dict[str, tuple[bool, float]] = {}
        self._service_liveliness_instances_by_service: dict[str, set[str]] = {}
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
        self._restart_service_after_guard = SimpleNamespace(emit=self._emit_restart_service_after_guard)

    def _emit_log_line(self, line: str) -> None:
        self.logs.append(str(line))

    def _emit_restart_service_after_guard(self, service_id: str, service_class: object) -> None:
        self._on_restart_service_after_guard(str(service_id), service_class)

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.exceptions.append(f"{context}:{type(exc).__name__}")

    def _emit_service_process_state_safe(self, service_id: str, running: bool) -> None:
        self.emitted_states.append((str(service_id), bool(running)))

    def _submit_async(self, coro: Any, *, context: str) -> bool:
        self._submitted.append((coro, str(context)))
        return True

    async def _ensure_requester(self) -> object | None:
        return self._ensure_requester_value

    async def _query_zenoh_service_liveliness_instances_async(self, service_id: str) -> set[str]:
        return set(self._service_liveliness_instances_by_service.get(str(service_id), set()))


def test_request_service_status_dedupes_inflight(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        return {
            "active": service_id == "svc_alpha",
            "identityValid": True,
            "serviceId": service_id,
            "serviceClass": "f8.tests.alpha",
            "runtimeInstanceId": "inst_alpha",
        }

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


def test_ensure_service_available_spawns_when_no_live_instances() -> None:
    bridge = _Harness()

    ok = asyncio.run(bridge.ensure_service_available("svc_spawn", "f8.tests.spawn"))

    assert ok is True
    assert bridge._process_gateway.start_calls
    started = bridge._process_gateway.start_calls[0]
    assert started.config.service_id == "svc_spawn"
    assert started.config.service_class == "f8.tests.spawn"
    assert started.config.supervision_mode == "studio_owned"


def test_ensure_service_available_reuses_single_live_same_class(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        return {
            "alive": True,
            "identityValid": True,
            "serviceId": service_id,
            "serviceClass": "f8.tests.reuse",
            "runtimeInstanceId": "inst_reuse",
            "active": True,
        }

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_reuse"] = {"inst_reuse"}

    ok = asyncio.run(bridge.ensure_service_available("svc_reuse", "f8.tests.reuse"))

    assert ok is True
    assert bridge._process_gateway.start_calls == []
    assert bridge._managed_service_classes["svc_reuse"] == "f8.tests.reuse"
    assert any("reuse live service" in line for line in bridge.logs)


def test_ensure_service_available_blocks_service_class_mismatch(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = (service_id, timeout_s)
        return {
            "alive": True,
            "identityValid": True,
            "serviceId": "svc_mismatch",
            "serviceClass": "f8.tests.actual",
            "runtimeInstanceId": "inst_mismatch",
            "active": True,
        }

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_mismatch"] = {"inst_mismatch"}

    ok = asyncio.run(bridge.ensure_service_available("svc_mismatch", "f8.tests.desired"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("serviceClass collision" in line for line in bridge.logs)


def test_ensure_service_available_blocks_duplicate_instances() -> None:
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_dup"] = {"inst_a", "inst_b"}

    ok = asyncio.run(bridge.ensure_service_available("svc_dup", "f8.tests.dup"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("duplicate runtime instances" in line for line in bridge.logs)


def test_ensure_service_available_blocks_duplicate_instances_when_local_process_running() -> None:
    bridge = _Harness()
    bridge._process_gateway.running_by_service["svc_dup_local"] = True
    bridge._managed_service_classes["svc_dup_local"] = "f8.tests.dup"
    bridge._service_liveliness_instances_by_service["svc_dup_local"] = {"inst_a", "inst_b"}

    ok = asyncio.run(bridge.ensure_service_available("svc_dup_local", "f8.tests.dup"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("duplicate runtime instances" in line for line in bridge.logs)


def test_ensure_service_available_blocks_old_status_protocol(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = (service_id, timeout_s)
        return {"alive": True, "identityValid": False}

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_old"] = {"inst_old"}

    ok = asyncio.run(bridge.ensure_service_available("svc_old", "f8.tests.old"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("old protocol" in line for line in bridge.logs)


def test_ensure_service_available_blocks_identity_with_empty_runtime_instance(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = (service_id, timeout_s)
        return {
            "alive": True,
            "identityValid": True,
            "serviceId": "svc_bad_identity",
            "serviceClass": "f8.tests.bad",
            "runtimeInstanceId": "",
            "active": True,
        }

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_bad_identity"] = {"inst_bad"}

    ok = asyncio.run(bridge.ensure_service_available("svc_bad_identity", "f8.tests.bad"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("old protocol" in line for line in bridge.logs)


def test_restart_service_blocks_duplicate_instances() -> None:
    bridge = _Harness()
    bridge._process_gateway.running_by_service["svc_restart_dup"] = True
    bridge._managed_service_classes["svc_restart_dup"] = "f8.tests.restart"
    bridge._service_liveliness_instances_by_service["svc_restart_dup"] = {"inst_a", "inst_b"}

    bridge.restart_service("svc_restart_dup", service_class="f8.tests.restart")

    assert len(bridge._submitted) == 1
    submitted_coro, _context = bridge._submitted[0]
    asyncio.run(submitted_coro)

    assert bridge._process_actions.scheduled_restarts == []
    assert any("duplicate runtime instances" in line for line in bridge.logs)
