from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from f8pysdk.f8_naming import ensure_token

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from .rungraph_deployer import RungraphDeployRequest, RungraphGateway


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

        try:
            result = await self.rungraph_gateway.deploy_runtime_graph(
                RungraphDeployRequest(
                    service_id=sid,
                    graph=graph,
                    source="studio",
                )
            )
            if not result.success:
                raise RuntimeError(result.error_message or "set_rungraph rejected")
        except Exception as exc:
            self.emit_log(f"deploy service rungraph failed serviceId={sid}: {exc}")

    async def deploy_all_service_rungraphs(self, *, compiled: CompiledRuntimeGraphs) -> None:
        for sid_raw, graph in compiled.per_service.items():
            service_id = ensure_token(str(sid_raw), label="service_id")
            if service_id == str(self.studio_service_id):
                continue
            try:
                result = await self.rungraph_gateway.deploy_runtime_graph(
                    RungraphDeployRequest(
                        service_id=service_id,
                        graph=graph,
                        source="studio",
                    )
                )
                if not result.success:
                    raise RuntimeError(result.error_message or "set_rungraph rejected")
            except Exception as exc:
                self.emit_log(f"deploy failed serviceId={service_id}: {exc}")
