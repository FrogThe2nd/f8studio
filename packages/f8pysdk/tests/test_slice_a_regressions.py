from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.generated import F8RuntimeNode  # noqa: E402
from f8pysdk.nats_naming import data_subject  # noqa: E402
from f8pysdk.runtime_node import OperatorNode, ServiceNode  # noqa: E402
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry  # noqa: E402
from f8pysdk.service_bus.bus import ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.service_runtime import ServiceRuntime, ServiceRuntimeConfig  # noqa: E402
from f8pysdk.testing import InMemoryCluster, InMemoryTransport, ServiceBusHarness, push_input  # noqa: E402


class _RecordingTransport(InMemoryTransport):
    def __init__(self, *, cluster: InMemoryCluster, kv_bucket: str) -> None:
        super().__init__(cluster=cluster, kv_bucket=kv_bucket)
        self.published_subjects: list[str] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published_subjects.append(str(subject))
        await super().publish(subject, payload)


class _DataReceiverNode:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.data_calls: list[tuple[str, object, int | None]] = []

    def attach(self, bus: object) -> None:
        self._bus = bus

    async def validate_state(self, field: str, value: object, *, ts_ms: int, meta: dict[str, object]) -> object:
        _ = field
        _ = ts_ms
        _ = meta
        return value

    async def on_state(self, field: str, value: object, *, ts_ms: int | None = None) -> None:
        _ = field
        _ = value
        _ = ts_ms
        return

    async def on_data(self, port: str, value: object, *, ts_ms: int | None = None) -> None:
        self.data_calls.append((str(port), value, ts_ms))


class SliceARegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_runtime_defaults_to_fresh_registry_instances(self) -> None:
        cfg = ServiceRuntimeConfig.from_values(service_id="svc", service_class="svc.test")

        rt_a = ServiceRuntime(cfg)
        rt_b = ServiceRuntime(cfg)

        self.assertIsNot(rt_a._registry, rt_b._registry)

    async def test_service_runtime_uses_explicit_shared_registry(self) -> None:
        cfg = ServiceRuntimeConfig.from_values(service_id="svc", service_class="svc.test")
        registry = RuntimeNodeRegistry()

        rt_a = ServiceRuntime(cfg, registry=registry)
        rt_b = ServiceRuntime(cfg, registry=registry)

        self.assertIs(rt_a._registry, registry)
        self.assertIs(rt_b._registry, registry)

    async def test_publish_all_data_publishes_without_cross_routes(self) -> None:
        cluster = InMemoryCluster()
        transport = _RecordingTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", publish_all_data=True), transport=transport)

        await bus.emit_data("node1", "out", {"x": 1}, ts_ms=1)

        self.assertEqual(
            transport.published_subjects,
            [data_subject("svc", from_node_id="node1", port_id="out")],
        )

    async def test_data_delivery_both_delivers_by_callback_and_pull(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="both"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        push_input(bus, "node1", "in", 123, ts_ms=5)
        await asyncio_sleep_ticks(2)
        pulled = await bus.pull_data("node1", "in")

        self.assertEqual(node.data_calls, [("in", 123, 5)])
        self.assertEqual(pulled, 123)

    async def test_missing_operator_factory_falls_back_to_generic_operator_node(self) -> None:
        registry = RuntimeNodeRegistry()
        registry.register_service("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))

        node = registry.create(
            node_id="op1",
            node=F8RuntimeNode(
                nodeId="op1",
                serviceId="svc",
                serviceClass="svc.test",
                operatorClass="missing.operator",
            ),
            initial_state={},
        )

        self.assertIsInstance(node, OperatorNode)
        self.assertEqual(node.node_id, "op1")

    async def test_service_bus_is_not_restartable_after_stop(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        with patch("f8pysdk.service_bus.lifecycle._ensure_micro_endpoints_started") as ensure_micro:
            async def _noop(_bus: object) -> None:
                return None

            ensure_micro.side_effect = _noop
            await bus.start()
        await bus.stop()

        with self.assertRaisesRegex(RuntimeError, "not restartable"):
            await bus.start()

    async def test_service_runtime_is_not_restartable_after_stop(self) -> None:
        registry = RuntimeNodeRegistry()
        registry.register_service("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))
        runtime = ServiceRuntime(
            ServiceRuntimeConfig.from_values(service_id="svc", service_class="svc.test"),
            registry=registry,
        )
        runtime.bus.start = AsyncMock()
        runtime.bus.stop = AsyncMock()

        await runtime.start()
        await runtime.stop()

        with self.assertRaisesRegex(RuntimeError, "not restartable"):
            await runtime.start()


async def asyncio_sleep_ticks(ticks: int) -> None:
    for _ in range(max(0, int(ticks))):
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
