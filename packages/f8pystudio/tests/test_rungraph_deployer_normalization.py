from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import msgspec

from f8pysdk.codec import decode_obj, dump_json, encode_obj
from f8pysdk.f8_naming import svc_endpoint_key
from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint
from f8pysdk.service_runtime_tools.deploy.readiness import (
    rungraph_deploy_request_status_key,
    rungraph_deploy_status_key,
)
from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode

from f8pystudio.bridge.rungraph_deployer import RuntimeRungraphGateway, RungraphDeployConfig


def _match_retained_pattern(pattern: str, key: str) -> bool:
    if pattern == key:
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return key == prefix or key.startswith(f"{prefix}/")
    return False


def test_normalize_graph_for_request_omits_null_operator_class_for_service_nodes() -> None:
    graph = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        nodes=[
            F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None),
            F8RuntimeNode(nodeId="op1", serviceId="svc1", serviceClass="svc.a", operatorClass="svc.a.op"),
        ],
        edges=[],
    )
    gateway = RuntimeRungraphGateway(RungraphDeployConfig())
    normalized = gateway._normalize_graph_for_request(graph)

    assert isinstance(normalized.nodes[0].operatorClass, msgspec.UnsetType)
    payload = dump_json(normalized, mode="json", by_alias=True)
    assert isinstance(payload, dict)
    nodes_payload = payload.get("nodes")
    assert isinstance(nodes_payload, list)
    assert "operatorClass" not in nodes_payload[0]
    assert nodes_payload[1].get("operatorClass") == "svc.a.op"


class _GatewayTransportStub:
    def __init__(
        self,
        *,
        publish_status: bool = True,
        request_timeout: bool = False,
        status_runtime_instance_id: str = "inst_svc1",
        apply_runtime_instance_id: str = "inst_svc1",
    ) -> None:
        self.retained: dict[str, bytes] = {}
        self._watchers: list[tuple[str, Callable[[str, bytes], Awaitable[None]]]] = []
        self.request_payloads: list[dict[str, object]] = []
        self.status_probe_count = 0
        self.publish_status = bool(publish_status)
        self.request_timeout = bool(request_timeout)
        self.status_runtime_instance_id = str(status_runtime_instance_id)
        self.apply_runtime_instance_id = str(apply_runtime_instance_id)
        self.status_fingerprint = ""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def publish(self, key: str, payload: bytes) -> None:
        del key, payload

    async def subscribe(self, key_expr: str, *, queue: str | None = None, cb: object | None = None) -> object:
        del key_expr, queue, cb
        return None

    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        del timeout, raise_on_error
        if key == svc_endpoint_key("svc1", "status"):
            self.status_probe_count += 1
            decoded_status = decode_obj(payload)
            assert isinstance(decoded_status, dict)
            return encode_obj(
                {
                    "reqId": str(decoded_status.get("reqId") or ""),
                    "ok": True,
                    "result": {
                        "serviceId": "svc1",
                        "serviceClass": "f8.tests.svc1",
                        "runtimeInstanceId": self.status_runtime_instance_id,
                        "active": True,
                        "rungraphGraphId": "g1",
                        "rungraphRevision": "r1",
                        "rungraphFingerprint": self.status_fingerprint,
                    },
                    "error": None,
                }
            )
        assert key == svc_endpoint_key("svc1", "set_rungraph")
        decoded = decode_obj(payload)
        assert isinstance(decoded, dict)
        self.request_payloads.append(decoded)
        req_id = str(decoded.get("reqId") or "")
        asyncio.create_task(self._publish_apply_evidence(req_id), name="test_publish_rungraph_apply_evidence")
        if self.request_timeout:
            raise TimeoutError("query timed out")
        return encode_obj({"reqId": req_id, "ok": True, "result": {"graphId": "g1"}, "error": None})

    async def retained_put(self, key: str, value: bytes) -> None:
        key_s = str(key)
        self.retained[key_s] = bytes(value)
        callbacks = [cb for pattern, cb in list(self._watchers) if _match_retained_pattern(pattern, key_s)]
        for cb in callbacks:
            await cb(key_s, bytes(value))

    async def retained_get(self, key: str) -> bytes | None:
        return self.retained.get(str(key))

    async def retained_watch(
        self,
        key_expr: str,
        *,
        cb: Callable[[str, bytes], Awaitable[None]],
        with_initial: bool = True,
    ) -> object:
        key_s = str(key_expr)
        self._watchers.append((key_s, cb))
        if with_initial:
            for retained_key, value in list(self.retained.items()):
                if _match_retained_pattern(key_s, retained_key):
                    await cb(retained_key, bytes(value))
        return _WatchStub(self, key_s, cb)

    async def _publish_apply_evidence(self, req_id: str) -> None:
        await asyncio.sleep(0)
        request_payload = self.request_payloads[-1]
        args = request_payload.get("args")
        assert isinstance(args, dict)
        graph = args.get("graph")
        assert isinstance(graph, dict)
        self.status_fingerprint = build_rungraph_deploy_fingerprint(graph)
        meta = graph.get("meta")
        assert isinstance(meta, dict)
        deploy_source = str(meta.get("source") or "")
        request_meta = request_payload.get("meta")
        assert isinstance(request_meta, dict)
        target_fingerprint = str(request_meta.get("targetFingerprint") or "")
        assert target_fingerprint == self.status_fingerprint
        if self.publish_status:
            payload = {
                "schemaVersion": "f8.rungraphDeployStatus/2",
                "serviceId": "svc1",
                "reqId": req_id,
                "graphId": "g1",
                "revision": "r1",
                "phase": "applied",
                "ok": True,
                "source": deploy_source,
                "errorMessage": "",
                "ts": 1,
                "targetFingerprint": target_fingerprint,
                "appliedFingerprint": self.status_fingerprint,
                "runtimeInstanceId": self.apply_runtime_instance_id,
            }
            await self.retained_put(
                rungraph_deploy_request_status_key("svc1", req_id),
                encode_obj(payload),
            )
            await self.retained_put(rungraph_deploy_status_key("svc1"), encode_obj(payload))
            await self.retained_put("f8/svc/svc1/config/rungraph", encode_obj(graph))

    def remove_watch(self, key: str, cb: Callable[[str, bytes], Awaitable[None]]) -> None:
        try:
            self._watchers.remove((str(key), cb))
        except ValueError:
            return


class _ApplyingOnlyGatewayTransportStub(_GatewayTransportStub):
    async def _publish_apply_evidence(self, req_id: str) -> None:
        await asyncio.sleep(0)
        request_payload = self.request_payloads[-1]
        request_meta = request_payload.get("meta")
        assert isinstance(request_meta, dict)
        target_fingerprint = str(request_meta.get("targetFingerprint") or "")
        await self.retained_put(
            rungraph_deploy_request_status_key("svc1", req_id),
            encode_obj(
                {
                    "schemaVersion": "f8.rungraphDeployStatus/2",
                    "serviceId": "svc1",
                    "reqId": req_id,
                    "graphId": "g1",
                    "revision": "r1",
                    "phase": "applying",
                    "ok": True,
                    "source": "test",
                    "errorMessage": "",
                    "ts": 1,
                    "targetFingerprint": target_fingerprint,
                    "appliedFingerprint": "",
                    "runtimeInstanceId": "inst_svc1",
                }
            ),
        )


class _AppliedMismatchedFingerprintTransportStub(_GatewayTransportStub):
    async def _publish_apply_evidence(self, req_id: str) -> None:
        await asyncio.sleep(0)
        request_payload = self.request_payloads[-1]
        request_meta = request_payload.get("meta")
        assert isinstance(request_meta, dict)
        target_fingerprint = str(request_meta.get("targetFingerprint") or "")
        await self.retained_put(
            rungraph_deploy_request_status_key("svc1", req_id),
            encode_obj(
                {
                    "schemaVersion": "f8.rungraphDeployStatus/2",
                    "serviceId": "svc1",
                    "reqId": req_id,
                    "graphId": "g1",
                    "revision": "r1",
                    "phase": "applied",
                    "ok": True,
                    "source": "test",
                    "errorMessage": "",
                    "ts": 1,
                    "targetFingerprint": target_fingerprint,
                    "appliedFingerprint": "different-fingerprint",
                    "runtimeInstanceId": "inst_svc1",
                }
            ),
        )


class _NoEvidenceGatewayTransportStub(_GatewayTransportStub):
    async def _publish_apply_evidence(self, req_id: str) -> None:
        _ = req_id
        await asyncio.sleep(0)


class _RequestOnlyNoEvidenceTransportStub(_NoEvidenceGatewayTransportStub):
    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        if key == svc_endpoint_key("svc1", "status"):
            decoded_status = decode_obj(payload)
            assert isinstance(decoded_status, dict)
            return encode_obj(
                {
                    "reqId": str(decoded_status.get("reqId") or ""),
                    "ok": True,
                    "result": {
                        "serviceId": "svc1",
                        "serviceClass": "f8.tests.svc1",
                        "runtimeInstanceId": "inst_svc1",
                        "active": True,
                        "rungraphGraphId": "",
                        "rungraphRevision": "",
                        "rungraphFingerprint": "",
                    },
                    "error": None,
                }
            )
        return await super().request(key, payload, timeout=timeout, raise_on_error=raise_on_error)


class _RepublishConfigOnlyTransportStub(_RequestOnlyNoEvidenceTransportStub):
    async def _publish_apply_evidence(self, req_id: str) -> None:
        _ = req_id
        await asyncio.sleep(0)
        request_payload = self.request_payloads[-1]
        args = request_payload.get("args")
        assert isinstance(args, dict)
        graph = args.get("graph")
        assert isinstance(graph, dict)
        self.status_fingerprint = build_rungraph_deploy_fingerprint(graph)
        await self.retained_put("f8/svc/svc1/config/rungraph", encode_obj(graph))


class _ForceApplyConfigOnlyTransportStub(_RepublishConfigOnlyTransportStub):
    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        if key == svc_endpoint_key("svc1", "status"):
            self.status_probe_count += 1
            decoded_status = decode_obj(payload)
            assert isinstance(decoded_status, dict)
            return encode_obj(
                {
                    "reqId": str(decoded_status.get("reqId") or ""),
                    "ok": True,
                    "result": {
                        "serviceId": "svc1",
                        "serviceClass": "f8.tests.svc1",
                        "runtimeInstanceId": "inst_svc1",
                        "active": True,
                        "rungraphGraphId": "",
                        "rungraphRevision": "",
                        "rungraphFingerprint": "",
                    },
                    "error": None,
                }
            )
        return await super().request(key, payload, timeout=timeout, raise_on_error=raise_on_error)


class _ConfigGetFailsNoEvidenceTransportStub(_RequestOnlyNoEvidenceTransportStub):
    async def retained_get(self, key: str) -> bytes | None:
        if str(key).endswith("/config/rungraph"):
            raise RuntimeError("config retained_get failed")
        return await super().retained_get(key)


class _WatchOrderTransportStub(_GatewayTransportStub):
    def __init__(self) -> None:
        super().__init__()
        self.watch_count_before_set_rungraph = -1

    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        if key == svc_endpoint_key("svc1", "set_rungraph"):
            self.watch_count_before_set_rungraph = len(self._watchers)
        return await super().request(key, payload, timeout=timeout, raise_on_error=raise_on_error)


class _WatchStub:
    def __init__(
        self,
        transport: _GatewayTransportStub,
        key: str,
        cb: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        self._transport = transport
        self._key = str(key)
        self._cb = cb

    async def stop(self) -> None:
        self._transport.remove_watch(self._key, self._cb)


@dataclass(frozen=True)
class _DeployRequest:
    service_id: str
    graph: F8RuntimeGraph
    source: str
    force_apply: bool = False
    expected_runtime_instance_id: str = ""


def test_gateway_waits_for_rungraph_applied_status_after_ack() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub()
        ready_key = "f8/svc/svc1/status/ready"
        transport.retained[ready_key] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert result.error_message == ""
        assert len(transport.request_payloads) == 1
        assert transport.status_probe_count >= 1

    asyncio.run(_run())


def test_gateway_accepts_control_endpoint_when_ready_retained_status_is_missing() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub()
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert result.error_message == ""
        assert len(transport.request_payloads) == 1
        assert transport.status_probe_count >= 1

    asyncio.run(_run())


def test_gateway_uses_rungraph_status_when_ack_times_out() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub(request_timeout=True)
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert result.error_message == ""

    asyncio.run(_run())


def test_gateway_skips_set_rungraph_when_status_already_has_target() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub()
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        normalized = gateway._normalize_graph_for_request(graph, source="test:status-ready")
        transport.status_fingerprint = build_rungraph_deploy_fingerprint(normalized)
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(
            _DeployRequest(service_id="svc1", graph=graph, source="test:status-ready")
        )

        assert result.success is True
        assert result.error_message == ""
        assert transport.request_payloads == []

    asyncio.run(_run())


def test_gateway_force_apply_sends_set_rungraph_even_when_status_already_has_target() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub()
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        normalized = gateway._normalize_graph_for_request(graph, source="test:force")
        transport.status_fingerprint = build_rungraph_deploy_fingerprint(normalized)
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(
            _DeployRequest(service_id="svc1", graph=graph, source="test:force", force_apply=True)
        )

        assert result.success is True
        assert len(transport.request_payloads) == 1
        request_meta = transport.request_payloads[0].get("meta")
        assert isinstance(request_meta, dict)
        assert request_meta.get("forceApply") is True

    asyncio.run(_run())


def test_gateway_force_apply_requires_status_evidence_from_ready_runtime_instance() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub(apply_runtime_instance_id="old_inst")
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(
            _DeployRequest(service_id="svc1", graph=graph, source="test:force", force_apply=True)
        )

        assert result.success is False
        assert len(transport.request_payloads) == 1
        assert "unexpected runtime instance" in result.error_message
        assert "expectedRuntimeInstanceId=inst_svc1" in result.error_message
        assert "runtimeInstanceId=old_inst" in result.error_message

    asyncio.run(_run())


def test_gateway_respects_explicit_expected_runtime_instance_id() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub(
            status_runtime_instance_id="ready_inst",
            apply_runtime_instance_id="apply_inst",
        )
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(
            _DeployRequest(
                service_id="svc1",
                graph=graph,
                source="test:force",
                force_apply=True,
                expected_runtime_instance_id="apply_inst",
            )
        )

        assert result.success is True
        assert len(transport.request_payloads) == 1

    asyncio.run(_run())


def test_gateway_installs_evidence_watchers_before_set_rungraph_request() -> None:
    async def _run() -> None:
        transport = _WatchOrderTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert transport.watch_count_before_set_rungraph >= 2

    asyncio.run(_run())


def test_gateway_force_apply_ignores_config_republish_without_status_evidence() -> None:
    async def _run() -> None:
        transport = _ForceApplyConfigOnlyTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(
            _DeployRequest(service_id="svc1", graph=graph, source="test:force", force_apply=True)
        )

        assert result.success is False
        assert len(transport.request_payloads) == 1
        assert "expectedRuntimeInstanceId=inst_svc1" in result.error_message

    asyncio.run(_run())


def test_gateway_ignores_stale_retained_config_before_request() -> None:
    async def _run() -> None:
        transport = _RequestOnlyNoEvidenceTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        normalized = RuntimeRungraphGateway._normalize_graph_for_request(graph, source="test:stale")
        target_fingerprint = build_rungraph_deploy_fingerprint(normalized)
        transport.retained["f8/svc/svc1/config/rungraph"] = encode_obj(normalized)
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert len(transport.request_payloads) == 1
        assert target_fingerprint
        assert "rungraph apply status not received" in result.error_message

    asyncio.run(_run())


def test_gateway_ignores_stale_config_initial_watch_when_initial_get_fails() -> None:
    async def _run() -> None:
        transport = _ConfigGetFailsNoEvidenceTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        normalized = RuntimeRungraphGateway._normalize_graph_for_request(graph, source="test:stale")
        transport.retained["f8/svc/svc1/config/rungraph"] = encode_obj(normalized)
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert len(transport.request_payloads) == 1
        assert "rungraph apply status not received" in result.error_message

    asyncio.run(_run())


def test_gateway_accepts_config_republish_after_request() -> None:
    async def _run() -> None:
        transport = _RepublishConfigOnlyTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        normalized = RuntimeRungraphGateway._normalize_graph_for_request(graph, source="test:stale")
        transport.retained["f8/svc/svc1/config/rungraph"] = encode_obj(normalized)
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert result.error_message == ""
        assert len(transport.request_payloads) == 1

    asyncio.run(_run())


def test_gateway_ignores_stale_request_status_for_same_target() -> None:
    async def _run() -> None:
        transport = _NoEvidenceGatewayTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        normalized = RuntimeRungraphGateway._normalize_graph_for_request(graph, source="test:stale")
        target_fingerprint = build_rungraph_deploy_fingerprint(normalized)
        stale_payload = {
            "schemaVersion": "f8.rungraphDeployStatus/2",
            "serviceId": "svc1",
            "reqId": "old-request",
            "graphId": "g1",
            "revision": "r1",
            "phase": "applied",
            "ok": True,
            "source": "old",
            "errorMessage": "",
            "ts": 1,
            "targetFingerprint": target_fingerprint,
            "appliedFingerprint": target_fingerprint,
            "runtimeInstanceId": "old-instance",
        }
        transport.retained[rungraph_deploy_request_status_key("svc1", "old-request")] = encode_obj(stale_payload)
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert len(transport.request_payloads) == 1
        assert "rungraph apply status not received" in result.error_message

    asyncio.run(_run())


def test_gateway_reports_last_rungraph_apply_phase_on_timeout() -> None:
    async def _run() -> None:
        transport = _ApplyingOnlyGatewayTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert "last phase=applying" in result.error_message
        assert "f8/svc/svc1/status/rungraph/requests/" in result.error_message

    asyncio.run(_run())


def test_gateway_reports_applied_fingerprint_mismatch_on_timeout() -> None:
    async def _run() -> None:
        transport = _AppliedMismatchedFingerprintTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert "reported applied but fingerprint mismatched" in result.error_message
        assert "target=" in result.error_message
        assert "applied=different-finge" in result.error_message

    asyncio.run(_run())


def test_gateway_reports_request_status_key_when_apply_evidence_is_missing() -> None:
    async def _run() -> None:
        transport = _NoEvidenceGatewayTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=0.01))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert "rungraph apply status not received within 0.01s" in result.error_message
        assert "f8/svc/svc1/status/rungraph/requests/" in result.error_message

    asyncio.run(_run())


def test_gateway_includes_ack_failure_when_ack_and_apply_evidence_are_missing() -> None:
    async def _run() -> None:
        transport = _RequestOnlyNoEvidenceTransportStub(request_timeout=True)
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(_ready_payload())
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(
            RungraphDeployConfig(
                apply_timeout_s=0.01,
                request_attempts=1,
            )
        )
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is False
        assert "set_rungraph acknowledgement failed" in result.error_message
        assert "query timed out" in result.error_message
        assert "rungraph apply status not received within 0.01s" in result.error_message

    asyncio.run(_run())


def test_gateway_accepts_ready_payload_without_protocol_fields() -> None:
    async def _run() -> None:
        transport = _GatewayTransportStub()
        transport.retained["f8/svc/svc1/status/ready"] = encode_obj(
            {
                "serviceId": "svc1",
                "ready": True,
                "reason": "start",
                "ts": 1,
            }
        )
        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
            edges=[],
        )
        gateway = RuntimeRungraphGateway(RungraphDeployConfig(apply_timeout_s=1.0))
        gateway._transport = transport

        result = await gateway.deploy_runtime_graph(_DeployRequest(service_id="svc1", graph=graph, source="test"))

        assert result.success is True
        assert result.error_message == ""
        assert transport.status_probe_count >= 1

    asyncio.run(_run())


def _ready_payload() -> dict[str, object]:
    return {
        "serviceId": "svc1",
        "ready": True,
        "reason": "start",
        "ts": 1,
    }
