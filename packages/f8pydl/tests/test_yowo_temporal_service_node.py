import os
import sys
import unittest
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import numpy as np


PKG_PYDL = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_PYDL, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)


from f8pydl.service_node import OnnxVisionServiceNode  # noqa: E402


@dataclass(frozen=True)
class _FakeDetection:
    cls: str
    conf: float
    xyxy: tuple[float, float, float, float]
    keypoints: list[Any] | None = None
    obb: list[tuple[float, float]] | None = None


@dataclass(frozen=True)
class _FakeTemporalRuntime:
    clip_length: int
    sampling_rate: int

    @property
    def buffer_span(self) -> int:
        return (int(self.clip_length) - 1) * int(self.sampling_rate) + 1


class _TemporalHarness:
    _reset_temporal_buffer = OnnxVisionServiceNode._reset_temporal_buffer
    _append_temporal_frame = OnnxVisionServiceNode._append_temporal_frame
    _temporal_window_ready = OnnxVisionServiceNode._temporal_window_ready
    _should_infer_temporal = OnnxVisionServiceNode._should_infer_temporal
    _build_temporal_sequence = OnnxVisionServiceNode._build_temporal_sequence
    _build_detection_payload = OnnxVisionServiceNode._build_detection_payload

    def __init__(self, *, runtime: _FakeTemporalRuntime, infer_every_n: int = 1) -> None:
        self._temporal_det_runtime = runtime
        self._temporal_frame_buffer: deque[Any] = deque(maxlen=runtime.buffer_span)
        self._temporal_frame_counter = 0
        self._infer_every_n = infer_every_n
        self._last_infer_frame_id: int | None = None
        self._enabled_classes: list[str] = []
        self._per_class_k = 0
        self._model = SimpleNamespace(model_id="demo_model", skeleton_protocol="none")
        self._service_task = "detector"

    def _apply_detection_filters(self, detections: list[Any]) -> list[Any]:
        return list(detections)


class YowoTemporalServiceNodeTests(unittest.TestCase):
    def test_temporal_window_warms_up_before_ready(self) -> None:
        harness = _TemporalHarness(runtime=_FakeTemporalRuntime(clip_length=4, sampling_rate=2))
        for frame_id in range(1, 7):
            harness._append_temporal_frame(
                prepared_frame=np.full((3, 1, 1), frame_id, dtype=np.float32),
                frame_id=frame_id,
                ts_ms=frame_id * 10,
            )
            self.assertFalse(harness._temporal_window_ready())
        harness._append_temporal_frame(
            prepared_frame=np.full((3, 1, 1), 7, dtype=np.float32),
            frame_id=7,
            ts_ms=70,
        )
        self.assertTrue(harness._temporal_window_ready())

    def test_sampling_rate_selects_sparse_frames(self) -> None:
        harness = _TemporalHarness(runtime=_FakeTemporalRuntime(clip_length=4, sampling_rate=2))
        for frame_id in range(1, 8):
            harness._append_temporal_frame(
                prepared_frame=np.full((3, 1, 1), frame_id, dtype=np.float32),
                frame_id=frame_id,
                ts_ms=frame_id * 10,
            )
        sequence = harness._build_temporal_sequence()
        self.assertEqual(sequence.shape, (4, 3, 1, 1))
        self.assertEqual(sequence[:, 0, 0, 0].tolist(), [1.0, 3.0, 5.0, 7.0])

    def test_infer_every_n_throttles_temporal_inference(self) -> None:
        harness = _TemporalHarness(runtime=_FakeTemporalRuntime(clip_length=4, sampling_rate=1), infer_every_n=2)
        for frame_id in range(1, 5):
            harness._append_temporal_frame(
                prepared_frame=np.full((3, 1, 1), frame_id, dtype=np.float32),
                frame_id=frame_id,
                ts_ms=frame_id * 10,
            )
        self.assertTrue(harness._should_infer_temporal())
        harness._last_infer_frame_id = 4
        harness._append_temporal_frame(
            prepared_frame=np.full((3, 1, 1), 5, dtype=np.float32),
            frame_id=5,
            ts_ms=50,
        )
        self.assertFalse(harness._should_infer_temporal())
        harness._append_temporal_frame(
            prepared_frame=np.full((3, 1, 1), 6, dtype=np.float32),
            frame_id=6,
            ts_ms=60,
        )
        self.assertTrue(harness._should_infer_temporal())

    def test_reset_temporal_buffer_clears_history(self) -> None:
        harness = _TemporalHarness(runtime=_FakeTemporalRuntime(clip_length=4, sampling_rate=1))
        harness._append_temporal_frame(
            prepared_frame=np.ones((3, 1, 1), dtype=np.float32),
            frame_id=1,
            ts_ms=10,
        )
        harness._reset_temporal_buffer()
        self.assertEqual(len(harness._temporal_frame_buffer), 0)
        self.assertEqual(harness._temporal_frame_counter, 0)

    def test_temporal_detection_payload_stays_on_standard_schema(self) -> None:
        harness = _TemporalHarness(runtime=_FakeTemporalRuntime(clip_length=4, sampling_rate=1))
        payload = harness._build_detection_payload(
            width=640,
            height=360,
            frame_id=9,
            ts_ms=1234,
            detections=[_FakeDetection(cls="insertive_actor", conf=0.8, xyxy=(10.0, 20.0, 100.0, 120.0))],
        )
        self.assertEqual(payload["schemaVersion"], "f8visionDetections/1")
        self.assertEqual(payload["frameId"], 9)
        self.assertEqual(payload["detections"][0]["cls"], "insertive_actor")
        self.assertEqual(payload["detections"][0]["bbox"], [10, 20, 100, 120])


if __name__ == "__main__":
    unittest.main()
