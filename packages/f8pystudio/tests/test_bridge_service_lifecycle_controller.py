from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeService
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
        self.external_by_service: dict[str, list[Any]] = {}
        self.terminate_external_calls: list[str] = []

    def start(self, request: Any) -> None:
        self.start_calls.append(request)
        self.running_by_service[str(request.config.service_id)] = True

    def stop(self, request: Any) -> Any:
        self.stop_calls.append(request)
        self.running_by_service[str(request.service_id)] = False
        return SimpleNamespace(success=True)

    def is_running(self, service_id: str) -> bool:
        return bool(self.running_by_service.get(str(service_id), False))

    def service_ids(self) -> list[str]:
        return sorted(self.running_by_service.keys())

    def external_processes(self, service_id: str) -> list[Any]:
        return list(self.external_by_service.get(str(service_id), ()))

    def terminate_external_processes(self, service_id: str) -> Any:
        sid = str(service_id)
        self.terminate_external_calls.append(sid)
        matches = list(self.external_by_service.get(sid, ()))
        self.external_by_service[sid] = []
        return SimpleNamespace(
            success=True,
            matched_pids=tuple(match.pid for match in matches),
            terminated_pids=tuple(match.pid for match in matches),
            remaining_pids=(),
        )


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
        self._last_compiled = None
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


def _close_submitted(bridge: _Harness) -> None:
    for coro, _context in list(bridge._submitted):
        if asyncio.iscoroutine(coro):
            coro.close()
    bridge._submitted.clear()


def _run_single_submitted(bridge: _Harness) -> None:
    assert len(bridge._submitted) == 1
    submitted_coro, _context = bridge._submitted.pop(0)
    asyncio.run(submitted_coro)


def _compiled_for_service(service_id: str, service_class: str) -> Any:
    graph = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        services=[
            F8RuntimeService(serviceId=str(service_id), serviceClass=str(service_class)),
        ],
        nodes=[],
        edges=[],
    )
    return SimpleNamespace(global_graph=graph, per_service={str(service_id): graph}, warnings=())


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

    assert "svc_alpha" in bridge._service_status_inflight
    _run_single_submitted(bridge)

    assert "svc_alpha" not in bridge._service_status_inflight
    assert bridge.get_cached_service_active("svc_alpha") is True
    assert bridge._service_alive_cache["svc_alpha"][0] is True


def test_start_service_skips_when_already_running(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = timeout_s
        return {
            "active": True,
            "identityValid": True,
            "serviceId": service_id,
            "serviceClass": "f8.tests.beta",
            "runtimeInstanceId": "inst_beta",
        }

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)

    bridge = _Harness()
    bridge._managed_service_classes["svc_beta"] = "f8.tests.beta"
    bridge._process_gateway.running_by_service["svc_beta"] = True
    bridge._service_liveliness_instances_by_service["svc_beta"] = {"inst_beta"}

    bridge.start_service("svc_beta")
    _run_single_submitted(bridge)

    assert bridge._process_gateway.start_calls == []
    assert any("reuse local service" in line for line in bridge.logs)


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


def test_ensure_service_available_cleans_untracked_local_process_collision_in_studio_owned() -> None:
    bridge = _Harness()
    bridge._process_gateway.external_by_service["svc_orphan"] = [
        SimpleNamespace(pid=101),
        SimpleNamespace(pid=202),
    ]

    ok = asyncio.run(bridge.ensure_service_available("svc_orphan", "f8.tests.orphan"))

    assert ok is True
    assert bridge._process_gateway.terminate_external_calls == ["svc_orphan"]
    assert len(bridge._process_gateway.start_calls) == 1
    assert any("cleaned untracked local service processes serviceId=svc_orphan pids=101,202" in line for line in bridge.logs)


def test_ensure_service_available_blocks_untracked_local_process_collision_in_detached() -> None:
    bridge = _Harness()
    bridge._cfg = SimpleNamespace(supervision_mode="detached")
    bridge._process_gateway.external_by_service["svc_orphan"] = [
        SimpleNamespace(pid=101),
        SimpleNamespace(pid=202),
    ]

    ok = asyncio.run(bridge.ensure_service_available("svc_orphan", "f8.tests.orphan"))

    assert ok is False
    assert bridge._process_gateway.terminate_external_calls == []
    assert bridge._process_gateway.start_calls == []
    assert any("deploy blocked serviceId=svc_orphan: untracked local process collision pids=101,202" in line for line in bridge.logs)


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


def test_ensure_service_available_blocks_unknown_liveliness_state() -> None:
    class _UnknownLivelinessHarness(_Harness):
        async def _query_zenoh_service_liveliness_instances_async(self, service_id: str) -> set[str] | None:
            _ = service_id
            return None

    bridge = _UnknownLivelinessHarness()

    ok = asyncio.run(bridge.ensure_service_available("svc_unknown", "f8.tests.unknown"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("service liveliness query failed" in line for line in bridge.logs)


def test_ensure_service_available_blocks_single_live_instance_when_status_unreachable(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any] | None:
        _ = (service_id, timeout_s)
        return None

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_status_down"] = {"inst_down"}

    ok = asyncio.run(bridge.ensure_service_available("svc_status_down", "f8.tests.down"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("live service status unreachable" in line for line in bridge.logs)


def test_ensure_service_available_replaces_untracked_local_live_process_when_status_unreachable(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any] | None:
        _ = (service_id, timeout_s)
        return None

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_live_down"] = {"inst_down"}
    bridge._process_gateway.external_by_service["svc_live_down"] = [SimpleNamespace(pid=303)]

    ok = asyncio.run(bridge.ensure_service_available("svc_live_down", "f8.tests.down"))

    assert ok is True
    assert bridge._process_gateway.terminate_external_calls == ["svc_live_down"]
    assert len(bridge._process_gateway.start_calls) == 1
    assert any("cleaned untracked local service processes serviceId=svc_live_down pids=303" in line for line in bridge.logs)


def test_ensure_service_available_blocks_local_running_when_status_unreachable(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any] | None:
        _ = (service_id, timeout_s)
        return None

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._process_gateway.running_by_service["svc_local_down"] = True
    bridge._managed_service_classes["svc_local_down"] = "f8.tests.localdown"
    bridge._service_liveliness_instances_by_service["svc_local_down"] = {"inst_local"}

    ok = asyncio.run(bridge.ensure_service_available("svc_local_down", "f8.tests.localdown"))

    assert ok is False
    assert bridge._process_gateway.start_calls == []
    assert any("local service status unreachable" in line for line in bridge.logs)


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


def test_ensure_service_available_replaces_untracked_local_live_process_with_old_status_protocol(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = (service_id, timeout_s)
        return {"alive": True, "identityValid": False}

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_old_local"] = {"inst_old"}
    bridge._process_gateway.external_by_service["svc_old_local"] = [SimpleNamespace(pid=404)]

    ok = asyncio.run(bridge.ensure_service_available("svc_old_local", "f8.tests.old"))

    assert ok is True
    assert bridge._process_gateway.terminate_external_calls == ["svc_old_local"]
    assert len(bridge._process_gateway.start_calls) == 1
    assert bridge._process_gateway.start_calls[0].config.service_id == "svc_old_local"


def test_ensure_service_available_replaces_untracked_local_live_process_on_class_mismatch(monkeypatch) -> None:
    async def _fake_request_service_status(_nc: object, *, service_id: str, timeout_s: float) -> dict[str, Any]:
        _ = (timeout_s,)
        return {
            "alive": True,
            "identityValid": True,
            "serviceId": service_id,
            "serviceClass": "f8.tests.oldclass",
            "runtimeInstanceId": "inst_mismatch",
            "active": True,
        }

    monkeypatch.setattr(lifecycle_module, "request_service_status", _fake_request_service_status)
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_mismatch_local"] = {"inst_mismatch"}
    bridge._process_gateway.external_by_service["svc_mismatch_local"] = [SimpleNamespace(pid=505)]

    ok = asyncio.run(bridge.ensure_service_available("svc_mismatch_local", "f8.tests.newclass"))

    assert ok is True
    assert bridge._process_gateway.terminate_external_calls == ["svc_mismatch_local"]
    assert len(bridge._process_gateway.start_calls) == 1
    assert bridge._process_gateway.start_calls[0].config.service_class == "f8.tests.newclass"


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

    _run_single_submitted(bridge)

    assert bridge._process_actions.scheduled_restarts == []
    assert any("duplicate runtime instances" in line for line in bridge.logs)


def test_start_service_uses_ensure_gate_and_blocks_duplicate_instances() -> None:
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_start_dup"] = {"inst_a", "inst_b"}

    bridge.start_service("svc_start_dup", service_class="f8.tests.start")
    _run_single_submitted(bridge)

    assert bridge._process_gateway.start_calls == []
    assert any("duplicate runtime instances" in line for line in bridge.logs)


def test_start_service_and_deploy_uses_single_ensure_gate() -> None:
    bridge = _Harness()
    bridge._last_compiled = None
    refresh_calls: list[Any] = []
    deploy_calls: list[str] = []
    active_calls: list[tuple[str, bool]] = []

    async def _refresh_studio_runtime_async(*, compiled: object | None = None) -> None:
        refresh_calls.append(compiled)

    async def _deploy_service_rungraph_async(service_id: str, *, compiled: object | None = None) -> None:
        _ = compiled
        deploy_calls.append(str(service_id))

    async def _set_service_active_async(service_id: str, active: bool) -> bool:
        active_calls.append((str(service_id), bool(active)))
        return True

    bridge._refresh_studio_runtime_async = _refresh_studio_runtime_async  # type: ignore[method-assign]
    bridge._deploy_service_rungraph_async = _deploy_service_rungraph_async  # type: ignore[method-assign]
    bridge._set_service_active_async = _set_service_active_async  # type: ignore[method-assign]

    compiled = _compiled_for_service("svc_start_deploy", "f8.tests.startdeploy")
    bridge.start_service_and_deploy("svc_start_deploy", service_class="f8.tests.startdeploy", compiled=compiled)
    _run_single_submitted(bridge)

    assert len(bridge._process_gateway.start_calls) == 1
    assert bridge._process_gateway.start_calls[0].config.service_id == "svc_start_deploy"
    assert refresh_calls == [compiled]
    assert deploy_calls == ["svc_start_deploy"]
    assert active_calls == [("svc_start_deploy", True)]


def test_restart_service_and_deploy_waits_for_old_process_before_deploy() -> None:
    bridge = _Harness()
    bridge._process_gateway.running_by_service["svc_restart_deploy"] = True
    refresh_calls: list[Any] = []
    deploy_calls: list[str] = []
    active_calls: list[tuple[str, bool]] = []

    async def _request_service_terminate_async(service_id: str) -> bool:
        bridge._process_gateway.running_by_service[str(service_id)] = False
        return True

    async def _refresh_studio_runtime_async(*, compiled: object | None = None) -> None:
        refresh_calls.append(compiled)

    async def _deploy_service_rungraph_async(service_id: str, *, compiled: object | None = None) -> None:
        _ = compiled
        deploy_calls.append(str(service_id))

    async def _set_service_active_async(service_id: str, active: bool) -> bool:
        active_calls.append((str(service_id), bool(active)))
        return True

    bridge._request_service_terminate_async = _request_service_terminate_async  # type: ignore[method-assign]
    bridge._refresh_studio_runtime_async = _refresh_studio_runtime_async  # type: ignore[method-assign]
    bridge._deploy_service_rungraph_async = _deploy_service_rungraph_async  # type: ignore[method-assign]
    bridge._set_service_active_async = _set_service_active_async  # type: ignore[method-assign]

    compiled = _compiled_for_service("svc_restart_deploy", "f8.tests.restartdeploy")
    bridge.restart_service_and_deploy("svc_restart_deploy", service_class="f8.tests.restartdeploy", compiled=compiled)
    _run_single_submitted(bridge)

    assert bridge._process_actions.cancelled_services == ["svc_restart_deploy"]
    assert bridge.emitted_states[0] == ("svc_restart_deploy", False)
    assert len(bridge._process_gateway.start_calls) == 1
    assert bridge._process_gateway.start_calls[0].config.service_id == "svc_restart_deploy"
    assert refresh_calls == [compiled]
    assert deploy_calls == ["svc_restart_deploy"]
    assert active_calls == [("svc_restart_deploy", True)]


def test_restart_service_and_deploy_blocks_when_old_process_will_not_stop() -> None:
    bridge = _Harness()
    bridge._process_gateway.running_by_service["svc_restart_stuck"] = True
    deploy_calls: list[str] = []

    async def _request_service_terminate_async(service_id: str) -> bool:
        _ = service_id
        return False

    async def _deploy_service_rungraph_async(service_id: str, *, compiled: object | None = None) -> None:
        _ = compiled
        deploy_calls.append(str(service_id))

    bridge._request_service_terminate_async = _request_service_terminate_async  # type: ignore[method-assign]
    bridge._deploy_service_rungraph_async = _deploy_service_rungraph_async  # type: ignore[method-assign]
    bridge._stop_process_once_local = lambda service_id: False  # type: ignore[method-assign]

    bridge.restart_service_and_deploy("svc_restart_stuck", service_class="f8.tests.stuck")
    _run_single_submitted(bridge)

    assert bridge._process_gateway.start_calls == []
    assert deploy_calls == []
    assert any("service did not stop" in line for line in bridge.logs)


def test_stop_all_services_covers_known_and_process_services() -> None:
    bridge = _Harness()
    bridge._managed_service_ids.add("svc_managed")
    bridge._managed_service_classes["svc_class"] = "f8.tests.class"
    bridge._service_alive_cache["svc_alive"] = (True, 0.0)
    bridge._service_status_cache["svc_status"] = (True, 0.0)
    bridge._service_liveliness_instances_by_service["svc_live"] = {"inst_live"}
    for sid in ("svc_managed", "svc_class", "svc_alive", "svc_status", "svc_live", "svc_proc"):
        bridge._process_gateway.running_by_service[sid] = True

    bridge.stop_all_services()
    _close_submitted(bridge)

    assert bridge._process_actions.scheduled_stops == [
        "svc_alive",
        "svc_class",
        "svc_live",
        "svc_managed",
        "svc_proc",
        "svc_status",
    ]


def test_liveliness_instance_counts_as_running_after_alive_cache_expires() -> None:
    bridge = _Harness()
    bridge._service_liveliness_instances_by_service["svc_live_only"] = {"inst_live"}
    bridge._service_alive_cache["svc_live_only"] = (True, 0.0)

    assert bridge.is_service_running("svc_live_only") is True
