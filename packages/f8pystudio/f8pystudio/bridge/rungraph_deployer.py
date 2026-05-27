from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import msgspec
from f8pysdk.bus import BusBackend
from f8pysdk.codec import decode_obj
from f8pysdk.codec import copy_model
from f8pysdk.codec import decode_as, encode_obj
from f8pysdk.specs import F8EmptyArgs, F8RuntimeGraph, F8RuntimeGraphMeta
from f8pysdk.specs import F8SetRungraphArgs, F8SetRungraphReply, F8SetRungraphRequest
from f8pysdk.specs import F8StatusReply, F8StatusRequest
from f8pysdk.f8_naming import new_id, svc_endpoint_key
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig
from f8pysdk.service_runtime_tools.deploy.readiness import (
    rungraph_deploy_status_key,
)

from .rungraph_deploy_evidence import (
    RungraphApplyEvidenceTracker,
    decode_retained_rungraph_fingerprint,
    is_rungraph_config_key,
    rungraph_config_key,
)


logger = logging.getLogger(__name__)


class RungraphGateway(Protocol):
    async def deploy_runtime_graph(self, req: "RungraphDeployRequest") -> "RungraphDeployResult": ...


@dataclass(frozen=True)
class RungraphDeployConfig:
    endpoint_ready_timeout_s: float = 4.0
    endpoint_probe_timeout_s: float = 0.5
    request_timeout_s: float = 0.75
    request_attempts: int = 3
    request_retry_sleep_s: float = 0.15
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
    force_apply: bool = False
    expected_runtime_instance_id: str = ""


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

    async def _status_endpoint_has_target(
        self,
        transport: RuntimeTransport,
        *,
        service_id: str,
        target_fingerprint: str,
        expected_runtime_instance_id: str = "",
        timeout_s: float,
    ) -> bool:
        request_payload = F8StatusRequest(reqId=new_id(), args=F8EmptyArgs(), meta={"source": "studio:deploy-evidence"})
        try:
            raw = await transport.request(
                svc_endpoint_key(service_id, "status"),
                encode_obj(request_payload),
                timeout=float(timeout_s),
                raise_on_error=True,
            )
        except (TimeoutError, ValueError, RuntimeError, OSError) as exc:
            _ = exc
            return False
        if not raw:
            return False
        try:
            response = decode_as(raw, F8StatusReply)
        except ValueError:
            return False
        if not bool(response.ok):
            return False
        result = response.result
        if result is None or isinstance(result, msgspec.UnsetType):
            return False
        expected_runtime_instance_id_s = str(expected_runtime_instance_id or "").strip()
        if expected_runtime_instance_id_s:
            runtime_instance_id = str(result.runtimeInstanceId or "").strip()
            if runtime_instance_id != expected_runtime_instance_id_s:
                return False
        fingerprint = str(result.rungraphFingerprint or "").strip()
        return bool(fingerprint and fingerprint == target_fingerprint)

    @staticmethod
    async def _stop_retained_watch(watch: Any) -> None:
        if isinstance(watch, tuple):
            watcher, task = watch
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await watcher.stop()
            return
        await watch.stop()

    async def _retained_config_sample(
        self,
        transport: RuntimeTransport,
        *,
        service_id: str,
    ) -> tuple[bytes | None, str]:
        key = rungraph_config_key(service_id)
        try:
            raw = await transport.retained_get(key)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("rungraph retained config read failed key=%s", key, exc_info=exc)
            return (None, "")
        return (raw, decode_retained_rungraph_fingerprint(raw))

    async def _wait_target_evidence(
        self,
        transport: RuntimeTransport,
        *,
        service_id: str,
        req_id: str,
        target_fingerprint: str,
        initial_config_raw: bytes | None,
        initial_config_fingerprint: str,
        deadline_s: float,
        force_apply: bool = False,
        expected_runtime_instance_id: str = "",
        watcher_ready: asyncio.Future[None] | None = None,
    ) -> RungraphDeployResult:
        loop = asyncio.get_running_loop()
        watch_specs = (
            (f"{rungraph_deploy_status_key(service_id)}/**", True),
            (f"f8/svc/{service_id}/config/rungraph", False),
        )
        fut: asyncio.Future[RungraphDeployResult] = loop.create_future()
        expected_runtime_instance_id_s = str(expected_runtime_instance_id or "").strip()
        config_can_satisfy = not bool(force_apply) and not expected_runtime_instance_id_s
        evidence_tracker = RungraphApplyEvidenceTracker(
            service_id=service_id,
            req_id=req_id,
            target_fingerprint=target_fingerprint,
            expected_runtime_instance_id=expected_runtime_instance_id_s,
            apply_timeout_s=float(self.config.apply_timeout_s),
        )

        async def _on_evidence(key: str, value: bytes) -> None:
            if fut.done():
                return
            if is_rungraph_config_key(key):
                if not config_can_satisfy:
                    return
                fingerprint = decode_retained_rungraph_fingerprint(value)
                if fingerprint == target_fingerprint:
                    fut.set_result(RungraphDeployResult(service_id=service_id, success=True, error_message=""))
                return
            try:
                payload = decode_obj(value or b"")
            except ValueError:
                return
            decision = evidence_tracker.observe_status_payload(payload)
            if decision.success:
                fut.set_result(RungraphDeployResult(service_id=service_id, success=True, error_message=""))

        watches: list[Any] = []
        try:
            for key, with_initial in watch_specs:
                try:
                    watch = await transport.retained_watch(key, cb=_on_evidence, with_initial=with_initial)
                    watches.append(watch)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.debug("rungraph evidence retained_watch failed key=%s", key, exc_info=exc)
                    continue
            if watcher_ready is not None and not watcher_ready.done():
                watcher_ready.set_result(None)
            while True:
                if fut.done():
                    return await fut
                if config_can_satisfy and initial_config_fingerprint != target_fingerprint:
                    retained_raw, retained_fingerprint = await self._retained_config_sample(
                        transport,
                        service_id=service_id,
                    )
                    if retained_fingerprint == target_fingerprint and retained_raw != initial_config_raw:
                        return RungraphDeployResult(service_id=service_id, success=True, error_message="")
                if not bool(force_apply):
                    if await self._status_endpoint_has_target(
                        transport,
                        service_id=service_id,
                        target_fingerprint=target_fingerprint,
                        expected_runtime_instance_id=expected_runtime_instance_id_s,
                        timeout_s=min(0.25, max(0.05, self.config.endpoint_probe_timeout_s)),
                    ):
                        return RungraphDeployResult(service_id=service_id, success=True, error_message="")
                remaining = deadline_s - loop.time()
                if remaining <= 0:
                    return RungraphDeployResult(
                        service_id=service_id,
                        success=False,
                        error_message=evidence_tracker.timeout_error_message(),
                    )
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=min(0.2, remaining))
                except asyncio.TimeoutError:
                    continue
        finally:
            if watcher_ready is not None and not watcher_ready.done():
                watcher_ready.set_result(None)
            for watch in watches:
                try:
                    await self._stop_retained_watch(watch)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.debug("rungraph evidence retained_watch stop failed", exc_info=exc)

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
    ) -> str:
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
                        result = response.result
                        if result is None or isinstance(result, msgspec.UnsetType):
                            return ""
                        return str(result.runtimeInstanceId or "").strip()
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
        ready_runtime_instance_id = ""
        try:
            ready_runtime_instance_id = await self._wait_control_endpoint_ready(
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
        target_fingerprint = build_rungraph_deploy_fingerprint(graph_for_request)
        expected_runtime_instance_id = str(req.expected_runtime_instance_id or "").strip()
        if not expected_runtime_instance_id and bool(req.force_apply):
            expected_runtime_instance_id = ready_runtime_instance_id
        if not bool(req.force_apply):
            if await self._status_endpoint_has_target(
                transport,
                service_id=service_id,
                target_fingerprint=target_fingerprint,
                expected_runtime_instance_id=expected_runtime_instance_id,
                timeout_s=min(0.25, max(0.05, self.config.endpoint_probe_timeout_s)),
            ):
                return RungraphDeployResult(service_id=service_id, success=True, error_message="")
        initial_config_raw, initial_config_fingerprint = await self._retained_config_sample(
            transport,
            service_id=service_id,
        )
        request_payload = F8SetRungraphRequest(
            reqId=req_id,
            args=F8SetRungraphArgs(graph=graph_for_request),
            meta={"source": deploy_source, "targetFingerprint": target_fingerprint, "forceApply": bool(req.force_apply)},
        )
        deadline_s = asyncio.get_running_loop().time() + max(0.001, float(self.config.apply_timeout_s))
        watcher_ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        evidence_task = asyncio.create_task(
            self._wait_target_evidence(
                transport,
                service_id=service_id,
                req_id=req_id,
                target_fingerprint=target_fingerprint,
                initial_config_raw=initial_config_raw,
                initial_config_fingerprint=initial_config_fingerprint,
                deadline_s=deadline_s,
                force_apply=bool(req.force_apply),
                expected_runtime_instance_id=expected_runtime_instance_id,
                watcher_ready=watcher_ready,
            ),
            name=f"rungraph_target_evidence:{service_id}:{req_id}",
        )
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(watcher_ready),
                    timeout=min(0.5, max(0.05, float(self.config.endpoint_probe_timeout_s))),
                )
            except asyncio.TimeoutError:
                logger.debug("rungraph evidence watchers not ready before request service_id=%s", service_id)
            last_ack_error = ""
            request_attempts = max(1, int(self.config.request_attempts))
            for attempt_index in range(request_attempts):
                if evidence_task.done():
                    return await evidence_task
                try:
                    response_bytes = await transport.request(
                        svc_endpoint_key(service_id, "set_rungraph"),
                        encode_obj(request_payload),
                        timeout=float(self.config.request_timeout_s),
                        raise_on_error=True,
                    )
                except TimeoutError as exc:
                    last_ack_error = str(exc)
                except (RuntimeError, OSError) as exc:
                    last_ack_error = f"{type(exc).__name__}: {exc}"
                else:
                    if not response_bytes:
                        last_ack_error = "empty response"
                    else:
                        try:
                            response_payload = decode_as(response_bytes, F8SetRungraphReply)
                        except ValueError:
                            last_ack_error = "invalid response"
                            continue
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
                        break
                if attempt_index + 1 < request_attempts:
                    await asyncio.sleep(float(self.config.request_retry_sleep_s))
            result = await evidence_task
            if result.success or not last_ack_error:
                return result
            if "not received" in result.error_message:
                return RungraphDeployResult(
                    service_id=service_id,
                    success=False,
                    error_message=f"set_rungraph acknowledgement failed ({last_ack_error}); {result.error_message}",
                )
            return result
        finally:
            if not evidence_task.done():
                evidence_task.cancel()
                await asyncio.gather(evidence_task, return_exceptions=True)
