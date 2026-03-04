from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from f8pystudio.bridge.rungraph_deploy_flow import RungraphDeployFlow, pick_compiled


@dataclass(frozen=True)
class _DeployResult:
    success: bool
    error_message: str = ""


class _FakeRungraphGateway:
    def __init__(self, *, reject_service_ids: set[str] | None = None) -> None:
        self._reject_service_ids = set(reject_service_ids or set())
        self.calls: list[str] = []

    async def deploy_runtime_graph(self, req: object) -> _DeployResult:
        service_id = str(req.service_id)  # type: ignore[attr-defined]
        self.calls.append(service_id)
        if service_id in self._reject_service_ids:
            return _DeployResult(success=False, error_message="rejected")
        return _DeployResult(success=True, error_message="")


def _compiled(per_service: dict[str, object]) -> object:
    return SimpleNamespace(per_service=dict(per_service))


def test_pick_compiled_prefers_explicit_value() -> None:
    explicit = _compiled({"svc_a": object()})
    fallback = _compiled({"svc_b": object()})
    assert pick_compiled(explicit, fallback) is explicit
    assert pick_compiled(None, fallback) is fallback


def test_deploy_service_rungraph_logs_rejection() -> None:
    logs: list[str] = []
    gateway = _FakeRungraphGateway(reject_service_ids={"svc_a"})
    flow = RungraphDeployFlow(
        studio_service_id="studio",
        rungraph_gateway=gateway,
        emit_log=lambda line: logs.append(str(line)),
    )
    compiled = _compiled({"svc_a": object()})

    asyncio.run(flow.deploy_service_rungraph(service_id="svc_a", compiled=compiled))  # type: ignore[arg-type]

    assert gateway.calls == ["svc_a"]
    assert logs == ["deploy service rungraph failed serviceId=svc_a: rejected"]


def test_deploy_all_service_rungraphs_skips_studio_service() -> None:
    logs: list[str] = []
    gateway = _FakeRungraphGateway(reject_service_ids={"svc_b"})
    flow = RungraphDeployFlow(
        studio_service_id="studio",
        rungraph_gateway=gateway,
        emit_log=lambda line: logs.append(str(line)),
    )
    compiled = _compiled(
        {
            "studio": object(),
            "svc_a": object(),
            "svc_b": object(),
        }
    )

    asyncio.run(flow.deploy_all_service_rungraphs(compiled=compiled))  # type: ignore[arg-type]

    assert gateway.calls == ["svc_a", "svc_b"]
    assert logs == ["deploy failed serviceId=svc_b: rejected"]
