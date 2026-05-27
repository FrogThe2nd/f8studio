import asyncio
import os
import sys
import unittest

import msgspec

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.codec import decode_obj  # noqa: E402
from f8pysdk.codec import encode_obj  # noqa: E402
from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint  # noqa: E402
from f8pysdk.service_runtime_tools.deploy.readiness import (  # noqa: E402
    rungraph_deploy_request_status_key,
    rungraph_deploy_status_key,
    RungraphDeployStatusTimeout,
    wait_rungraph_deploy_status,
)
from f8pysdk.specs import (  # noqa: E402
    F8RuntimeGraph,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
)
from f8pysdk.specs import string_schema  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402


class RungraphApplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_rungraph_accepts_decoded_model(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        self.assertFalse(bus.has_rungraph())

        service_node = F8RuntimeNode(
            nodeId="svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=None,
            stateFields=[
                F8StateSpec(name="svcId", valueSchema=string_schema(), access=F8StateAccess.ro),
            ],
        )
        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[service_node], edges=[])

        await bus.set_rungraph(graph)
        self.assertIsNotNone(bus._graph)
        self.assertTrue(bus.has_rungraph())

    async def test_apply_rungraph_accepts_service_node_with_unset_operator_class(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")

        service_node = F8RuntimeNode(
            nodeId="svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=msgspec.UNSET,
            stateFields=[
                F8StateSpec(name="svcId", valueSchema=string_schema(), access=F8StateAccess.ro),
            ],
        )
        graph = F8RuntimeGraph(graphId="g2", revision="r1", nodes=[service_node], edges=[])

        await bus.set_rungraph(graph)
        self.assertIsNotNone(bus._graph)

    async def test_apply_rungraph_rejects_unset_service_node_id_mismatch(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")

        service_node = F8RuntimeNode(
            nodeId="not-svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=msgspec.UNSET,
            stateFields=[
                F8StateSpec(name="svcId", valueSchema=string_schema(), access=F8StateAccess.ro),
            ],
        )
        graph = F8RuntimeGraph(graphId="g3", revision="r1", nodes=[service_node], edges=[])

        with self.assertRaises(RuntimeError):
            await bus.set_rungraph(graph)

    async def test_submit_rungraph_publishes_retained_applied_status(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        service_node = F8RuntimeNode(
            nodeId="svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=None,
            stateFields=[],
        )
        graph = F8RuntimeGraph(graphId="g4", revision="r1", nodes=[service_node], edges=[])

        await bus.submit_rungraph(graph, req_id="req-apply", source="test")
        status = await wait_rungraph_deploy_status(
            bus._transport,
            service_id="svc",
            req_id="req-apply",
            graph_id="g4",
            revision="r1",
            timeout_s=1.0,
        )

        self.assertEqual(status.phase, "applied")
        self.assertTrue(status.ok)
        self.assertTrue(bus.has_rungraph())

    async def test_submit_rungraph_request_status_survives_generic_status_publish_failure(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        original_retained_put = bus._transport.retained_put

        async def _retained_put(key: str, value: bytes) -> None:
            if str(key) == rungraph_deploy_status_key("svc"):
                raise TimeoutError("generic status blocked")
            await original_retained_put(key, value)

        bus._transport.retained_put = _retained_put  # type: ignore[method-assign]
        service_node = F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)
        graph = F8RuntimeGraph(graphId="g-request-status", revision="r1", nodes=[service_node], edges=[])

        await bus.submit_rungraph(graph, req_id="req-request-status", source="test")
        status = await wait_rungraph_deploy_status(
            bus._transport,
            service_id="svc",
            req_id="req-request-status",
            graph_id="g-request-status",
            revision="r1",
            timeout_s=1.0,
        )

        self.assertEqual(status.phase, "applied")
        self.assertTrue(status.ok)

    async def test_submit_rungraph_publishes_accepted_status_before_apply_completes(self) -> None:
        class _SlowRungraphHook:
            async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph
                await asyncio.Event().wait()

            async def validate_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph
                return None

        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        bus.register_rungraph_hook(_SlowRungraphHook())
        service_node = F8RuntimeNode(
            nodeId="svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=None,
            stateFields=[],
        )
        graph = F8RuntimeGraph(graphId="g-accepted", revision="r1", nodes=[service_node], edges=[])

        await bus.submit_rungraph(graph, req_id="req-accepted", source="test")
        raw = None
        for _ in range(20):
            raw = await bus._transport.retained_get(rungraph_deploy_request_status_key("svc", "req-accepted"))
            if raw is not None:
                break
            await asyncio.sleep(0)
        payload = decode_obj(raw) if raw is not None else {}

        self.assertEqual(payload.get("phase"), "accepted")
        self.assertEqual(payload.get("reqId"), "req-accepted")

        for task in list(bus._rungraph_apply_tasks):
            task.cancel()
        await asyncio.gather(*list(bus._rungraph_apply_tasks), return_exceptions=True)

    async def test_submit_rungraph_returns_before_slow_status_publish(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        original_retained_put = bus._transport.retained_put
        slow_started = asyncio.Event()

        async def _slow_retained_put(key: str, value: bytes) -> None:
            if "status/rungraph" in str(key):
                slow_started.set()
                await asyncio.Event().wait()
            await original_retained_put(key, value)

        bus._transport.retained_put = _slow_retained_put  # type: ignore[method-assign]
        service_node = F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)
        graph = F8RuntimeGraph(graphId="g-fast-ack", revision="r1", nodes=[service_node], edges=[])

        await asyncio.wait_for(bus.submit_rungraph(graph, req_id="req-fast-ack", source="test"), timeout=0.1)
        await asyncio.wait_for(slow_started.wait(), timeout=0.1)

        for task in list(bus._rungraph_apply_tasks):
            task.cancel()
        await asyncio.gather(*list(bus._rungraph_apply_tasks), return_exceptions=True)

    async def test_status_endpoint_reports_current_rungraph_fingerprint(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        service_node = F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)
        graph = F8RuntimeGraph(graphId="g-status-fp", revision="r1", nodes=[service_node], edges=[])

        await bus.set_rungraph(graph)

        from f8pysdk.service_bus.internal.micro import ServiceBusControlHandlers
        from f8pysdk.specs import F8EmptyArgs, F8StatusRequest
        from f8pysdk.codec import encode_obj

        class _Req:
            data = encode_obj(F8StatusRequest(reqId="req-status", args=F8EmptyArgs(), meta={}))

            def __init__(self) -> None:
                self.response = b""

            async def respond(self, payload: bytes) -> None:
                self.response = bytes(payload)

        req = _Req()
        await ServiceBusControlHandlers(bus)._status(req)
        payload = decode_obj(req.response)
        result = payload.get("result")

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("rungraphGraphId"), "g-status-fp")
        self.assertEqual(result.get("rungraphRevision"), "r1")
        self.assertEqual(result.get("rungraphFingerprint"), build_rungraph_deploy_fingerprint(graph))

    async def test_submit_rungraph_aliases_same_target_without_duplicate_apply(self) -> None:
        class _CountingHook:
            def __init__(self) -> None:
                self.count = 0
                self.block = asyncio.Event()

            async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph
                self.count += 1
                await self.block.wait()

            async def validate_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph

        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        hook = _CountingHook()
        bus.register_rungraph_hook(hook)
        service_node = F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)
        graph = F8RuntimeGraph(graphId="g-alias", revision="r1", nodes=[service_node], edges=[])

        await bus.submit_rungraph(graph, req_id="req-a", source="test")
        await bus.submit_rungraph(graph, req_id="req-b", source="test")
        await asyncio.sleep(0)
        self.assertEqual(hook.count, 1)
        hook.block.set()

        status_a = await wait_rungraph_deploy_status(bus._transport, service_id="svc", req_id="req-a", timeout_s=1.0)
        status_b = await wait_rungraph_deploy_status(bus._transport, service_id="svc", req_id="req-b", timeout_s=1.0)
        self.assertEqual(status_a.phase, "applied")
        self.assertEqual(status_b.phase, "applied")

    async def test_submit_rungraph_clears_inflight_alias_after_cancel(self) -> None:
        class _BlockingHook:
            def __init__(self) -> None:
                self.count = 0
                self.block = asyncio.Event()

            async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph
                self.count += 1
                await self.block.wait()

            async def validate_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph

        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        hook = _BlockingHook()
        bus.register_rungraph_hook(hook)
        graph = F8RuntimeGraph(
            graphId="g-cancel-retry",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)],
            edges=[],
        )

        await bus.submit_rungraph(graph, req_id="req-cancel-a", source="test")
        await asyncio.sleep(0)
        tasks = list(bus._rungraph_apply_tasks)
        self.assertEqual(hook.count, 1)

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertNotIn(build_rungraph_deploy_fingerprint(graph), bus._rungraph_inflight_aliases)

        hook.block.set()
        await bus.submit_rungraph(graph, req_id="req-cancel-b", source="test")
        status = await wait_rungraph_deploy_status(
            bus._transport,
            service_id="svc",
            req_id="req-cancel-b",
            timeout_s=1.0,
        )

        self.assertEqual(status.phase, "applied")
        self.assertEqual(hook.count, 2)

    async def test_submit_rungraph_force_apply_reapplies_same_target(self) -> None:
        class _CountingHook:
            def __init__(self) -> None:
                self.count = 0

            async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph
                self.count += 1

            async def validate_rungraph(self, graph: F8RuntimeGraph) -> None:
                _ = graph

        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        hook = _CountingHook()
        bus.register_rungraph_hook(hook)
        graph = F8RuntimeGraph(
            graphId="g-force",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)],
            edges=[],
        )

        await bus.submit_rungraph(graph, req_id="req-first", source="test")
        await wait_rungraph_deploy_status(bus._transport, service_id="svc", req_id="req-first", timeout_s=1.0)
        await bus.submit_rungraph(graph, req_id="req-second", source="test")
        await wait_rungraph_deploy_status(bus._transport, service_id="svc", req_id="req-second", timeout_s=1.0)
        self.assertEqual(hook.count, 1)

        await bus.submit_rungraph(graph, req_id="req-force", source="test", force_apply=True)
        status = await wait_rungraph_deploy_status(bus._transport, service_id="svc", req_id="req-force", timeout_s=1.0)

        self.assertEqual(status.phase, "applied")
        self.assertEqual(hook.count, 2)

    async def test_wait_rungraph_deploy_status_requires_expected_runtime_instance(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        payload = {
            "schemaVersion": "f8.rungraphDeployStatus/2",
            "serviceId": "svc",
            "reqId": "req-wrong-runtime",
            "graphId": "g-runtime",
            "revision": "r1",
            "phase": "applied",
            "ok": True,
            "source": "test",
            "errorMessage": "",
            "ts": 1,
            "targetFingerprint": "fp-runtime",
            "appliedFingerprint": "fp-runtime",
            "runtimeInstanceId": "old_inst",
        }
        await bus._transport.retained_put(
            rungraph_deploy_request_status_key("svc", "req-wrong-runtime"),
            encode_obj(payload),
        )

        with self.assertRaises(RungraphDeployStatusTimeout):
            await wait_rungraph_deploy_status(
                bus._transport,
                service_id="svc",
                req_id="req-wrong-runtime",
                target_fingerprint="fp-runtime",
                expected_runtime_instance_id="new_inst",
                timeout_s=0.01,
            )

    async def test_wait_rungraph_deploy_status_requires_applied_fingerprint_when_target_given(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        payload = {
            "schemaVersion": "f8.rungraphDeployStatus/2",
            "serviceId": "svc",
            "reqId": "req-missing-applied",
            "graphId": "g-runtime",
            "revision": "r1",
            "phase": "applied",
            "ok": True,
            "source": "test",
            "errorMessage": "",
            "ts": 1,
            "targetFingerprint": "fp-runtime",
            "appliedFingerprint": "",
            "runtimeInstanceId": bus.runtime_instance_id,
        }
        await bus._transport.retained_put(
            rungraph_deploy_request_status_key("svc", "req-missing-applied"),
            encode_obj(payload),
        )

        with self.assertRaises(RungraphDeployStatusTimeout):
            await wait_rungraph_deploy_status(
                bus._transport,
                service_id="svc",
                req_id="req-missing-applied",
                target_fingerprint="fp-runtime",
                expected_runtime_instance_id=bus.runtime_instance_id,
                timeout_s=0.01,
            )

    async def test_submit_rungraph_rejects_req_id_reuse_with_different_target(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        graph_a = F8RuntimeGraph(
            graphId="g-reuse",
            revision="r1",
            nodes=[F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None)],
            edges=[],
        )
        graph_b = F8RuntimeGraph(
            graphId="g-reuse",
            revision="r1",
            nodes=[
                F8RuntimeNode(nodeId="svc", serviceId="svc", serviceClass="svc", operatorClass=None),
                F8RuntimeNode(nodeId="op", serviceId="svc", serviceClass="svc", operatorClass="svc.op"),
            ],
            edges=[],
        )

        await bus.submit_rungraph(graph_a, req_id="req-reuse", source="test")
        with self.assertRaises(ValueError):
            await bus.submit_rungraph(graph_b, req_id="req-reuse", source="test")

    async def test_submit_rungraph_publishes_retained_failed_status(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        service_node = F8RuntimeNode(
            nodeId="not-svc",
            serviceId="svc",
            serviceClass="svc",
            operatorClass=msgspec.UNSET,
            stateFields=[],
        )
        graph = F8RuntimeGraph(graphId="g5", revision="r1", nodes=[service_node], edges=[])

        await bus.submit_rungraph(graph, req_id="req-fail", source="test")
        status = await wait_rungraph_deploy_status(
            bus._transport,
            service_id="svc",
            req_id="req-fail",
            graph_id="g5",
            revision="r1",
            timeout_s=1.0,
        )
        raw = await bus._transport.retained_get(rungraph_deploy_status_key("svc"))
        payload = decode_obj(raw) if raw is not None else {}

        self.assertEqual(status.phase, "failed")
        self.assertFalse(status.ok)
        self.assertIn("set_rungraph", status.error_message)
        self.assertEqual(payload.get("phase"), "failed")


if __name__ == "__main__":
    unittest.main()
