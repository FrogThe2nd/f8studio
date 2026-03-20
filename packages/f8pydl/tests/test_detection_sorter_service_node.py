from __future__ import annotations

import re
import unittest
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np

from f8pysdk.service_bus.state_read import StateRead
from f8pysdk.shm.video import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16, VIDEO_FORMAT_SCALAR1_F32, VideoShmHeader, VideoShmWriter

from f8pydl.detection_sorter_service_node import (
    DetectionSorterServiceNode,
    aggregate_roi_score,
    decode_score_map_from_frame,
    rescale_bbox_to_score_map,
    sort_detection_payload,
)


class _BusStub:
    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(initial_state or {})
        self.emitted: list[tuple[str, str, Any, int | None]] = []
        self.published_state: list[tuple[str, str, Any, int | None]] = []

    async def emit_data(self, node_id: str, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        self.emitted.append((node_id, port, value, ts_ms))

    async def publish_state_runtime(self, node_id: str, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        self.state[field] = value
        self.published_state.append((node_id, field, value, ts_ms))

    async def get_state(self, node_id: str, field: str) -> StateRead:
        del node_id
        if field in self.state:
            return StateRead(found=True, value=self.state[field], ts_ms=None)
        return StateRead(found=False, value=None, ts_ms=None)

    def get_state_cached(self, node_id: str, field: str, default: Any = None) -> Any:
        del node_id
        return self.state.get(field, default)


def _make_detection_payload(
    detections: list[dict[str, Any]],
    *,
    frame_id: int = 1,
    ts_ms: int = 1000,
    width: int = 4,
    height: int = 4,
) -> dict[str, Any]:
    return {
        "schemaVersion": "f8visionDetections/1",
        "frameId": frame_id,
        "tsMs": ts_ms,
        "width": width,
        "height": height,
        "model": "demo",
        "task": "detector",
        "skeletonProtocol": "none",
        "detections": detections,
    }


def _unique_shm_name(prefix: str) -> str:
    return f"{prefix}.{uuid.uuid4().hex}"


def _write_scalar_frame(array: np.ndarray) -> tuple[str, VideoShmWriter]:
    values = np.asarray(array, dtype=np.float32)
    height = int(values.shape[0])
    width = int(values.shape[1])
    pitch = width * 4
    writer = VideoShmWriter(_unique_shm_name("test.scalar"), size=1 << 20)
    writer.open()
    writer.write_frame(width=width, height=height, pitch=pitch, payload=values.tobytes(order="C"), fmt=VIDEO_FORMAT_SCALAR1_F32)
    return writer.shm_name, writer


def _write_flow_frame(flow: np.ndarray) -> tuple[str, VideoShmWriter]:
    flow_values = np.asarray(flow, dtype=np.float32)
    height = int(flow_values.shape[0])
    width = int(flow_values.shape[1])
    payload = np.ascontiguousarray(flow_values.astype(np.float16).view(np.uint8)).tobytes(order="C")
    pitch = width * 4
    writer = VideoShmWriter(_unique_shm_name("test.flow"), size=1 << 20)
    writer.open()
    writer.write_frame(width=width, height=height, pitch=pitch, payload=payload, fmt=VIDEO_FORMAT_FLOW2_F16)
    return writer.shm_name, writer


class DetectionSorterHelpersTests(unittest.TestCase):
    def test_rescale_bbox_to_score_map(self) -> None:
        bbox = rescale_bbox_to_score_map(
            [50, 25, 150, 75],
            detections_width=200,
            detections_height=100,
            score_width=100,
            score_height=50,
        )
        self.assertEqual(bbox, (25, 12, 75, 38))

    def test_aggregate_roi_score_variants(self) -> None:
        roi = np.asarray([[1.0, 3.0], [5.0, 7.0]], dtype=np.float32)
        self.assertEqual(aggregate_roi_score(roi, "max"), 7.0)
        self.assertEqual(aggregate_roi_score(roi, "sum"), 16.0)
        self.assertEqual(aggregate_roi_score(roi, "median"), 4.0)
        self.assertEqual(aggregate_roi_score(roi, "mean"), 4.0)

    def test_sort_detection_payload_mean_desc_keeps_original_score(self) -> None:
        score_map = np.asarray(
            [
                [1.0, 1.0, 9.0, 9.0],
                [1.0, 1.0, 9.0, 9.0],
                [2.0, 2.0, 3.0, 3.0],
                [2.0, 2.0, 3.0, 3.0],
            ],
            dtype=np.float32,
        )
        payload = _make_detection_payload(
            [
                {"cls": "low", "score": 0.11, "bbox": [0, 0, 2, 2]},
                {"cls": "high", "score": 0.22, "bbox": [2, 0, 4, 2]},
            ]
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="desc",
            score_aggregation="mean",
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["high", "low"])
        self.assertEqual([item["score"] for item in sorted_payload["detections"]], [0.22, 0.11])

    def test_sort_detection_payload_flow_magnitude(self) -> None:
        flow = np.zeros((4, 4, 2), dtype=np.float32)
        flow[0:2, 0:2, 0] = 1.0
        flow[2:4, 2:4, 0] = 3.0
        magnitude = np.sqrt((flow[:, :, 0] * flow[:, :, 0]) + (flow[:, :, 1] * flow[:, :, 1]))
        payload = _make_detection_payload(
            [
                {"cls": "weak", "score": 0.5, "bbox": [0, 0, 2, 2]},
                {"cls": "strong", "score": 0.6, "bbox": [2, 2, 4, 4]},
            ]
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=magnitude,
            sort_direction="desc",
            score_aggregation="mean",
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["strong", "weak"])

    def test_sort_detection_payload_ascending_order(self) -> None:
        score_map = np.asarray([[10.0, 10.0], [1.0, 1.0]], dtype=np.float32)
        payload = _make_detection_payload(
            [
                {"cls": "high", "score": 0.9, "bbox": [0, 0, 2, 1]},
                {"cls": "low", "score": 0.8, "bbox": [0, 1, 2, 2]},
            ],
            width=2,
            height=2,
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="asc",
            score_aggregation="mean",
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["low", "high"])

    def test_sort_detection_payload_applies_cls_weights_exact(self) -> None:
        score_map = np.asarray([[5.0, 5.0], [4.0, 4.0]], dtype=np.float32)
        payload = _make_detection_payload(
            [
                {"cls": "person", "score": 0.1, "bbox": [0, 0, 2, 1]},
                {"cls": "car", "score": 0.2, "bbox": [0, 1, 2, 2]},
            ],
            width=2,
            height=2,
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="desc",
            score_aggregation="mean",
            cls_weights_exact={"person": 0.1, "car": 2.0},
            cls_weights_regex=[],
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["car", "person"])

    def test_sort_detection_payload_applies_cls_weights_regex(self) -> None:
        score_map = np.asarray([[5.0, 5.0], [4.0, 4.0]], dtype=np.float32)
        payload = _make_detection_payload(
            [
                {"cls": "cat", "score": 0.1, "bbox": [0, 0, 2, 1]},
                {"cls": "dog_1", "score": 0.2, "bbox": [0, 1, 2, 2]},
            ],
            width=2,
            height=2,
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="desc",
            score_aggregation="mean",
            cls_weights_exact={},
            cls_weights_regex=[(re.compile("^dog_.*$"), 2.0)],
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["dog_1", "cat"])

    def test_sort_detection_payload_multiplies_all_matching_cls_weights(self) -> None:
        score_map = np.asarray([[4.0, 4.0], [5.0, 5.0]], dtype=np.float32)
        payload = _make_detection_payload(
            [
                {"cls": "FEMALE_GENITALIA_EXPOSED", "score": 0.1, "bbox": [0, 0, 2, 1]},
                {"cls": "neutral", "score": 0.2, "bbox": [0, 1, 2, 2]},
            ],
            width=2,
            height=2,
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="desc",
            score_aggregation="mean",
            cls_weights_exact={},
            cls_weights_regex=[
                (re.compile("^.*_GENITALIA_.*$"), 3.0),
                (re.compile("^.*_EXPOSED$"), 2.0),
            ],
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["FEMALE_GENITALIA_EXPOSED", "neutral"])

    def test_sort_detection_payload_all_aggregations(self) -> None:
        score_map = np.asarray(
            [
                [1.0, 5.0, 2.0, 2.0],
                [1.0, 1.0, 2.0, 2.0],
            ],
            dtype=np.float32,
        )
        payload = _make_detection_payload(
            [
                {"cls": "peaky", "score": 0.1, "bbox": [0, 0, 2, 2]},
                {"cls": "steady", "score": 0.2, "bbox": [2, 0, 4, 2]},
            ],
            width=4,
            height=2,
        )

        self.assertEqual(
            [item["cls"] for item in sort_detection_payload(payload, score_map=score_map, sort_direction="desc", score_aggregation="max")["detections"]],
            ["peaky", "steady"],
        )
        self.assertEqual(
            [item["cls"] for item in sort_detection_payload(payload, score_map=score_map, sort_direction="desc", score_aggregation="sum")["detections"]],
            ["peaky", "steady"],
        )
        self.assertEqual(
            [item["cls"] for item in sort_detection_payload(payload, score_map=score_map, sort_direction="desc", score_aggregation="median")["detections"]],
            ["steady", "peaky"],
        )

    def test_sort_detection_payload_invalid_bbox_kept_at_end_stably(self) -> None:
        score_map = np.asarray([[3.0, 3.0], [4.0, 4.0]], dtype=np.float32)
        payload = _make_detection_payload(
            [
                {"cls": "valid", "score": 0.7, "bbox": [0, 0, 2, 2]},
                {"cls": "bad-a", "score": 0.5, "bbox": [1, 1, 1, 2]},
                {"cls": "bad-b", "score": 0.4, "bbox": [0, 0, 0, 0]},
            ],
            width=2,
            height=2,
        )

        sorted_payload = sort_detection_payload(
            payload,
            score_map=score_map,
            sort_direction="desc",
            score_aggregation="mean",
        )

        self.assertIsNotNone(sorted_payload)
        assert sorted_payload is not None
        self.assertEqual([item["cls"] for item in sorted_payload["detections"]], ["valid", "bad-a", "bad-b"])


class DetectionSorterServiceNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_state_cls_weights_rejects_invalid_json(self) -> None:
        node = DetectionSorterServiceNode(node_id="sorterZ", node=SimpleNamespace(stateFields=[]), initial_state=None)
        with self.assertRaises(ValueError):
            _ = await node.validate_state("clsWeights", "{", ts_ms=0, meta={})

    async def test_service_node_sorts_scalar_map_and_emits(self) -> None:
        shm_name, writer = _write_scalar_frame(
            np.asarray(
                [
                    [1.0, 1.0, 9.0, 9.0],
                    [1.0, 1.0, 9.0, 9.0],
                    [0.0, 0.0, 2.0, 2.0],
                    [0.0, 0.0, 2.0, 2.0],
                ],
                dtype=np.float32,
            )
        )
        try:
            bus = _BusStub({"scoreShmName": shm_name})
            node = DetectionSorterServiceNode(node_id="sorterA", node=SimpleNamespace(stateFields=[]), initial_state=None)
            node.attach(bus)
            payload = _make_detection_payload(
                [
                    {"cls": "left", "score": 0.3, "bbox": [0, 0, 2, 2]},
                    {"cls": "right", "score": 0.4, "bbox": [2, 0, 4, 2]},
                ],
                frame_id=1,
            )

            await node.on_data("detections", payload)

            self.assertEqual(len(bus.emitted), 1)
            emitted_payload = bus.emitted[0][2]
            self.assertEqual([item["cls"] for item in emitted_payload["detections"]], ["right", "left"])
            self.assertEqual(bus.state.get("lastError", ""), "")
            node._close_score_reader()
        finally:
            writer.close(unlink=True)

    async def test_service_node_supports_flow2f16_magnitude(self) -> None:
        flow = np.zeros((4, 4, 2), dtype=np.float32)
        flow[0:2, 0:2, 0] = 1.0
        flow[2:4, 2:4, 1] = 4.0
        shm_name, writer = _write_flow_frame(flow)
        try:
            bus = _BusStub({"scoreShmName": shm_name})
            node = DetectionSorterServiceNode(node_id="sorterB", node=SimpleNamespace(stateFields=[]), initial_state=None)
            node.attach(bus)
            payload = _make_detection_payload(
                [
                    {"cls": "weak", "score": 0.3, "bbox": [0, 0, 2, 2]},
                    {"cls": "strong", "score": 0.4, "bbox": [2, 2, 4, 4]},
                ],
                frame_id=1,
            )

            await node.on_data("detections", payload)

            self.assertEqual(len(bus.emitted), 1)
            emitted_payload = bus.emitted[0][2]
            self.assertEqual([item["cls"] for item in emitted_payload["detections"]], ["strong", "weak"])
            node._close_score_reader()
        finally:
            writer.close(unlink=True)

    async def test_service_node_rescales_bboxes(self) -> None:
        shm_name, writer = _write_scalar_frame(
            np.asarray(
                [
                    [0.0, 0.0, 9.0, 9.0],
                    [0.0, 0.0, 9.0, 9.0],
                ],
                dtype=np.float32,
            )
        )
        try:
            bus = _BusStub({"scoreShmName": shm_name})
            node = DetectionSorterServiceNode(node_id="sorterC", node=SimpleNamespace(stateFields=[]), initial_state=None)
            node.attach(bus)
            payload = _make_detection_payload(
                [
                    {"cls": "left", "score": 0.3, "bbox": [0, 0, 2, 4]},
                    {"cls": "right", "score": 0.4, "bbox": [2, 0, 4, 4]},
                ],
                frame_id=1,
                width=4,
                height=4,
            )

            await node.on_data("detections", payload)

            self.assertEqual([item["cls"] for item in bus.emitted[0][2]["detections"]], ["right", "left"])
            node._close_score_reader()
        finally:
            writer.close(unlink=True)

    async def test_service_node_unsupported_format_sets_last_error(self) -> None:
        writer = VideoShmWriter(_unique_shm_name("test.unsupported"), size=1 << 20)
        writer.open()
        try:
            bgra = np.zeros((2, 2, 4), dtype=np.uint8)
            writer.write_frame(width=2, height=2, pitch=8, payload=bgra.tobytes(order="C"), fmt=VIDEO_FORMAT_BGRA32)
            bus = _BusStub({"scoreShmName": writer.shm_name})
            node = DetectionSorterServiceNode(node_id="sorterD", node=SimpleNamespace(stateFields=[]), initial_state=None)
            node.attach(bus)
            payload = _make_detection_payload([{"cls": "x", "score": 0.8, "bbox": [0, 0, 2, 2]}], frame_id=1, width=2, height=2)

            await node.on_data("detections", payload)

            self.assertEqual(len(bus.emitted), 1)
            emitted_payload = bus.emitted[0][2]
            self.assertEqual([item["cls"] for item in emitted_payload["detections"]], ["x"])
            self.assertIn("score SHM unavailable", str(bus.state.get("lastError", "")))
            node._close_score_reader()
        finally:
            writer.close(unlink=True)

    async def test_service_node_emits_even_when_frame_id_differs(self) -> None:
        shm_name, writer = _write_scalar_frame(np.ones((2, 2), dtype=np.float32))
        try:
            bus = _BusStub({"scoreShmName": shm_name})
            node = DetectionSorterServiceNode(node_id="sorterE", node=SimpleNamespace(stateFields=[]), initial_state=None)
            node.attach(bus)
            payload = _make_detection_payload([{"cls": "x", "score": 0.8, "bbox": [0, 0, 2, 2]}], frame_id=10, width=2, height=2)

            await node.on_data("detections", payload)

            self.assertEqual(len(bus.emitted), 1)
            self.assertEqual(bus.state.get("lastError", ""), "")
            node._close_score_reader()
        finally:
            writer.close(unlink=True)

    async def test_service_node_shm_unavailable_pass_through(self) -> None:
        bus = _BusStub({"scoreShmName": "shm.this.does.not.exist"})
        node = DetectionSorterServiceNode(node_id="sorterF", node=SimpleNamespace(stateFields=[]), initial_state=None)
        node.attach(bus)
        payload = _make_detection_payload(
            [
                {"cls": "a", "score": 0.1, "bbox": [0, 0, 2, 2]},
                {"cls": "b", "score": 0.9, "bbox": [0, 0, 2, 2]},
            ],
            frame_id=1,
            width=2,
            height=2,
        )

        await node.on_data("detections", payload)

        self.assertEqual(len(bus.emitted), 1)
        emitted_payload = bus.emitted[0][2]
        self.assertEqual([item["cls"] for item in emitted_payload["detections"]], ["a", "b"])
        self.assertIn("score SHM unavailable", str(bus.state.get("lastError", "")))

    def test_decode_score_map_from_frame_scalar(self) -> None:
        values = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        header = VideoShmHeader(
            magic=1,
            version=1,
            slot_count=2,
            width=2,
            height=2,
            pitch=8,
            fmt=VIDEO_FORMAT_SCALAR1_F32,
            frame_id=1,
            ts_ms=1000,
            active_slot=0,
            payload_capacity=16,
            notify_seq=1,
        )

        score_map = decode_score_map_from_frame(header=header, payload=memoryview(values.tobytes(order="C")))

        np.testing.assert_allclose(score_map, values)


if __name__ == "__main__":
    unittest.main()
