from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from f8pysdk.f8_naming import ensure_token
from f8pysdk.specs import F8RuntimeGraph

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from .rungraph_deployer import RungraphDeployRequest, RungraphGateway

DEPLOY_SERVICE_CONCURRENCY = 2


def pick_compiled(
    compiled: CompiledRuntimeGraphs | None,
    fallback: CompiledRuntimeGraphs | None,
) -> CompiledRuntimeGraphs | None:
    return compiled if compiled is not None else fallback


@dataclass
class RungraphDeployFlow:
    studio_service_id: str
    rungraph_gateway: RungraphGateway
    emit_log: Callable[[str], None]

    async def _deploy_one(
        self,
        *,
        service_id: str,
        graph: F8RuntimeGraph,
        failure_prefix: str,
        force_apply: bool = False,
    ) -> None:
        try:
            result = await self.rungraph_gateway.deploy_runtime_graph(
                RungraphDeployRequest(
                    service_id=service_id,
                    graph=graph,
                    source="studio",
                    force_apply=bool(force_apply),
                )
            )
            if not result.success:
                raise RuntimeError(result.error_message or "set_rungraph rejected")
        except Exception as exc:
            self.emit_log(f"{failure_prefix} serviceId={service_id}: {exc}")

    async def deploy_service_rungraph(
        self,
        *,
        service_id: str,
        compiled: CompiledRuntimeGraphs,
    ) -> None:
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        graph = compiled.per_service.get(sid)
        if graph is None:
            return

        await self._deploy_one(
            service_id=sid,
            graph=graph,
            failure_prefix="deploy service rungraph failed",
            force_apply=True,
        )

    async def deploy_all_service_rungraphs(self, *, compiled: CompiledRuntimeGraphs) -> None:
        await self.deploy_selected_service_rungraphs(compiled=compiled, allowed_service_ids=None)

    async def deploy_selected_service_rungraphs(
        self,
        *,
        compiled: CompiledRuntimeGraphs,
        allowed_service_ids: set[str] | None,
    ) -> None:
        async def _deploy_limited(service_id: str, graph: F8RuntimeGraph) -> None:
            async with semaphore:
                await self._deploy_one(
                    service_id=service_id,
                    graph=graph,
                    failure_prefix="deploy failed",
                    force_apply=False,
                )

        tasks: list[asyncio.Task[None]] = []
        semaphore = asyncio.Semaphore(DEPLOY_SERVICE_CONCURRENCY)
        for sid_raw, graph in compiled.per_service.items():
            service_id = ensure_token(str(sid_raw), label="service_id")
            if service_id == str(self.studio_service_id):
                continue
            if allowed_service_ids is not None and service_id not in allowed_service_ids:
                continue
            tasks.append(asyncio.create_task(_deploy_limited(service_id, graph), name=f"deploy_rungraph:{service_id}"))
        if tasks:
            await asyncio.gather(*tasks)
