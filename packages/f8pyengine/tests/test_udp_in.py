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
    F8RuntimeGraph,
    F8RuntimeNode,
)
from f8pysdk.registry import create_runtime_node_registry  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.udp_in import UdpInRuntimeNode  # noqa: E402
from f8pyengine.pyengine_node_registry import register_pyengine_specs  # noqa: E402


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _send_udp(*, port: int, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, ("127.0.0.1", int(port)))


@dataclass
class _FakeEntrypointContext:
    calls: list[tuple[str, str]]

    def __init__(self) -> None:
        self.calls = []

    async def emit_exec(self, out_port: str, *, exec_id: str | int) -> None:
        self.calls.append((str(out_port), str(exec_id)))


class UdpInTests(unittest.IsolatedAsyncioTestCase):
    async def _setup_runtime(self, *, port: int, output_mode: str = "text") -> tuple[object, UdpInRuntimeNode, _FakeEntrypointContext]:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_pyengine_specs(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        udp = F8RuntimeNode(
            nodeId="udp_in",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=UdpInRuntimeNode.SPEC.operatorClass,
            stateFields=list(UdpInRuntimeNode.SPEC.stateFields or []),
            stateValues={
                "bindAddress": "127.0.0.1",
                "port": int(port),
                "maxQueue": 1024,
                "reuseAddress": False,
                "outputMode": output_mode,
            },
            execInPorts=list(UdpInRuntimeNode.SPEC.execInPorts or []),
            execOutPorts=list(UdpInRuntimeNode.SPEC.execOutPorts or []),
            dataOutPorts=list(UdpInRuntimeNode.SPEC.dataOutPorts or []),
        )
        graph = F8RuntimeGraph(
            graphId="g_udp_in",
            revision="r1",
            nodes=[udp],
            edges=[],
        )
        await bus.set_rungraph(graph)  # type: ignore[attr-defined]

        node = bus.get_node("udp_in")
        self.assertIsInstance(node, UdpInRuntimeNode)
        assert isinstance(node, UdpInRuntimeNode)
        ctx = _FakeEntrypointContext()
        await node.start_entrypoint(ctx)  # type: ignore[arg-type]
        await node.on_lifecycle(True, {"case": "test"})

        runtime_ready = False
        for _ in range(100):
            if node._entrypoint_ctx is not None and node._drain_task is not None:
                runtime_ready = True
                break
            await asyncio.sleep(0.01)
        self.assertTrue(runtime_ready)
        return bus, node, ctx

    async def _teardown_runtime(self, node: UdpInRuntimeNode) -> None:
        await node.close()

    async def _wait_exec_calls(self, ctx: _FakeEntrypointContext, *, at_least: int, timeout_s: float = 1.2) -> None:
        end = asyncio.get_running_loop().time() + timeout_s
        while True:
            if len(ctx.calls) >= at_least:
                return
            if asyncio.get_running_loop().time() >= end:
                self.fail(f"timed out waiting for exec calls >= {at_least}")
            await asyncio.sleep(0.01)

    async def _compute_output(self, node: UdpInRuntimeNode, port: str) -> Any:
        return await node.compute_output(port, ctx_id=f"ctx:{port}")

    async def test_json_packet_can_switch_from_text_to_json_via_state(self) -> None:
        port = _free_udp_port()
        bus, node, ctx = await self._setup_runtime(port=port, output_mode="text")
        try:
            payload = {"kind": "ping", "count": 3}
            _send_udp(port=port, payload=json.dumps(payload).encode("utf-8"))
            await self._wait_exec_calls(ctx, at_least=1, timeout_s=1.5)

            value_as_text = await self._compute_output(node, "value")
            self.assertEqual(value_as_text, json.dumps(payload))

            await bus.publish_state_external("udp_in", "outputMode", "json", source="test")
            value_as_json = await self._compute_output(node, "value")
            self.assertEqual(value_as_json, payload)

            json_value = await self._compute_output(node, "json")
            self.assertEqual(json_value, payload)

            packet = await self._compute_output(node, "packet")
            self.assertIsInstance(packet, dict)
            assert isinstance(packet, dict)
            self.assertEqual(packet["json"], payload)
            self.assertEqual(packet["value"], payload)
            self.assertEqual(packet["remoteAddress"], "127.0.0.1")
            self.assertGreater(int(packet["remotePort"]), 0)
        finally:
            await self._teardown_runtime(node)

    async def test_raw_output_preserves_non_ascii_bytes(self) -> None:
        port = _free_udp_port()
        bus, node, ctx = await self._setup_runtime(port=port, output_mode="bytearray")
        try:
            raw = bytes([0xFF, 0x00, 0x41, 0x80])
            _send_udp(port=port, payload=raw)
            await self._wait_exec_calls(ctx, at_least=1, timeout_s=1.5)

            value = await self._compute_output(node, "value")
            self.assertIsInstance(value, bytearray)
            self.assertEqual(bytes(value), raw)

            raw_out = await self._compute_output(node, "raw")
            self.assertIsInstance(raw_out, bytearray)
            self.assertEqual(bytes(raw_out), raw)

            text = await self._compute_output(node, "text")
            self.assertIsInstance(text, str)
            self.assertIn("A", text)
            self.assertIn("\ufffd", text)

            byte_length = await bus.get_state("udp_in", "lastByteLength")
            self.assertTrue(byte_length.found)
            self.assertEqual(int(byte_length.value or 0), len(raw))
        finally:
            await self._teardown_runtime(node)

    async def test_invalid_json_in_json_mode_falls_back_to_text_and_records_parse_error(self) -> None:
        port = _free_udp_port()
        bus, node, ctx = await self._setup_runtime(port=port, output_mode="json")
        try:
            payload = b"{bad json"
            _send_udp(port=port, payload=payload)
            await self._wait_exec_calls(ctx, at_least=1, timeout_s=1.5)

            value = await self._compute_output(node, "value")
            self.assertEqual(value, payload.decode("utf-8", errors="replace"))

            json_value = await self._compute_output(node, "json")
            self.assertIsNone(json_value)

            parse_error = await bus.get_state("udp_in", "lastParseError")
            self.assertTrue(parse_error.found)
            self.assertIn("JSONDecodeError", str(parse_error.value or ""))
        finally:
            await self._teardown_runtime(node)

    async def test_bind_security_rejects_non_loopback_by_default(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_pyengine_specs(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        op = F8RuntimeNode(
            nodeId="udp_in1",
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=UdpInRuntimeNode.SPEC.operatorClass,
            stateFields=list(UdpInRuntimeNode.SPEC.stateFields or []),
            stateValues={"bindAddress": "127.0.0.1", "port": _free_udp_port()},
        )
        await bus.set_rungraph(F8RuntimeGraph(graphId="g_udp_in_bind", revision="r1", nodes=[op], edges=[]))
        try:
            with self.assertRaises(ValueError):
                await bus.publish_state_external("udp_in1", "bindAddress", "0.0.0.0", source="test")

            await bus.publish_state_external("udp_in1", "allowNonLoopbackBind", True, source="test")
            await bus.publish_state_external("udp_in1", "bindAddress", "0.0.0.0", source="test")
        finally:
            node = bus.get_node("udp_in1")
            if isinstance(node, UdpInRuntimeNode):
                await node.close()


if __name__ == "__main__":
    unittest.main()
