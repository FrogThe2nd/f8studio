from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import msgspec
from f8pysdk.specs import F8RuntimeGraph
from f8pysdk.specs import F8SetRungraphArgs, F8SetRungraphReply, F8SetRungraphRequest
from f8pysdk.codec import copy_model
from f8pysdk.nats_naming import kv_bucket_for_service, new_id, svc_endpoint_subject
from f8pysdk.transport import NatsTransport, NatsTransportConfig
from f8pysdk.service_runtime_tools.deploy.readiness import wait_service_ready
from f8pysdk.codec import decode_as, encode_obj


class RungraphGateway(Protocol):
    async def deploy_runtime_graph(self, req: "RungraphDeployRequest") -> "RungraphDeployResult": ...


@dataclass(frozen=True)
class RungraphDeployConfig:
    nats_url: str
    ready_timeout_s: float = 6.0
    request_timeout_s: float = 2.0


@dataclass(frozen=True)
class RungraphDeployRequest:
    service_id: str
    graph: F8RuntimeGraph
    source: str = "studio"


@dataclass(frozen=True)
class RungraphDeployResult:
    service_id: str
    success: bool
    error_message: str = ""


@dataclass(frozen=True)
class NatsRungraphGateway:
    config: RungraphDeployConfig

    @staticmethod
    def _normalize_graph_for_request(graph: F8RuntimeGraph) -> F8RuntimeGraph:
        normalized_nodes = []
        changed = False
        for node in list(graph.nodes or []):
            if node.operatorClass is None:
                normalized_nodes.append(copy_model(node, update={"operatorClass": msgspec.UNSET}))
                changed = True
            else:
                normalized_nodes.append(node)
        if not changed:
            return graph
        return copy_model(graph, update={"nodes": normalized_nodes})

    async def deploy_runtime_graph(self, req: RungraphDeployRequest) -> RungraphDeployResult:
        service_id = str(req.service_id)
        bucket = kv_bucket_for_service(service_id)
        transport = NatsTransport(NatsTransportConfig(url=str(self.config.nats_url), kv_bucket=bucket))
        await transport.connect()
        try:
            await wait_service_ready(transport, timeout_s=float(self.config.ready_timeout_s))
            graph_for_request = self._normalize_graph_for_request(req.graph)
            request_payload = F8SetRungraphRequest(
                reqId=new_id(),
                args=F8SetRungraphArgs(graph=graph_for_request),
                meta={"source": str(req.source or "studio")},
            )
            request_bytes = encode_obj(request_payload)
            response_bytes = await transport.request(
                svc_endpoint_subject(service_id, "set_rungraph"),
                request_bytes,
                timeout=float(self.config.request_timeout_s),
                raise_on_error=True,
            )
            if not response_bytes:
                return RungraphDeployResult(service_id=service_id, success=False, error_message="empty response")
            try:
                response_payload = decode_as(response_bytes, F8SetRungraphReply)
            except ValueError:
                return RungraphDeployResult(service_id=service_id, success=False, error_message="invalid response")
            if response_payload.error is None or isinstance(response_payload.error, msgspec.UnsetType):
                error_message = ""
            else:
                error_message = str(response_payload.error.message or "")
            return RungraphDeployResult(
                service_id=service_id,
                success=bool(response_payload.ok),
                error_message=("" if response_payload.ok else error_message),
            )
        finally:
            await transport.close()
