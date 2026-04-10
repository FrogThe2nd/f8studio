import asyncio
import os
import sys
import unittest
from typing import Any


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk.specs import F8DataPortSpec, F8Edge, F8EdgeKindEnum, F8EdgeStrategyEnum, F8RuntimeGraph, F8RuntimeNode  # noqa: E402
from f8pysdk.bus import ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.nats_naming import kv_bucket_for_service  # noqa: E402
from f8pysdk.registry import create_runtime_node_registry  # noqa: E402
from f8pysdk.specs import any_schema  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import InMemoryCluster, InMemoryTransport, ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.pull import PullRuntimeNode, register_operator  # noqa: E402


class _FixedComputableNode:
    def __init__(self, *, node_id: str, value: Any) -> None:
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


class PullNodeTests(unittest.IsolatedAsyncioTestCase):
    async def _build_bus_and_host(self) -> tuple[Any, ServiceHost]:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(reg)
        host = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)
        return bus, host

    async def _install_graph(self, bus: Any, *, enabled: bool, interval_ms: int) -> None:
        op = F8RuntimeNode(
            nodeId="pull1",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=PullRuntimeNode.SPEC.operatorClass,
            stateFields=list(PullRuntimeNode.SPEC.stateFields or []),
            stateValues={
                "autoTriggerEnabled": bool(enabled),
                "autoTriggerIntervalMs": int(interval_ms),
            },
            dataInPorts=[
                F8DataPortSpec(name="value", description="", valueSchema=any_schema(), required=False),
            ],
            dataOutPorts=[],
            execInPorts=["exec"],
            execOutPorts=[],
        )
        graph = F8RuntimeGraph(graphId="g_pull", revision="1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

    async def test_auto_trigger_periodically_pulls_inputs(self) -> None:
        bus, _host = await self._build_bus_and_host()
        await self._install_graph(bus, enabled=True, interval_ms=33)

        node = bus.get_node("pull1")
        self.assertIsInstance(node, PullRuntimeNode)
        assert isinstance(node, PullRuntimeNode)

        count = 0

        async def _count_pull(_port: str, *, ctx_id: str | int | None = None) -> Any:
            nonlocal count
            _ = ctx_id
            count += 1
            return None

        node.pull = _count_pull  # type: ignore[method-assign]
        await asyncio.sleep(0.22)
        self.assertGreaterEqual(count, 2)

    async def test_auto_trigger_disabled_does_not_pull(self) -> None:
        bus, _host = await self._build_bus_and_host()
        await self._install_graph(bus, enabled=False, interval_ms=33)

        node = bus.get_node("pull1")
        self.assertIsInstance(node, PullRuntimeNode)
        assert isinstance(node, PullRuntimeNode)

        count = 0

        async def _count_pull(_port: str, *, ctx_id: str | int | None = None) -> Any:
            nonlocal count
            _ = ctx_id
            count += 1
            return None

        node.pull = _count_pull  # type: ignore[method-assign]
        await asyncio.sleep(0.2)
        self.assertEqual(count, 0)

    async def test_lifecycle_pause_and_resume_periodic_pull(self) -> None:
        bus, _host = await self._build_bus_and_host()
        await self._install_graph(bus, enabled=True, interval_ms=50)

        node = bus.get_node("pull1")
        self.assertIsInstance(node, PullRuntimeNode)
        assert isinstance(node, PullRuntimeNode)
        self.assertIsNotNone(node._task)
        assert node._task is not None
        self.assertFalse(node._task.done())

        await node.on_lifecycle(False, {"case": "pause"})
        await asyncio.sleep(0.05)
        self.assertIsNone(node._task)

        await node.on_lifecycle(True, {"case": "resume"})
        await asyncio.sleep(0.05)
        self.assertIsNotNone(node._task)
        assert node._task is not None
        self.assertFalse(node._task.done())

    async def test_spec_is_hidden_and_has_no_exec_inputs(self) -> None:
        spec = PullRuntimeNode.SPEC
        self.assertEqual(spec.specKind, "operator")
        self.assertEqual(spec.paletteCategory, SERVICE_CLASS)
        self.assertTrue(spec.hiddenInPalette)
        self.assertEqual(list(spec.execInPorts or []), [])

    async def test_auto_trigger_republishes_sample_to_cross_service_consumers(self) -> None:
        cluster = InMemoryCluster()
        engine_bus = ServiceBus(
            ServiceBusConfig(service_id="svcA", data_delivery="buffered"),
            transport=InMemoryTransport(cluster=cluster, kv_bucket=kv_bucket_for_service("svcA")),
        )
        studio_bus = ServiceBus(
            ServiceBusConfig(service_id="studio", data_delivery="both"),
            transport=InMemoryTransport(cluster=cluster, kv_bucket=kv_bucket_for_service("studio")),
        )

        sample = {"value": 42, "label": "sampled"}
        source = _FixedComputableNode(node_id="src", value=sample)
        engine_bus.register_node(source)

        pull_runtime_node = F8RuntimeNode(
            nodeId="pull1",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=PullRuntimeNode.SPEC.operatorClass,
            stateFields=list(PullRuntimeNode.SPEC.stateFields or []),
            stateValues={
                "autoTriggerEnabled": True,
                "autoTriggerIntervalMs": 25,
                "publishCrossServiceOnly": True,
                "publishSourceNodeId": "src",
                "publishSourcePort": "out",
            },
            dataInPorts=[F8DataPortSpec(name="value", description="", valueSchema=any_schema(), required=False)],
            dataOutPorts=[],
            execInPorts=[],
            execOutPorts=[],
        )
        pull_node = PullRuntimeNode(
            node_id="pull1",
            node=pull_runtime_node,
            initial_state=dict(pull_runtime_node.stateValues or {}),
        )
        engine_bus.register_node(pull_node)

        graph = F8RuntimeGraph(
            graphId="g_pull_cross_publish",
            revision="1",
            nodes=[
                F8RuntimeNode(
                    nodeId="src",
                    serviceId="svcA",
                    serviceClass=SERVICE_CLASS,
                    operatorClass="f8.test.source",
                    dataInPorts=[],
                    dataOutPorts=[F8DataPortSpec(name="out", description="", valueSchema=any_schema(), required=False)],
                    execInPorts=[],
                    execOutPorts=[],
                ),
                pull_runtime_node,
                F8RuntimeNode(
                    nodeId="viz_text_1",
                    serviceId="studio",
                    serviceClass="f8.pystudio",
                    operatorClass="f8.viz.text",
                    dataInPorts=[F8DataPortSpec(name="inputData", description="", valueSchema=any_schema(), required=False)],
                    dataOutPorts=[],
                    execInPorts=[],
                    execOutPorts=[],
                ),
            ],
            edges=[
                F8Edge(
                    edgeId="edge_local_pull",
                    fromServiceId="svcA",
                    fromOperatorId="src",
                    fromPort="out",
                    toServiceId="svcA",
                    toOperatorId="pull1",
                    toPort="value",
                    kind=F8EdgeKindEnum.data,
                    strategy=F8EdgeStrategyEnum.latest,
                ),
                F8Edge(
                    edgeId="edge_cross_viz",
                    fromServiceId="svcA",
                    fromOperatorId="src",
                    fromPort="out",
                    toServiceId="studio",
                    toOperatorId="viz_text_1",
                    toPort="inputData",
                    kind=F8EdgeKindEnum.data,
                    strategy=F8EdgeStrategyEnum.latest,
                ),
            ],
        )

        try:
            await engine_bus.set_rungraph(graph)
            await studio_bus.set_rungraph(graph)
            await asyncio.sleep(0.12)

            pulled = await studio_bus.pull_data("viz_text_1", "inputData")
            self.assertEqual(pulled, sample)
            self.assertGreaterEqual(len(source.compute_calls), 1)
        finally:
            await pull_node.close()


if __name__ == "__main__":
    unittest.main()
