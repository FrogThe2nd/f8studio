from __future__ import annotations

import asyncio
import os
import sys
import unittest
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.specs import F8Edge, F8EdgeKindEnum, F8EdgeStrategyEnum, F8RuntimeGraph, F8RuntimeNode  # noqa: E402
from f8pysdk.f8_naming import data_subject  # noqa: E402
from f8pysdk.bus import ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.service_bus.data.emit import DataEmitOptions  # noqa: E402
from f8pysdk.testing import InMemoryCluster, InMemoryTransport, push_input  # noqa: E402


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


class _ComputableNode:
    def __init__(self, node_id: str, value: Any) -> None:
        self.node_id = node_id
        self._value = value
        self.compute_calls: list[tuple[str, str | int | None]] = []

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

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        self.compute_calls.append((str(port), ctx_id))
        return self._value


def _runtime_node(*, node_id: str, service_id: str, data_in: list[str] | None = None, data_out: list[str] | None = None) -> F8RuntimeNode:
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId=service_id,
        serviceClass=service_id,
        operatorClass=f"data_flow.{node_id}",
        dataInPorts=list(data_in or []),
        dataOutPorts=list(data_out or []),
    )


def _data_edge(
    *,
    edge_id: str,
    from_service: str,
    from_node: str,
    from_port: str,
    to_service: str,
    to_node: str,
    to_port: str,
) -> F8Edge:
    return F8Edge(
        edgeId=edge_id,
        fromServiceId=from_service,
        fromOperatorId=from_node,
        fromPort=from_port,
        toServiceId=to_service,
        toOperatorId=to_node,
        toPort=to_port,
        kind=F8EdgeKindEnum.data,
        strategy=F8EdgeStrategyEnum.latest,
    )


async def _sleep_ticks(ticks: int) -> None:
    for _ in range(max(0, int(ticks))):
        await asyncio.sleep(0)


class DataFlowRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_cross_publish_policy_is_routed(self) -> None:
        cluster = InMemoryCluster()
        transport = _RecordingTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc"), transport=transport)

        await bus.emit_data("node1", "out", {"x": 1}, ts_ms=1)

        self.assertEqual(bus.cross_publish_policy, "routed")
        self.assertEqual(transport.published_subjects, [])

    async def test_callback_delivery_invokes_callback_and_keeps_pull_buffer(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="callback"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        push_input(bus, "node1", "in", 123, ts_ms=5)
        await _sleep_ticks(2)
        pulled = await bus.pull_data("node1", "in")

        self.assertEqual(node.data_calls, [("in", 123, 5)])
        self.assertEqual(pulled, 123)

    async def test_buffered_delivery_keeps_pull_buffer_without_callback(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="buffered"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        push_input(bus, "node1", "in", 123, ts_ms=5)
        await _sleep_ticks(2)
        pulled = await bus.pull_data("node1", "in")

        self.assertEqual(node.data_calls, [])
        self.assertEqual(pulled, 123)

    async def test_legacy_data_delivery_aliases_fail_fast(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")

        for mode in ("push", "pull", "both"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    ServiceBus(ServiceBusConfig(service_id="svc", data_delivery=mode), transport=transport)

    async def test_pending_push_callbacks_are_dropped_when_bus_deactivates(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="callback"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        push_input(bus, "node1", "in", 123, ts_ms=5)
        await bus.set_active(False)
        await _sleep_ticks(2)

        self.assertEqual(node.data_calls, [])

    async def test_push_input_is_ignored_while_bus_inactive(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="callback"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        await bus.set_active(False)
        push_input(bus, "node1", "in", 123, ts_ms=5)
        await _sleep_ticks(2)

        self.assertEqual(node.data_calls, [])

    async def test_pull_triggered_compute_stays_local_even_when_cross_publish_is_all(self) -> None:
        cluster = InMemoryCluster()
        transport = _RecordingTransport(cluster=cluster, kv_bucket="kv.svcA")
        bus = ServiceBus(
            ServiceBusConfig(service_id="svcA", cross_publish_policy="all", data_delivery="buffered"),
            transport=transport,
        )
        source = _ComputableNode("src", 42)
        bus.register_node(source)

        graph = F8RuntimeGraph(
            graphId="g-data-flow-routing",
            revision="r1",
            nodes=[
                _runtime_node(node_id="src", service_id="svcA", data_out=["out"]),
                _runtime_node(node_id="local_dst", service_id="svcA", data_in=["in"]),
                _runtime_node(node_id="remote_dst", service_id="svcB", data_in=["in"]),
            ],
            edges=[
                _data_edge(
                    edge_id="local",
                    from_service="svcA",
                    from_node="src",
                    from_port="out",
                    to_service="svcA",
                    to_node="local_dst",
                    to_port="in",
                ),
                _data_edge(
                    edge_id="remote",
                    from_service="svcA",
                    from_node="src",
                    from_port="out",
                    to_service="svcB",
                    to_node="remote_dst",
                    to_port="in",
                ),
            ],
        )

        await bus.set_rungraph(graph)
        pulled = await bus.pull_data("local_dst", "in", ctx_id="ctx-1")

        self.assertEqual(pulled, 42)
        self.assertEqual(source.compute_calls, [("out", "ctx-1")])
        self.assertEqual(transport.published_subjects, [])
        self.assertEqual(bus.data_router.input_buffers[("local_dst", "in")].last_seen_value, 42)

    async def test_cross_publish_policy_all_publishes_without_cross_routes(self) -> None:
        cluster = InMemoryCluster()
        transport = _RecordingTransport(cluster=cluster, kv_bucket="kv.svc")
        bus = ServiceBus(ServiceBusConfig(service_id="svc", cross_publish_policy="all"), transport=transport)

        await bus.emit_data("node1", "out", {"x": 1}, ts_ms=1)

        self.assertEqual(bus.cross_publish_policy, "all")
        self.assertEqual(
            transport.published_subjects,
            [data_subject("svc", from_node_id="node1", port_id="out")],
        )

    async def test_data_emit_options_can_force_local_only_emit_without_cross_publish(self) -> None:
        cluster = InMemoryCluster()
        transport = _RecordingTransport(cluster=cluster, kv_bucket="kv.svcA")
        bus = ServiceBus(
            ServiceBusConfig(service_id="svcA", cross_publish_policy="all", data_delivery="buffered"),
            transport=transport,
        )

        graph = F8RuntimeGraph(
            graphId="g-local-only-emit",
            revision="r1",
            nodes=[
                _runtime_node(node_id="src", service_id="svcA", data_out=["out"]),
                _runtime_node(node_id="local_dst", service_id="svcA", data_in=["in"]),
                _runtime_node(node_id="remote_dst", service_id="svcB", data_in=["in"]),
            ],
            edges=[
                _data_edge(
                    edge_id="local",
                    from_service="svcA",
                    from_node="src",
                    from_port="out",
                    to_service="svcA",
                    to_node="local_dst",
                    to_port="in",
                ),
                _data_edge(
                    edge_id="remote",
                    from_service="svcA",
                    from_node="src",
                    from_port="out",
                    to_service="svcB",
                    to_node="remote_dst",
                    to_port="in",
                ),
            ],
        )

        await bus.set_rungraph(graph)
        await bus.data_router.emit_data(
            "src",
            "out",
            99,
            ts_ms=11,
            options=DataEmitOptions.local_compute_only(),
        )

        self.assertEqual(transport.published_subjects, [])
        self.assertEqual(bus.data_router.input_buffers[("local_dst", "in")].last_seen_value, 99)


if __name__ == "__main__":
    unittest.main()
