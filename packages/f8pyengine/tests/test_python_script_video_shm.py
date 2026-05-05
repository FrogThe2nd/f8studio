import asyncio
import os
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode  # noqa: E402
from f8pysdk.registry import Registry, create_runtime_node_registry  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.shm.video import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16, VideoShmWriter  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402
from f8pysdk.video_transport import LatestVideoFrame, ZenohLatestVideoFrameTransport  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.python_script import PythonScriptRuntimeNode, register_operator  # noqa: E402


def _runtime_python_script_node(*, node_id: str, code: str) -> F8RuntimeNode:
    spec = PythonScriptRuntimeNode.SPEC
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId="svcA",
        serviceClass=SERVICE_CLASS,
        operatorClass=spec.operatorClass,
        execInPorts=list(spec.execInPorts or []),
        execOutPorts=list(spec.execOutPorts or []),
        dataInPorts=list(spec.dataInPorts or []),
        dataOutPorts=list(spec.dataOutPorts or []),
        stateFields=list(spec.stateFields or []),
        stateValues={"code": code},
        )


class _FakeLatestVideoTransport:
    def __init__(self, *, payload: bytes) -> None:
        self._payload = bytes(payload)
        self._delivered = False
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def publish_frame(
        self,
        *,
        width: int,
        height: int,
        pitch: int,
        payload: bytes | bytearray | memoryview,
        fmt: int,
        ts_ms: int | None = None,
    ) -> None:
        del width, height, pitch, payload, fmt, ts_ms
        raise RuntimeError("fake transport is subscriber-only")

    def poll_latest(self) -> LatestVideoFrame | None:
        if self._delivered:
            return None
        self._delivered = True
        return LatestVideoFrame(
            width=2,
            height=2,
            pitch=8,
            fmt=VIDEO_FORMAT_BGRA32,
            frame_id=41,
            ts_ms=1234,
            payload=memoryview(self._payload),
        )

    def wait_latest(self, timeout_ms: int) -> LatestVideoFrame | None:
        del timeout_ms
        return self.poll_latest()


class PythonScriptVideoShmTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_transport_normalization_is_zenoh_first(self) -> None:
        normalize = PythonScriptRuntimeNode._normalize_video_transport

        self.assertEqual(normalize("", video_key="", shm_name=""), "zenoh")
        self.assertEqual(normalize("", video_key="f8/test/video", shm_name=""), "zenoh")
        self.assertEqual(normalize("bad", video_key="", shm_name="shm.video"), "zenoh")
        self.assertEqual(normalize("legacy_shm", video_key="f8/test/video", shm_name=""), "legacy_shm")

    async def test_subscribe_video_latest_zenoh_uses_latest_transport(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(Registry.wrap(reg))
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        payload = bytes((i % 251 for i in range(16)))
        opened: list[tuple[str, dict[str, object]]] = []

        def _fake_open_subscriber(key_expr: str, **kwargs: object) -> _FakeLatestVideoTransport:
            opened.append((str(key_expr), dict(kwargs)))
            return _FakeLatestVideoTransport(payload=payload)

        code = (
            "def onStart(ctx):\n"
            "    ctx.subscribe_video_latest('video', video_key='f8/test/video', decode='none')\n\n"
            "async def onExec(ctx, exec_in, inputs):\n"
            "    pkt = ctx.get_video_latest('video')\n"
            "    items = ctx.list_video_latest_subscriptions()\n"
            "    if pkt is None:\n"
            "        return {'outputs': {'out': {'ok': False, 'items': items}}}\n"
            "    header = pkt.get('header') or {}\n"
            "    meta = pkt.get('meta') or {}\n"
            "    return {'outputs': {'out': {\n"
            "        'ok': True,\n"
            "        'frameId': int(header.get('frameId') or 0),\n"
            "        'rawLen': len(pkt.get('raw') or b''),\n"
            "        'transport': str(meta.get('transport') or ''),\n"
            "        'videoKey': str(meta.get('videoKey') or ''),\n"
            "        'hasShmName': 'shmName' in meta,\n"
            "        'itemTransport': str((items[0] if items else {}).get('transport') or ''),\n"
            "        'itemHasShmName': 'shmName' in (items[0] if items else {}),\n"
            "    }}}\n"
        )

        with patch.object(ZenohLatestVideoFrameTransport, "open_subscriber", side_effect=_fake_open_subscriber):
            op = _runtime_python_script_node(node_id="psv_latest", code=code)
            graph = F8RuntimeGraph(graphId="gv-latest", revision="r1", nodes=[op], edges=[])
            await bus.set_rungraph(graph)

            node = bus.get_node("psv_latest")
            self.assertIsInstance(node, PythonScriptRuntimeNode)
            assert isinstance(node, PythonScriptRuntimeNode)
            await asyncio.sleep(0.1)
            out = await node.compute_output("out", ctx_id="ctx-latest")
            self.assertIsInstance(out, dict)
            assert isinstance(out, dict)
            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(int(out.get("frameId") or 0), 41)
            self.assertEqual(int(out.get("rawLen") or 0), len(payload))
            self.assertEqual(str(out.get("transport") or ""), "zenoh")
            self.assertEqual(str(out.get("videoKey") or ""), "f8/test/video")
            self.assertFalse(bool(out.get("hasShmName")))
            self.assertEqual(str(out.get("itemTransport") or ""), "zenoh")
            self.assertFalse(bool(out.get("itemHasShmName")))
            self.assertEqual(opened[0][0], "f8/test/video")
            await node.close()

    async def test_subscribe_latest_and_decode_flow2_f16(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(Registry.wrap(reg))
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        shm_name = f"test.shm.ps.flow.{uuid.uuid4().hex}"
        writer = VideoShmWriter(shm_name=shm_name, size=1024 * 1024, slot_count=2)
        writer.open()
        try:
            code = (
                f"def onStart(ctx):\n"
                f"    ctx.subscribe_video_shm('flow', '{shm_name}', decode='auto')\n\n"
                f"async def onExec(ctx, exec_in, inputs):\n"
                f"    pkt = ctx.get_video_shm('flow')\n"
                f"    if pkt is None:\n"
                f"        return {{'outputs': {{'out': {{'frameId': 0}}}}}}\n"
                f"    dec = pkt.get('decoded')\n"
                f"    kind = dec.get('kind') if isinstance(dec, dict) else ''\n"
                f"    shape = dec.get('shape') if isinstance(dec, dict) else []\n"
                f"    h = pkt.get('header') if isinstance(pkt, dict) else {{}}\n"
                f"    return {{'outputs': {{'out': {{\n"
                f"        'frameId': int((h or {{}}).get('frameId') or 0),\n"
                f"        'kind': kind,\n"
                f"        'shape': shape,\n"
                f"        'rawLen': len(pkt.get('raw') or b''),\n"
                f"    }}}}}}\n"
            )
            op = _runtime_python_script_node(node_id="psv1", code=code)
            graph = F8RuntimeGraph(graphId="gv1", revision="r1", nodes=[op], edges=[])
            await bus.set_rungraph(graph)

            width = 3
            height = 2
            pitch = width * 4
            payload = bytes((i % 251 for i in range(pitch * height)))
            writer.write_frame(width=width, height=height, pitch=pitch, payload=payload, fmt=VIDEO_FORMAT_FLOW2_F16)

            node = bus.get_node("psv1")
            self.assertIsInstance(node, PythonScriptRuntimeNode)
            assert isinstance(node, PythonScriptRuntimeNode)
            await asyncio.sleep(0.1)
            out1 = await node.compute_output("out", ctx_id="ctx-a")
            self.assertIsInstance(out1, dict)
            assert isinstance(out1, dict)
            frame_id_1 = int(out1.get("frameId") or 0)
            self.assertGreater(frame_id_1, 0)
            self.assertEqual(str(out1.get("kind") or ""), "flow2_f16")
            self.assertEqual(list(out1.get("shape") or []), [2, 3, 2])
            self.assertEqual(int(out1.get("rawLen") or 0), int(len(payload)))

            out2 = await node.compute_output("out", ctx_id="ctx-b")
            self.assertIsInstance(out2, dict)
            assert isinstance(out2, dict)
            frame_id_2 = int(out2.get("frameId") or 0)
            self.assertEqual(frame_id_2, frame_id_1)
            await node.close()
        finally:
            writer.close(unlink=True)

    async def test_decode_none_returns_raw_only(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(Registry.wrap(reg))
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        shm_name = f"test.shm.ps.raw.{uuid.uuid4().hex}"
        writer = VideoShmWriter(shm_name=shm_name, size=1024 * 1024, slot_count=2)
        writer.open()
        try:
            code = (
                f"def onStart(ctx):\n"
                f"    ctx.subscribe_video_shm('video', '{shm_name}', decode='none')\n\n"
                f"async def onExec(ctx, exec_in, inputs):\n"
                f"    pkt = ctx.get_video_shm('video')\n"
                f"    if pkt is None:\n"
                f"        return {{'outputs': {{'out': {{'ok': False}}}}}}\n"
                f"    return {{'outputs': {{'out': {{\n"
                f"        'ok': True,\n"
                f"        'decodedIsNone': pkt.get('decoded') is None,\n"
                f"        'rawLen': len(pkt.get('raw') or b''),\n"
                f"    }}}}}}\n"
            )
            op = _runtime_python_script_node(node_id="psv2", code=code)
            graph = F8RuntimeGraph(graphId="gv2", revision="r1", nodes=[op], edges=[])
            await bus.set_rungraph(graph)

            width = 2
            height = 2
            pitch = width * 4
            payload = bytes((10 + i for i in range(pitch * height)))
            writer.write_frame_bgra(width=width, height=height, pitch=pitch, payload=payload)

            node = bus.get_node("psv2")
            self.assertIsInstance(node, PythonScriptRuntimeNode)
            assert isinstance(node, PythonScriptRuntimeNode)
            await asyncio.sleep(0.1)
            out = await node.compute_output("out", ctx_id="ctx-x")
            self.assertIsInstance(out, dict)
            assert isinstance(out, dict)
            self.assertTrue(bool(out.get("ok")))
            self.assertTrue(bool(out.get("decodedIsNone")))
            self.assertEqual(int(out.get("rawLen") or 0), int(len(payload)))
            await node.close()
        finally:
            writer.close(unlink=True)

    async def test_replace_subscription_same_key(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(Registry.wrap(reg))
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        shm_b = f"test.shm.ps.b.{uuid.uuid4().hex}"
        writer = VideoShmWriter(shm_name=shm_b, size=1024 * 1024, slot_count=2)
        writer.open()
        writer.write_frame_bgra(width=2, height=2, pitch=8, payload=bytes((i % 251 for i in range(16))))
        shm_a = f"test.shm.ps.a.{uuid.uuid4().hex}"
        code = (
            f"def onStart(ctx):\n"
            f"    ctx.subscribe_video_shm('k', '{shm_a}', decode='none')\n"
            f"    ctx.subscribe_video_shm('k', '{shm_b}', decode='none')\n\n"
            f"def onExec(ctx, exec_in, inputs):\n"
            f"    items = ctx.list_video_shm_subscriptions()\n"
            f"    return {{'outputs': {{'out': items}}}}\n"
        )
        try:
            op = _runtime_python_script_node(node_id="psv3", code=code)
            graph = F8RuntimeGraph(graphId="gv3", revision="r1", nodes=[op], edges=[])
            await bus.set_rungraph(graph)

            node = bus.get_node("psv3")
            self.assertIsInstance(node, PythonScriptRuntimeNode)
            assert isinstance(node, PythonScriptRuntimeNode)
            await asyncio.sleep(0.05)
            out = await node.compute_output("out", ctx_id="ctx-z")
            self.assertIsInstance(out, list)
            assert isinstance(out, list)
            self.assertEqual(len(out), 1)
            item = out[0]
            self.assertIsInstance(item, dict)
            assert isinstance(item, dict)
            self.assertEqual(str(item.get("key") or ""), "k")
            self.assertEqual(str(item.get("shmName") or ""), shm_b)
            await node.close()
        finally:
            writer.close(unlink=True)

    async def test_close_clears_video_subscriptions(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = create_runtime_node_registry()
        register_operator(Registry.wrap(reg))
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        shm_name = f"test.shm.ps.cleanup.{uuid.uuid4().hex}"
        writer = VideoShmWriter(shm_name=shm_name, size=1024 * 1024, slot_count=2)
        writer.open()
        writer.write_frame_bgra(width=2, height=2, pitch=8, payload=bytes((i % 251 for i in range(16))))
        code = (
            f"def onStart(ctx):\n"
            f"    ctx.subscribe_video_shm('k', '{shm_name}', decode='none')\n\n"
            f"def onExec(ctx, exec_in, inputs):\n"
            f"    return {{'outputs': {{'out': 1}}}}\n"
        )
        try:
            op = _runtime_python_script_node(node_id="psv4", code=code)
            graph = F8RuntimeGraph(graphId="gv4", revision="r1", nodes=[op], edges=[])
            await bus.set_rungraph(graph)

            node = bus.get_node("psv4")
            self.assertIsInstance(node, PythonScriptRuntimeNode)
            assert isinstance(node, PythonScriptRuntimeNode)
            await asyncio.sleep(0.05)
            self.assertGreaterEqual(len(node._video_subscriptions), 1)
            await node.close()
            self.assertEqual(len(node._video_subscriptions), 0)
        finally:
            writer.close(unlink=True)


if __name__ == "__main__":
    unittest.main()
