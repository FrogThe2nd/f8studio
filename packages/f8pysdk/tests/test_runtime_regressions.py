from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode, F8ServiceSpec  # noqa: E402
from f8pysdk.nodes import ServiceNode  # noqa: E402
from f8pysdk.registry import (  # noqa: E402
    create_runtime_node_registry,
    OperatorFactoryNotRegistered,
    RuntimeNodeRegistry,
    shared_runtime_node_registry,
)
from f8pysdk.bus import DefaultServiceBusComponentFactory, ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.runtime import ServiceRuntime, ServiceRuntimeConfig  # noqa: E402
from f8pysdk.testing import InMemoryCluster, InMemoryTransport, ServiceBusHarness, push_input  # noqa: E402


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


class _NoopRungraphHook:
    async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
        _ = graph


class _NoopServiceHook:
    async def on_before_ready(self, bus: object) -> None:
        _ = bus

    async def on_after_ready(self, bus: object) -> None:
        _ = bus

    async def on_before_stop(self, bus: object) -> None:
        _ = bus

    async def on_after_stop(self, bus: object) -> None:
        _ = bus


class _ClosableServiceNode(ServiceNode):
    def __init__(self, node_id: str, events: list[str]) -> None:
        super().__init__(node_id=node_id)
        self._events = events

    async def close(self) -> None:
        self._events.append(f"node.close:{self.node_id}")


class _StuckCloseServiceNode(ServiceNode):
    def __init__(self, node_id: str, events: list[str]) -> None:
        super().__init__(node_id=node_id)
        self._events = events

    async def close(self) -> None:
        self._events.append(f"node.close.start:{self.node_id}")
        await asyncio.Event().wait()


class _RecordingComponentFactory(DefaultServiceBusComponentFactory):
    def __init__(self) -> None:
        self.created_data_router = None
        self.created_state_store = None
        self.created_state_router = None
        self.created_command_gateway = None
        self.created_monitor_collector = None

    def create_data_router(self, **kwargs: object):
        router = super().create_data_router(**kwargs)
        self.created_data_router = router
        return router

    def create_state_store(self, **kwargs: object):
        store = super().create_state_store(**kwargs)
        self.created_state_store = store
        return store

    def create_state_router(self, **kwargs: object):
        router = super().create_state_router(**kwargs)
        self.created_state_router = router
        return router

    def create_command_gateway(self, **kwargs: object):
        gateway = super().create_command_gateway(**kwargs)
        self.created_command_gateway = gateway
        return gateway

    def create_monitor_collector(self, **kwargs: object):
        collector = super().create_monitor_collector(**kwargs)
        self.created_monitor_collector = collector
        return collector


class RuntimeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_helpers_make_fresh_and_shared_semantics_explicit(self) -> None:
        original_instance = RuntimeNodeRegistry._instance
        RuntimeNodeRegistry._instance = create_runtime_node_registry()
        try:
            fresh_a = create_runtime_node_registry()
            fresh_b = create_runtime_node_registry()
            shared_a = shared_runtime_node_registry()
            shared_b = shared_runtime_node_registry()
        finally:
            RuntimeNodeRegistry._instance = original_instance

        self.assertIsNot(fresh_a, fresh_b)
        self.assertIs(shared_a, shared_b)

    async def test_service_runtime_defaults_to_fresh_registry_instances(self) -> None:
        cfg = ServiceRuntimeConfig(bus=ServiceBusConfig(service_id="svc", service_class="svc.test"))

        rt_a = ServiceRuntime(cfg)
        rt_b = ServiceRuntime(cfg)

        self.assertIsNot(rt_a._registry, rt_b._registry)

    async def test_service_runtime_uses_explicit_shared_registry(self) -> None:
        cfg = ServiceRuntimeConfig(bus=ServiceBusConfig(service_id="svc", service_class="svc.test"))
        registry = create_runtime_node_registry()

        rt_a = ServiceRuntime(cfg, registry=registry)
        rt_b = ServiceRuntime(cfg, registry=registry)

        self.assertIs(rt_a._registry, registry)
        self.assertIs(rt_b._registry, registry)

    async def test_service_bus_uses_explicit_component_factory(self) -> None:
        factory = _RecordingComponentFactory()

        bus = ServiceBus(
            ServiceBusConfig(service_id="svc"),
            component_factory=factory,
        )

        self.assertIs(bus.data_router, factory.created_data_router)
        self.assertIs(bus.state_store, factory.created_state_store)
        self.assertIs(bus.state_router, factory.created_state_router)
        self.assertIs(bus.command_gateway, factory.created_command_gateway)
        self.assertIs(bus.monitor_collector, factory.created_monitor_collector)

    async def test_callback_data_delivery_delivers_by_callback_and_pull(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster)
        bus = ServiceBus(ServiceBusConfig(service_id="svc", data_delivery="callback"), transport=transport)
        node = _DataReceiverNode("node1")
        bus.register_node(node)

        push_input(bus, "node1", "in", 123, ts_ms=5)
        await asyncio_sleep_ticks(2)
        pulled = await bus.pull_data("node1", "in")

        self.assertEqual(node.data_calls, [("in", 123, 5)])
        self.assertEqual(pulled, 123)

    async def test_missing_operator_factory_raises_explicit_error(self) -> None:
        registry = create_runtime_node_registry()
        registry.register_service_factory("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))

        with self.assertRaises(OperatorFactoryNotRegistered):
            registry.create_operator_node(
                node_id="op1",
                node=F8RuntimeNode(
                    nodeId="op1",
                    serviceId="svc",
                    serviceClass="svc.test",
                    operatorClass="missing.operator",
                ),
                initial_state={},
            )

    async def test_missing_service_factory_uses_generic_service_node_default(self) -> None:
        registry = create_runtime_node_registry()
        registry.register_service_spec(F8ServiceSpec(serviceClass="svc.test", label="svc"))

        node = registry.create_service_node(
            service_class="svc.test",
            node_id="svc",
            initial_state={},
        )

        self.assertIsInstance(node, ServiceNode)
        self.assertEqual(node.node_id, "svc")

    async def test_service_host_skips_operator_node_when_factory_is_missing(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        registry = create_runtime_node_registry()
        registry.register_service_factory("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))
        host = ServiceHost(bus, config=ServiceHostConfig(service_class="svc.test"), registry=registry)

        await host.start()
        await host.apply_rungraph(
            F8RuntimeGraph(
                graphId="g1",
                revision="r1",
                nodes=[
                    F8RuntimeNode(
                        nodeId="svc",
                        serviceId="svc",
                        serviceClass="svc.test",
                    ),
                    F8RuntimeNode(
                        nodeId="op1",
                        serviceId="svc",
                        serviceClass="svc.test",
                        operatorClass="missing.operator",
                    ),
                ],
                edges=[],
            )
        )

        self.assertIsNotNone(bus.get_node("svc"))
        self.assertIsNone(bus.get_node("op1"))

    async def test_service_host_derives_service_class_from_bus_config(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster)
        bus = ServiceBus(
            ServiceBusConfig(service_id="svc", service_class="svc.test"),
            transport=transport,
        )
        registry = create_runtime_node_registry()
        registry.register_service_factory("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))
        host = ServiceHost(bus, registry=registry)

        await host.start()

        self.assertIsNotNone(bus.get_node("svc"))

    async def test_service_bus_is_not_restartable_after_stop(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        with patch("f8pysdk.service_bus.workflow.lifecycle._ensure_control_endpoints_started") as ensure_control:
            async def _noop(_bus: object) -> None:
                return None

            ensure_control.side_effect = _noop
            await bus.start()
        await bus.stop()

        with self.assertRaisesRegex(RuntimeError, "not restartable"):
            await bus.start()

    async def test_service_runtime_is_not_restartable_after_stop(self) -> None:
        registry = create_runtime_node_registry()
        registry.register_service_factory("svc.test", lambda node_id, node, initial_state: ServiceNode(node_id=node_id))
        runtime = ServiceRuntime(
            ServiceRuntimeConfig(bus=ServiceBusConfig(service_id="svc", service_class="svc.test")),
            registry=registry,
        )
        runtime.bus.start = AsyncMock()
        runtime.bus.stop = AsyncMock()

        await runtime.start()
        await runtime.stop()

        with self.assertRaisesRegex(RuntimeError, "not restartable"):
            await runtime.start()

    async def test_service_runtime_stop_closes_host_nodes_before_bus_stop(self) -> None:
        events: list[str] = []
        registry = create_runtime_node_registry()
        registry.register_service_factory(
            "svc.test",
            lambda node_id, node, initial_state: _ClosableServiceNode(node_id=node_id, events=events),
        )
        runtime = ServiceRuntime(
            ServiceRuntimeConfig(bus=ServiceBusConfig(service_id="svc", service_class="svc.test")),
            registry=registry,
        )
        runtime.bus.start = AsyncMock()

        async def _stop_bus() -> None:
            events.append("bus.stop")

        runtime.bus.stop = AsyncMock(side_effect=_stop_bus)

        await runtime.start()
        await runtime.stop()

        self.assertEqual(events, ["node.close:svc", "bus.stop"])
        self.assertIsNone(runtime.host._service_node)

    async def test_service_host_stop_detaches_node_before_close(self) -> None:
        events: list[str] = []
        harness = ServiceBusHarness()
        bus = harness.create_bus("svc")
        registry = create_runtime_node_registry()
        registry.register_service_factory(
            "svc.test",
            lambda node_id, node, initial_state: _ClosableServiceNode(node_id=node_id, events=events),
        )
        host = ServiceHost(
            bus,
            config=ServiceHostConfig(service_class="svc.test", node_close_timeout_s=0.05),
            registry=registry,
        )

        await host.start()
        self.assertIsNotNone(bus.get_node("svc"))

        await host.stop()

        self.assertEqual(events, ["node.close:svc"])
        self.assertIsNone(bus.get_node("svc"))

    async def test_service_runtime_stop_still_stops_bus_when_node_close_times_out(self) -> None:
        events: list[str] = []
        registry = create_runtime_node_registry()
        registry.register_service_factory(
            "svc.test",
            lambda node_id, node, initial_state: _StuckCloseServiceNode(node_id=node_id, events=events),
        )
        runtime = ServiceRuntime(
            ServiceRuntimeConfig(bus=ServiceBusConfig(service_id="svc", service_class="svc.test")),
            registry=registry,
        )
        runtime.host._config = ServiceHostConfig(service_class="svc.test", node_close_timeout_s=0.05)
        runtime.bus.start = AsyncMock()

        async def _stop_bus() -> None:
            events.append("bus.stop")

        runtime.bus.stop = AsyncMock(side_effect=_stop_bus)

        await runtime.start()
        await runtime.stop()

        self.assertEqual(events, ["node.close.start:svc", "bus.stop"])
        self.assertIsNone(runtime.host._service_node)

    async def test_service_bus_stop_keeps_registered_hooks(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        rungraph_hook = _NoopRungraphHook()
        service_hook = _NoopServiceHook()
        bus.register_rungraph_hook(rungraph_hook)
        bus.register_service_hook(service_hook)

        with patch("f8pysdk.service_bus.workflow.lifecycle._ensure_control_endpoints_started") as ensure_control:
            async def _noop(_bus: object) -> None:
                return None

            ensure_control.side_effect = _noop
            await bus.start()
        await bus.stop()

        self.assertIn(rungraph_hook, bus._rungraph_hooks)
        self.assertIn(service_hook, bus._service_hooks)


async def asyncio_sleep_ticks(ticks: int) -> None:
    for _ in range(max(0, int(ticks))):
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
