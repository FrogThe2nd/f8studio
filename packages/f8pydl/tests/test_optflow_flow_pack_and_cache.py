import os
import sys
import unittest
from types import SimpleNamespace
from typing import Any


PKG_PYDL = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_PYDL, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)


from f8pydl.optflow_service_node import (  # noqa: E402
    OnnxOptflowServiceNode,
    OptflowFramePairCache,
    PreparedFlowFrame,
    pack_flow2_f16_payload,
)
from f8pysdk.state import StateRead  # noqa: E402


class _BusStub:
    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(initial_state or {})
        self.errors: list[tuple[str, str, str]] = []
        self.clear_count = 0

    def report_error(
        self,
        node_id: str,
        code: str,
        message: str,
        severity: str = "error",
        fingerprint: str | None = None,
        ts_ms: int | None = None,
    ) -> None:
        del severity, fingerprint, ts_ms
        self.errors.append((str(node_id), str(code), str(message)))

    def clear_error(self, node_id: str, fingerprint: str | None = None, ts_ms: int | None = None) -> None:
        del node_id, fingerprint, ts_ms
        self.clear_count += 1

    async def publish_state_runtime(self, node_id: str, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        del node_id, field, value, ts_ms

    async def get_state(self, node_id: str, field: str) -> StateRead:
        del node_id
        if field in self.state:
            return StateRead(found=True, value=self.state[field], ts_ms=None)
        return StateRead(found=False, value=None, ts_ms=None)


class OptflowPackAndCacheTests(unittest.TestCase):
    def test_pack_flow2_f16_payload_roundtrip(self) -> None:
        import numpy as np  # type: ignore

        flow = np.asarray(
            [
                [[1.25, -2.5], [0.0, 3.125]],
                [[-4.0, 5.5], [6.0, -7.75]],
            ],
            dtype=np.float32,
        )
        pitch, payload = pack_flow2_f16_payload(flow)
        self.assertEqual(pitch, 8)
        self.assertEqual(len(payload), 16)

        decoded = np.frombuffer(payload, dtype=np.float16).reshape((2, 2, 2)).astype(np.float32)
        self.assertTrue(np.allclose(decoded, flow, atol=1e-2))

    def test_frame_pair_cache_reuses_previous_tensor(self) -> None:
        cache = OptflowFramePairCache()
        tensor1 = object()
        tensor2 = object()
        tensor3 = object()
        f1 = PreparedFlowFrame(frame_id=1, width=640, height=480, tensor=tensor1)
        f2 = PreparedFlowFrame(frame_id=2, width=640, height=480, tensor=tensor2)
        f3 = PreparedFlowFrame(frame_id=3, width=640, height=480, tensor=tensor3)

        pair1 = cache.push_and_get_pair(f1)
        self.assertIsNone(pair1)

        pair2 = cache.push_and_get_pair(f2)
        assert pair2 is not None
        self.assertIs(pair2[0].tensor, tensor1)
        self.assertIs(pair2[1].tensor, tensor2)

        pair3 = cache.push_and_get_pair(f3)
        assert pair3 is not None
        self.assertIs(pair3[0].tensor, tensor2)
        self.assertIs(pair3[1].tensor, tensor3)

    def test_frame_pair_cache_resets_on_resolution_change(self) -> None:
        cache = OptflowFramePairCache()
        f1 = PreparedFlowFrame(frame_id=1, width=640, height=480, tensor=object())
        f2 = PreparedFlowFrame(frame_id=2, width=320, height=240, tensor=object())
        f3 = PreparedFlowFrame(frame_id=3, width=320, height=240, tensor=object())

        self.assertIsNone(cache.push_and_get_pair(f1))
        self.assertIsNone(cache.push_and_get_pair(f2))
        self.assertIsNotNone(cache.push_and_get_pair(f3))


class OptflowServiceNodeErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_last_error_dedupes_repeated_message(self) -> None:
        bus = _BusStub()
        node = OnnxOptflowServiceNode(
            node_id="optflowA",
            node=SimpleNamespace(stateFields=[]),
            initial_state=None,
            service_class="f8.dl.optflow",
            allowed_tasks={"optflow_neuflowv2"},
        )
        node._bus = bus

        await node._set_last_error("missing inputShmName")
        await node._set_last_error("missing inputShmName")
        await node._set_last_error("")
        await node._set_last_error("")

        self.assertEqual(bus.errors, [("optflowA", "DL_OPTFLOW_RUNTIME", "missing inputShmName")])
        self.assertEqual(bus.clear_count, 1)

    async def test_input_shm_state_callback_uses_callback_value(self) -> None:
        bus = _BusStub({"inputShmName": ""})
        node = OnnxOptflowServiceNode(
            node_id="optflowB",
            node=SimpleNamespace(stateFields=[]),
            initial_state=None,
            service_class="f8.dl.optflow",
            allowed_tasks={"optflow_neuflowv2"},
        )
        node._bus = bus
        node._config_loaded = True

        await node.on_state("inputShmName", "shm.visible.video")

        self.assertEqual(node._input_shm_name, "shm.visible.video")

    async def test_missing_input_shm_resyncs_from_state_store(self) -> None:
        bus = _BusStub({"inputShmName": "shm.visible.video"})
        node = OnnxOptflowServiceNode(
            node_id="optflowC",
            node=SimpleNamespace(stateFields=[]),
            initial_state=None,
            service_class="f8.dl.optflow",
            allowed_tasks={"optflow_neuflowv2"},
        )
        node._bus = bus
        node._config_loaded = True

        await node._sync_input_shm_name_from_state(force=True)

        self.assertEqual(node._input_shm_name, "shm.visible.video")

    async def test_missing_input_shm_waits_without_error(self) -> None:
        bus = _BusStub({"inputShmName": ""})
        node = OnnxOptflowServiceNode(
            node_id="optflowD",
            node=SimpleNamespace(stateFields=[]),
            initial_state=None,
            service_class="f8.dl.optflow",
            allowed_tasks={"optflow_neuflowv2"},
        )
        node._bus = bus
        node._config_loaded = True

        resolved = await node._resolve_synced_input_shm_name()

        self.assertEqual(resolved, "")
        self.assertEqual(bus.errors, [])
        self.assertEqual(bus.clear_count, 0)

    async def test_valid_input_shm_clears_stale_missing_error(self) -> None:
        bus = _BusStub()
        node = OnnxOptflowServiceNode(
            node_id="optflowE",
            node=SimpleNamespace(stateFields=[]),
            initial_state=None,
            service_class="f8.dl.optflow",
            allowed_tasks={"optflow_neuflowv2"},
        )
        node._bus = bus
        node._config_loaded = True

        await node._set_last_error("missing inputShmName")
        await node._apply_input_shm_name("shm.visible.video")

        self.assertEqual(node._last_error, "")
        self.assertEqual(bus.clear_count, 1)


if __name__ == "__main__":
    unittest.main()
