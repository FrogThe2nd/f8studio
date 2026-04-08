import asyncio
import json
import os
import socket
import sys
import unittest
from dataclasses import dataclass
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk.specs import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
)
from f8pysdk.nodes import OperatorNode  # noqa: E402
from f8pysdk.registry import create_runtime_node_registry  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.udp_skeleton import UdpSkeletonRuntimeNode  # noqa: E402
from f8pyengine.pyengine_node_registry import register_pyengine_specs  # noqa: E402
from f8pyengine.pyengine_service import PyEngineService  # noqa: E402


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _exec_edge(*, edge_id: str, from_node: str, from_port: str, to_node: str, to_port: str) -> F8Edge:
    return F8Edge(
        edgeId=edge_id,
        fromServiceId="svcA",
        fromOperatorId=from_node,
        fromPort=from_port,
        toServiceId="svcA",
        toOperatorId=to_node,
        toPort=to_port,
        kind=F8EdgeKindEnum.exec,
        strategy=F8EdgeStrategyEnum.latest,
    )


def _skeleton_payload(*, frame_id: int, model_name: str = "Model_A") -> dict[str, Any]:
    return {
        "type": "skeleton_binary",
        "modelName": model_name,
        "timestampMs": 1000 + int(frame_id),
        "schema": "f8.skeleton.v1",
        "boneCount": 1,
        "bones": [{"name": "root", "pos": [0, 0, 0], "rot": [1, 0, 0, 0]}],
        "trailer": {
            "magic": "LMEX",
            "extVersion": 1,
            "frameId": int(frame_id),
            "chunkIndex": 0,
            "chunkCount": 1,
            "totalBoneCount": 1,
            "characterId": 1,
        },
    }


def _send_udp_json(*, port: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(data, ("127.0.0.1", int(port)))


@dataclass
class _RuntimeStub:
    bus: object


class _ProbeExecRuntimeNode(OperatorNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        del initial_state
        super().__init__(
            node_id=node_id,
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
            exec_in_ports=list(node.execInPorts or []),
            exec_out_ports=list(node.execOutPorts or []),
        )
        self.calls = 0
        self.exec_ids: list[str] = []
        self.delay_s = 0.05

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = in_port
        self.calls += 1
        self.exec_ids.append(str(exec_id))
        await asyncio.sleep(float(self.delay_s))
        return []


class UdpSkeletonExecTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def _setup_runtime(self, *, port: int) -> tuple[object, PyEngineService, _RuntimeStub]:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_pyengine_specs(reg)
        reg.register_operator_factory(
            SERVICE_CLASS,
            "f8.test_probe_exec",
            lambda node_id, node, initial_state: _ProbeExecRuntimeNode(
                node_id=node_id, node=node, initial_state=initial_state
            ),
            overwrite=True,
        )
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)
        service = PyEngineService()
        runtime = _RuntimeStub(bus=bus)
        await service.setup(runtime)  # type: ignore[arg-type]

        udp = F8RuntimeNode(
            nodeId="udp_exec",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=UdpSkeletonRuntimeNode.SPEC.operatorClass,
            stateFields=list(UdpSkeletonRuntimeNode.SPEC.stateFields or []),
            stateValues={
                "bindAddress": "127.0.0.1",
                "port": int(port),
                "maxQueue": 1024,
                "reuseAddress": False,
                "cleanupAfterMs": 10000,
                "selectedKey": "",
            },
            execInPorts=list(UdpSkeletonRuntimeNode.SPEC.execInPorts or []),
            execOutPorts=list(UdpSkeletonRuntimeNode.SPEC.execOutPorts or []),
            dataOutPorts=list(UdpSkeletonRuntimeNode.SPEC.dataOutPorts or []),
        )
        probe = F8RuntimeNode(
            nodeId="probe",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass="f8.test_probe_exec",
            execInPorts=["exec"],
            execOutPorts=[],
        )
        graph = F8RuntimeGraph(
            graphId="g_udp_exec",
            revision="r1",
            nodes=[udp, probe],
            edges=[_exec_edge(edge_id="e1", from_node="udp_exec", from_port="packet", to_node="probe", to_port="exec")],
        )
        await bus.set_rungraph(graph)  # type: ignore[attr-defined]

        runtime_ready = False
        for _ in range(100):
            node = bus.get_node("udp_exec")
            if isinstance(node, UdpSkeletonRuntimeNode) and node._entrypoint_ctx is not None and node._drain_task is not None:
                runtime_ready = True
                break
            await asyncio.sleep(0.01)
        self.assertTrue(runtime_ready)

        return bus, service, runtime

    async def _teardown_runtime(self, service: PyEngineService, runtime: _RuntimeStub) -> None:
        bus = runtime.bus
        node = bus.get_node("udp_exec")
        if isinstance(node, UdpSkeletonRuntimeNode):
            await node.close()
        await service.teardown(runtime)  # type: ignore[arg-type]

    async def _wait_probe_calls(self, bus: object, *, at_least: int, timeout_s: float = 1.2) -> _ProbeExecRuntimeNode:
        end = asyncio.get_running_loop().time() + timeout_s
        while True:
            node = bus.get_node("probe")
            if isinstance(node, _ProbeExecRuntimeNode) and node.calls >= at_least:
                return node
            if asyncio.get_running_loop().time() >= end:
                self.fail(f"timed out waiting for probe calls >= {at_least}")
            await asyncio.sleep(0.01)

    async def test_packet_commit_triggers_single_exec(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            _send_udp_json(port=port, payload=_skeleton_payload(frame_id=1))
            probe = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            self.assertEqual(probe.calls, 1)

            keys = await bus.get_state("udp_exec", "availableKeys")
            self.assertTrue(keys.found)
            self.assertEqual(list(keys.value or []), ["Model_A"])
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_packet_burst_coalesces_pending_trigger(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            probe = bus.get_node("probe")
            self.assertIsInstance(probe, _ProbeExecRuntimeNode)
            assert isinstance(probe, _ProbeExecRuntimeNode)
            probe.delay_s = 0.08

            for frame_id in range(1, 41):
                _send_udp_json(port=port, payload=_skeleton_payload(frame_id=frame_id))

            # Wait until call count settles, instead of sleeping a fixed long window.
            end = asyncio.get_running_loop().time() + 2.0
            last_calls = -1
            stable_ticks = 0
            while asyncio.get_running_loop().time() < end:
                current = probe.calls
                if current > 0 and current == last_calls:
                    stable_ticks += 1
                    if stable_ticks >= 5:
                        break
                else:
                    stable_ticks = 0
                    last_calls = current
                await asyncio.sleep(0.02)
            self.assertGreater(probe.calls, 0)
            self.assertLess(probe.calls, 40)
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_deactivate_stops_emitting_exec(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            _send_udp_json(port=port, payload=_skeleton_payload(frame_id=1))
            probe = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            self.assertEqual(probe.calls, 1)

            udp_node = bus.get_node("udp_exec")
            self.assertIsInstance(udp_node, UdpSkeletonRuntimeNode)
            assert isinstance(udp_node, UdpSkeletonRuntimeNode)
            await udp_node.on_lifecycle(False, {"case": "deactivate"})
            _send_udp_json(port=port, payload=_skeleton_payload(frame_id=2))
            _send_udp_json(port=port, payload=_skeleton_payload(frame_id=3))
            await asyncio.sleep(0.4)
            self.assertEqual(probe.calls, 1)
        finally:
            await self._teardown_runtime(service, runtime)


if __name__ == "__main__":
    unittest.main()
