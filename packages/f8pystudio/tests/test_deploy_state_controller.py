from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from f8pystudio.bridge.deploy_state_controller import DeployStateControllerMixin


class _RungraphDeployFlow:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.allowed_service_ids: set[str] | None = None

    async def deploy_selected_service_rungraphs(
        self, *, compiled: object, allowed_service_ids: set[str] | None
    ) -> None:
        _ = compiled
        self.allowed_service_ids = set(allowed_service_ids or set())
        self._events.append("deploy")


class _Harness(DeployStateControllerMixin):
    def __init__(self) -> None:
        self.studio_service_id = "studio"
        self._events: list[str] = []
        self._rungraph_deploy_flow = _RungraphDeployFlow(self._events)
        self._ensure_results: dict[str, bool] = {}
        self._ensure_delays: dict[str, float] = {}
        self._previous_classes_seen: dict[str, str | None] = {}
        self.logs: list[str] = []
        self.exceptions: list[str] = []
        self.refreshed_compiled: list[object] = []

    async def ensure_service_available(
        self,
        service_id: str,
        desired_service_class: str,
        *,
        local_known_service_class: str | None = None,
    ) -> bool:
        sid = str(service_id)
        self._events.append(f"start:{sid}")
        self._previous_classes_seen[sid] = local_known_service_class
        delay = float(self._ensure_delays.get(sid, 0.0))
        if delay > 0:
            await asyncio.sleep(delay)
        self._events.append(f"finish:{sid}")
        return bool(self._ensure_results.get(sid, True))

    async def _refresh_studio_runtime_async(self, *, compiled: Any | None = None) -> None:
        self.refreshed_compiled.append(compiled)
        self._events.append("refresh")

    def _emit_log_line(self, line: str) -> None:
        self.logs.append(str(line))

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.exceptions.append(f"{context}:{type(exc).__name__}")


def test_ensure_remote_services_runs_start_gates_concurrently_and_deploys_passed_services() -> None:
    harness = _Harness()
    harness._ensure_delays = {
        "svc_slow": 0.05,
        "svc_blocked": 0.01,
    }
    harness._ensure_results = {
        "svc_slow": True,
        "svc_fast": True,
        "svc_blocked": False,
    }
    compiled = SimpleNamespace()

    asyncio.run(
        harness._ensure_remote_services_and_deploy_async(
            compiled,  # type: ignore[arg-type]
            start_order=(
                ("svc_slow", "f8.tests.slow"),
                ("svc_fast", "f8.tests.fast"),
                ("svc_blocked", "f8.tests.blocked"),
            ),
            previous_service_classes={"svc_fast": "f8.tests.fast.prev"},
        )
    )

    assert harness._events.index("start:svc_fast") < harness._events.index("finish:svc_slow")
    assert harness._rungraph_deploy_flow.allowed_service_ids == {"svc_fast", "svc_slow"}
    assert harness._previous_classes_seen["svc_fast"] == "f8.tests.fast.prev"
    assert harness._events[-2:] == ["deploy", "refresh"]
    assert harness.refreshed_compiled == [compiled]
    assert harness.exceptions == []
