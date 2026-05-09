from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import msgspec
from f8pysdk.bus import BusBackend
from f8pysdk.specs import F8EmptyArgs, F8RuntimeGraph, F8RuntimeGraphMeta
from f8pysdk.specs import F8StatusReply, F8StatusRequest
from f8pysdk.specs import F8SetRungraphArgs, F8SetRungraphReply, F8SetRungraphRequest
from f8pysdk.codec import copy_model
from f8pysdk.f8_naming import new_id, svc_endpoint_key
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig
from f8pysdk.service_runtime_tools.deploy.readiness import (
    RungraphDeployStatusTimeout,
    wait_rungraph_deploy_status,
)
from f8pysdk.codec import decode_as, encode_obj


class RungraphGateway(Protocol):
    async def deploy_runtime_graph(self, req: "RungraphDeployRequest") -> "RungraphDeployResult": ...


@dataclass(frozen=True)
class RungraphDeployConfig:
    endpoint_ready_timeout_s: float = 4.0
    endpoint_probe_timeout_s: float = 0.5
    request_timeout_s: float = 2.0
    apply_timeout_s: float = 15.0
    bus_backend: BusBackend = "zenoh"
    client_service_id: str = "studio"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = 256 * 1024 * 1024


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


@dataclass
class RuntimeRungraphGateway:
    config: RungraphDeployConfig
    _transport: RuntimeTransport | None = field(default=None, init=False)
    _connect_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _connect_lock_loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    @staticmethod
    def _normalize_graph_for_request(graph: F8RuntimeGraph, *, source: str = "") -> F8RuntimeGraph:
        normalized_nodes = []
        changed = False
        for node in list(graph.nodes or []):
            if node.operatorClass is None:
                normalized_nodes.append(copy_model(node, update={"operatorClass": msgspec.UNSET}))
                changed = True
            else:
                normalized_nodes.append(node)
        meta = graph.meta
        if meta is None or isinstance(meta, msgspec.UnsetType):
            meta = F8RuntimeGraphMeta()
        source_s = str(source or "").strip()
        meta_update = {"source": source_s} if source_s else {}
        meta2 = copy_model(meta, update=meta_update) if meta_update else meta
        if not changed and meta2 is meta:
            return graph
        return copy_model(graph, update={"nodes": normalized_nodes, "meta": meta2})

    async def _wait_apply_evidence(
        self,
        transport: RuntimeTransport,
        *,
        service_id: str,
        req_id: str,
        graph_id: str,
        revision: str,
    ) -> RungraphDeployResult:
        status_task = asyncio.create_task(
            wait_rungraph_deploy_status(
                transport,
                service_id=service_id,
                req_id=req_id,
                graph_id=graph_id,
                revision=revision,
                timeout_s=float(self.config.apply_timeout_s),
            ),
            name=f"rungraph_deploy_status:{service_id}:{req_id}",
        )
        try:
            result = await status_task
            if result.phase == "failed":
                return RungraphDeployResult(
                    service_id=service_id,
                    success=False,
                    error_message=result.error_message or "rungraph apply failed",
                )
            return RungraphDeployResult(service_id=service_id, success=True, error_message="")
        except RungraphDeployStatusTimeout as exc:
            return RungraphDeployResult(
                service_id=service_id,
                success=False,
                error_message=str(exc),
            )
        except asyncio.TimeoutError:
            return RungraphDeployResult(
                service_id=service_id,
                success=False,
                error_message=f"rungraph apply status not received within {float(self.config.apply_timeout_s):g}s",
            )
        finally:
            if not status_task.done():
                status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)

    def _build_transport(self) -> RuntimeTransport:
        if self.config.bus_backend == "mem":
            from f8pysdk.testing import InMemoryCluster, InMemoryTransport

            return InMemoryTransport(cluster=InMemoryCluster())
        if self.config.bus_backend != "zenoh":
            raise ValueError("Runtime rungraph deployment supports only bus_backend='zenoh' or 'mem'.")
        return ZenohTransport(
            ZenohTransportConfig(
                service_id=str(self.config.client_service_id),
                config_path=self.config.zenoh_config_path,
                connect=self.config.zenoh_connect,
                listen=self.config.zenoh_listen,
                shm_pool_bytes=self.config.zenoh_shm_pool_bytes,
            )
        )

    def _connect_lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._connect_lock
        if lock is None or self._connect_lock_loop is not loop:
            lock = asyncio.Lock()
            self._connect_lock = lock
            self._connect_lock_loop = loop
        return lock

    async def ensure_connected(self) -> RuntimeTransport:
        transport = self._transport
        if transport is not None:
            return transport
        async with self._connect_lock_for_loop():
            transport = self._transport
            if transport is not None:
                return transport
            transport = self._build_transport()
            await transport.connect()
            self._transport = transport
            return transport

    async def close(self) -> None:
        transport = self._transport
        self._transport = None
        self._connect_lock = None
        self._connect_lock_loop = None
        if transport is not None:
            await transport.close()

    async def _wait_control_endpoint_ready(
        self,
        transport: RuntimeTransport,
        *,
        service_id: str,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + max(0.001, float(self.config.endpoint_ready_timeout_s))
        last_error = ""
        while True:
            req_id = new_id()
            request_payload = F8StatusRequest(reqId=req_id, args=F8EmptyArgs(), meta={"source": "studio:probe"})
            try:
                raw = await transport.request(
                    svc_endpoint_key(service_id, "status"),
                    encode_obj(request_payload),
                    timeout=float(self.config.endpoint_probe_timeout_s),
                    raise_on_error=True,
                )
                if raw:
                    response = decode_as(raw, F8StatusReply)
                    if bool(response.ok):
                        return
                    if response.error is not None and not isinstance(response.error, msgspec.UnsetType):
                        last_error = str(response.error.message or "")
            except (TimeoutError, ValueError, RuntimeError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                suffix = f": {last_error}" if last_error else ""
                raise TimeoutError(
                    f"service control endpoint not ready within {float(self.config.endpoint_ready_timeout_s):g}s{suffix} "
                    f"({self._runtime_transport_diagnostics()})"
                )
            await asyncio.sleep(min(0.1, max(0.01, remaining)))

    def _runtime_transport_diagnostics(self) -> str:
        config_path = str(self.config.zenoh_config_path or "").strip() or "<default>"
        connect = ",".join(self.config.zenoh_connect) or "<auto>"
        listen = ",".join(self.config.zenoh_listen) or "<auto>"
        return (
            f"bus={self.config.bus_backend} clientServiceId={self.config.client_service_id} "
            f"zenohConfig={config_path} zenohConnect={connect} zenohListen={listen} "
            f"zenohShmPoolBytes={int(self.config.zenoh_shm_pool_bytes)}"
        )

    async def deploy_runtime_graph(self, req: RungraphDeployRequest) -> RungraphDeployResult:
        service_id = str(req.service_id)
        transport = await self.ensure_connected()
        try:
            await self._wait_control_endpoint_ready(
                transport,
                service_id=service_id,
            )
        except TimeoutError as exc:
            return RungraphDeployResult(
                service_id=service_id,
                success=False,
                error_message=str(exc),
            )
        req_id = new_id()
        deploy_source = f"{str(req.source or 'studio')}:{req_id}"
        graph_for_request = self._normalize_graph_for_request(req.graph, source=deploy_source)
        request_payload = F8SetRungraphRequest(
            reqId=req_id,
            args=F8SetRungraphArgs(graph=graph_for_request),
            meta={"source": deploy_source},
        )
        graph_id = str(graph_for_request.graphId or "")
        revision = str(graph_for_request.revision or "")
        try:
            response_bytes = await transport.request(
                svc_endpoint_key(service_id, "set_rungraph"),
                encode_obj(request_payload),
                timeout=float(self.config.request_timeout_s),
                raise_on_error=True,
            )
        except TimeoutError:
            fallback = await self._wait_apply_evidence(
                transport,
                service_id=service_id,
                req_id=req_id,
                graph_id=graph_id,
                revision=revision,
            )
            if fallback.success:
                return fallback
            return RungraphDeployResult(
                service_id=service_id,
                success=False,
                error_message=f"set_rungraph acknowledgement timed out; {fallback.error_message}",
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
        if not bool(response_payload.ok):
            return RungraphDeployResult(
                service_id=service_id,
                success=False,
                error_message=error_message or "set_rungraph rejected",
            )
        return await self._wait_apply_evidence(
            transport,
            service_id=service_id,
            req_id=req_id,
            graph_id=graph_id,
            revision=revision,
        )
