import asyncio
import os
import socket
import struct
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

from f8pysdk.generated import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
)
from f8pysdk.nodes import OperatorNode  # noqa: E402
from f8pysdk.registry import create_runtime_node_registry  # noqa: E402
from f8pysdk.app import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.udp_vmc import UdpVmcRuntimeNode, register_operator as register_udp_vmc  # noqa: E402
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


def _osc_pad(raw: bytes) -> bytes:
    pad = (4 - (len(raw) & 0x03)) & 0x03
    return raw + (b"\x00" * pad)


def _osc_string(text: str) -> bytes:
    return _osc_pad(text.encode("utf-8") + b"\x00")


def _osc_message(address: str, args: list[Any]) -> bytes:
    tags = [","]
    payload = bytearray()
    for arg in args:
        if isinstance(arg, str):
            tags.append("s")
            payload.extend(_osc_string(arg))
        elif isinstance(arg, int):
            tags.append("i")
            payload.extend(struct.pack(">i", int(arg)))
        else:
            tags.append("f")
            payload.extend(struct.pack(">f", float(arg)))
    out = bytearray()
    out.extend(_osc_string(address))
    out.extend(_osc_string("".join(tags)))
    out.extend(payload)
    return bytes(out)


def _osc_bundle(messages: list[bytes]) -> bytes:
    out = bytearray()
    out.extend(b"#bundle\x00")
    out.extend(struct.pack(">Q", 0))
    for msg in messages:
        out.extend(struct.pack(">I", len(msg)))
        out.extend(msg)
    return bytes(out)


def _send_udp(*, port: int, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, ("127.0.0.1", int(port)))


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
        self.delay_s = 0.05

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id, in_port
        self.calls += 1
        await asyncio.sleep(float(self.delay_s))
        return []


class UdpVmcTests(unittest.IsolatedAsyncioTestCase):
    async def _setup_runtime(self, *, port: int) -> tuple[object, PyEngineService, _RuntimeStub]:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_pyengine_specs(reg)
        reg.register_operator_factory(
            SERVICE_CLASS,
            "f8.test_probe_exec_vmc",
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
            nodeId="udp_vmc",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=UdpVmcRuntimeNode.SPEC.operatorClass,
            stateFields=list(UdpVmcRuntimeNode.SPEC.stateFields or []),
            stateValues={
                "bindAddress": "127.0.0.1",
                "port": int(port),
                "maxQueue": 1024,
                "reuseAddress": False,
                "cleanupAfterMs": 10000,
                "selectedKey": "",
            },
            execInPorts=list(UdpVmcRuntimeNode.SPEC.execInPorts or []),
            execOutPorts=list(UdpVmcRuntimeNode.SPEC.execOutPorts or []),
            dataOutPorts=list(UdpVmcRuntimeNode.SPEC.dataOutPorts or []),
        )
        probe = F8RuntimeNode(
            nodeId="probe",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass="f8.test_probe_exec_vmc",
            execInPorts=["exec"],
            execOutPorts=[],
        )
        graph = F8RuntimeGraph(
            graphId="g_udp_vmc",
            revision="r1",
            nodes=[udp, probe],
            edges=[_exec_edge(edge_id="e1", from_node="udp_vmc", from_port="packet", to_node="probe", to_port="exec")],
        )
        await bus.set_rungraph(graph)  # type: ignore[attr-defined]

        runtime_ready = False
        for _ in range(100):
            node = bus.get_node("udp_vmc")
            if isinstance(node, UdpVmcRuntimeNode) and node._entrypoint_ctx is not None and node._drain_task is not None:
                runtime_ready = True
                break
            await asyncio.sleep(0.01)
        self.assertTrue(runtime_ready)

        return bus, service, runtime

    async def _teardown_runtime(self, service: PyEngineService, runtime: _RuntimeStub) -> None:
        bus = runtime.bus
        node = bus.get_node("udp_vmc")
        if isinstance(node, UdpVmcRuntimeNode):
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

    async def _selected_payload(self, bus: object) -> dict[str, Any] | None:
        node = bus.get_node("udp_vmc")
        self.assertIsInstance(node, UdpVmcRuntimeNode)
        assert isinstance(node, UdpVmcRuntimeNode)
        value = await node.compute_output("selectedSkeleton", ctx_id=None)
        if value is None:
            return None
        self.assertIsInstance(value, dict)
        assert isinstance(value, dict)
        return value

    async def test_decode_single_bone_message_to_skeleton_payload(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            msg = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 0.9])
            _send_udp(port=port, payload=msg)
            _ = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["modelName"], "VMC")
            self.assertEqual(payload["skeletonProtocol"], "unity_humanoid")
            self.assertEqual(int(payload["boneCount"]), 1)
            bone = payload["bones"][0]
            self.assertEqual(bone["name"], "Hips")
            rot = [float(v) for v in bone["rot"]]
            expected = [0.9, 0.1, 0.2, 0.3]
            norm = sum(v * v for v in expected) ** 0.5
            expected_norm = [v / norm for v in expected]
            dot = abs(sum(rot[i] * expected_norm[i] for i in range(4)))
            self.assertGreater(dot, 0.999)
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_packet_with_multiple_bones_commits_once(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            m1 = _osc_message("/VMC/Ext/Bone/Pos", ["A", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            m2 = _osc_message("/VMC/Ext/Bone/Pos", ["B", 1.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.9])
            _send_udp(port=port, payload=_osc_bundle([m1, m2]))
            probe = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            await asyncio.sleep(0.2)
            self.assertEqual(probe.calls, 1)
            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(int(payload["boneCount"]), 2)
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_partial_packet_updates_preserve_previous_bones(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            first = _osc_bundle(
                [
                    _osc_message("/VMC/Ext/Bone/Pos", ["A", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
                    _osc_message("/VMC/Ext/Bone/Pos", ["B", 5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
                ]
            )
            _send_udp(port=port, payload=first)
            _ = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)

            second = _osc_message("/VMC/Ext/Bone/Pos", ["A", 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0])
            _send_udp(port=port, payload=second)
            _ = await self._wait_probe_calls(bus, at_least=2, timeout_s=1.5)

            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            bones = {str(item["name"]): item for item in payload["bones"]}
            self.assertIn("A", bones)
            self.assertIn("B", bones)
            self.assertEqual([round(float(v), 3) for v in bones["A"]["pos"]], [1.0, 2.0, 3.0])
            self.assertEqual([round(float(v), 3) for v in bones["B"]["pos"]], [5.0, 0.0, 0.0])
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_non_vmc_messages_do_not_commit(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            msg = _osc_message("/noop", ["x", 1.0])
            _send_udp(port=port, payload=msg)
            await asyncio.sleep(0.3)
            probe = bus.get_node("probe")
            self.assertIsInstance(probe, _ProbeExecRuntimeNode)
            assert isinstance(probe, _ProbeExecRuntimeNode)
            self.assertEqual(probe.calls, 0)
            payload = await self._selected_payload(bus)
            self.assertIsNone(payload)
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_available_keys_and_selected_key_behavior(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            msg = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            _send_udp(port=port, payload=msg)
            _ = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            keys = await bus.get_state("udp_vmc", "availableKeys")
            self.assertTrue(keys.found)
            self.assertEqual(list(keys.value or []), ["VMC"])
            selected = await bus.get_state("udp_vmc", "selectedKey")
            self.assertTrue(selected.found)
            self.assertEqual(str(selected.value or ""), "VMC")
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_bind_security_matches_udp_skeleton(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_udp_vmc(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        op = F8RuntimeNode(
            nodeId="udp_vmc1",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=UdpVmcRuntimeNode.SPEC.operatorClass,
            stateFields=list(UdpVmcRuntimeNode.SPEC.stateFields or []),
            stateValues={"bindAddress": "127.0.0.1", "port": _free_udp_port()},
        )
        await bus.set_rungraph(F8RuntimeGraph(graphId="g_bind_vmc", revision="r1", nodes=[op], edges=[]))
        try:
            with self.assertRaises(ValueError):
                await bus.publish_state_external("udp_vmc1", "bindAddress", "0.0.0.0", source="test")
            await bus.publish_state_external("udp_vmc1", "allowNonLoopbackBind", True, source="test")
            await bus.publish_state_external("udp_vmc1", "bindAddress", "0.0.0.0", source="test")
        finally:
            node = bus.get_node("udp_vmc1")
            if isinstance(node, UdpVmcRuntimeNode):
                await node.close()

    async def test_deactivate_stops_emitting_exec(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            msg1 = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            _send_udp(port=port, payload=msg1)
            probe = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            self.assertEqual(probe.calls, 1)

            udp_node = bus.get_node("udp_vmc")
            self.assertIsInstance(udp_node, UdpVmcRuntimeNode)
            assert isinstance(udp_node, UdpVmcRuntimeNode)
            await udp_node.on_lifecycle(False, {"case": "deactivate"})

            msg2 = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            _send_udp(port=port, payload=msg2)
            await asyncio.sleep(0.4)
            self.assertEqual(probe.calls, 1)
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_root_and_ok_frame_sync(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            root_msg = _osc_message("/VMC/Ext/Root/Pos", ["root", 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            bone_msg = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0])
            ok_msg = _osc_message("/VMC/Ext/OK", [])

            _send_udp(port=port, payload=root_msg)
            _send_udp(port=port, payload=bone_msg)
            await asyncio.sleep(0.25)
            probe = bus.get_node("probe")
            self.assertIsInstance(probe, _ProbeExecRuntimeNode)
            assert isinstance(probe, _ProbeExecRuntimeNode)
            # Root alone does not commit; after first bone packet (before any OK seen), one commit can happen.
            pre_ok_calls = probe.calls
            self.assertGreaterEqual(pre_ok_calls, 1)

            _send_udp(port=port, payload=ok_msg)
            _ = await self._wait_probe_calls(bus, at_least=pre_ok_calls + 1, timeout_s=1.5)
            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            bones = {str(item["name"]): item for item in payload["bones"]}
            self.assertIn("Hips", bones)
            # Hips should be composed with root translation (10 + 1, 0 + 2, 0 + 3).
            self.assertEqual([round(float(v), 3) for v in bones["Hips"]["pos"]], [11.0, 2.0, 3.0])
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_fk_world_space_for_parent_child_chain(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            root_msg = _osc_message("/VMC/Ext/Root/Pos", ["root", 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            hips_msg = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            spine_msg = _osc_message("/VMC/Ext/Bone/Pos", ["Spine", 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            bundle = _osc_bundle([root_msg, hips_msg, spine_msg])
            _send_udp(port=port, payload=bundle)
            _ = await self._wait_probe_calls(bus, at_least=1, timeout_s=1.5)
            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            bones = {str(item["name"]): item for item in payload["bones"]}
            self.assertIn("Hips", bones)
            self.assertIn("Spine", bones)
            self.assertEqual([round(float(v), 3) for v in bones["Hips"]["pos"]], [11.0, 0.0, 0.0])
            self.assertEqual([round(float(v), 3) for v in bones["Spine"]["pos"]], [11.0, 1.0, 0.0])
        finally:
            await self._teardown_runtime(service, runtime)

    async def test_bone_packets_still_commit_after_ok_seen(self) -> None:
        port = _free_udp_port()
        bus, service, runtime = await self._setup_runtime(port=port)
        try:
            ok_msg = _osc_message("/VMC/Ext/OK", [])
            _send_udp(port=port, payload=ok_msg)
            await asyncio.sleep(0.2)

            probe = bus.get_node("probe")
            self.assertIsInstance(probe, _ProbeExecRuntimeNode)
            assert isinstance(probe, _ProbeExecRuntimeNode)
            baseline = int(probe.calls)

            bone_msg = _osc_message("/VMC/Ext/Bone/Pos", ["Hips", 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 1.0])
            _send_udp(port=port, payload=bone_msg)
            _ = await self._wait_probe_calls(bus, at_least=baseline + 1, timeout_s=1.5)
            payload = await self._selected_payload(bus)
            self.assertIsNotNone(payload)
            assert payload is not None
            bones = {str(item["name"]): item for item in payload["bones"]}
            self.assertIn("Hips", bones)
            self.assertEqual([round(float(v), 3) for v in bones["Hips"]["pos"]], [2.0, 3.0, 4.0])
        finally:
            await self._teardown_runtime(service, runtime)


if __name__ == "__main__":
    unittest.main()
