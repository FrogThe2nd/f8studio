from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import ServiceNode
from f8pysdk.shm.video import VIDEO_FORMAT_FLOW2_F16, VIDEO_FORMAT_SCALAR1_F32, VideoShmHeader, VideoShmReader

SortDirection = Literal["asc", "desc"]
ScoreAggregation = Literal["mean", "max", "sum", "median"]

_MAX_FRAME_GAP = 2
_MAX_TS_GAP_MS = 100

logger = logging.getLogger(__name__)


def _coerce_str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text:
        return text
    return default


def _coerce_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_sort_direction(value: Any, *, default: SortDirection = "desc") -> SortDirection:
    text = _coerce_str(value, default=default).lower()
    if text == "asc":
        return "asc"
    if text == "desc":
        return "desc"
    return default


def _coerce_score_aggregation(value: Any, *, default: ScoreAggregation = "mean") -> ScoreAggregation:
    text = _coerce_str(value, default=default).lower()
    if text == "max":
        return "max"
    if text == "sum":
        return "sum"
    if text == "median":
        return "median"
    return "mean"


def _payload_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def detections_and_score_map_are_synced(detections_payload: dict[str, Any], score_header: VideoShmHeader) -> bool:
    detection_frame_id = _payload_int(detections_payload, "frameId")
    score_frame_id = int(score_header.frame_id) if int(score_header.frame_id) > 0 else None
    if detection_frame_id is not None and score_frame_id is not None:
        return abs(detection_frame_id - score_frame_id) <= _MAX_FRAME_GAP

    detection_ts_ms = _payload_int(detections_payload, "tsMs")
    score_ts_ms = int(score_header.ts_ms) if int(score_header.ts_ms) > 0 else None
    if detection_ts_ms is not None and score_ts_ms is not None:
        return abs(detection_ts_ms - score_ts_ms) <= _MAX_TS_GAP_MS
    return False


def decode_score_map_from_frame(*, header: VideoShmHeader, payload: memoryview) -> np.ndarray:
    width = int(header.width)
    height = int(header.height)
    pitch = int(header.pitch)
    if width <= 0 or height <= 0 or pitch <= 0:
        raise ValueError("invalid score shm dimensions")

    frame_bytes = int(pitch) * int(height)
    if len(payload) < frame_bytes:
        raise ValueError("score shm frame too small")

    if int(header.fmt) == VIDEO_FORMAT_SCALAR1_F32:
        if pitch < width * 4 or (pitch % 4) != 0:
            raise ValueError("invalid scalar1_f32 pitch")
        row_floats = pitch // 4
        scores = np.frombuffer(payload, dtype=np.float32, count=row_floats * height).reshape((height, row_floats))
        return np.ascontiguousarray(scores[:, :width], dtype=np.float32)

    if int(header.fmt) == VIDEO_FORMAT_FLOW2_F16:
        if pitch < width * 4 or (pitch % 2) != 0:
            raise ValueError("invalid flow2_f16 pitch")
        row_halfs = pitch // 2
        flow_halfs = np.frombuffer(payload, dtype=np.float16, count=row_halfs * height).reshape((height, row_halfs))
        packed = np.ascontiguousarray(flow_halfs[:, : width * 2], dtype=np.float16)
        flow = packed.astype(np.float32).reshape((height, width, 2))
        magnitude = np.sqrt((flow[:, :, 0] * flow[:, :, 0]) + (flow[:, :, 1] * flow[:, :, 1]))
        return np.ascontiguousarray(magnitude, dtype=np.float32)

    raise ValueError(f"unsupported score shm format: {int(header.fmt)}")


def rescale_bbox_to_score_map(
    bbox: list[Any] | tuple[Any, ...],
    *,
    detections_width: int,
    detections_height: int,
    score_width: int,
    score_height: int,
) -> tuple[int, int, int, int] | None:
    if len(bbox) < 4:
        return None
    if detections_width <= 0 or detections_height <= 0 or score_width <= 0 or score_height <= 0:
        return None

    x1 = _coerce_int(bbox[0], default=0)
    y1 = _coerce_int(bbox[1], default=0)
    x2 = _coerce_int(bbox[2], default=0)
    y2 = _coerce_int(bbox[3], default=0)
    if x2 <= x1 or y2 <= y1:
        return None

    scale_x = float(score_width) / float(detections_width)
    scale_y = float(score_height) / float(detections_height)

    scaled_x1 = int(np.floor(float(x1) * scale_x))
    scaled_y1 = int(np.floor(float(y1) * scale_y))
    scaled_x2 = int(np.ceil(float(x2) * scale_x))
    scaled_y2 = int(np.ceil(float(y2) * scale_y))

    clamped_x1 = max(0, min(score_width, scaled_x1))
    clamped_y1 = max(0, min(score_height, scaled_y1))
    clamped_x2 = max(0, min(score_width, scaled_x2))
    clamped_y2 = max(0, min(score_height, scaled_y2))
    if clamped_x2 <= clamped_x1 or clamped_y2 <= clamped_y1:
        return None
    return (clamped_x1, clamped_y1, clamped_x2, clamped_y2)


def aggregate_roi_score(roi: np.ndarray, aggregation: ScoreAggregation) -> float | None:
    if roi.size <= 0:
        return None

    roi_float = np.asarray(roi, dtype=np.float32)
    if aggregation == "max":
        return float(np.max(roi_float))
    if aggregation == "sum":
        return float(np.sum(roi_float, dtype=np.float32))
    if aggregation == "median":
        return float(np.median(roi_float))
    return float(np.mean(roi_float, dtype=np.float32))


@dataclass(frozen=True)
class RankedDetection:
    index: int
    detection: dict[str, Any]
    metric_score: float | None


def sort_detection_payload(
    detections_payload: dict[str, Any],
    *,
    score_map: np.ndarray,
    sort_direction: SortDirection,
    score_aggregation: ScoreAggregation,
) -> dict[str, Any] | None:
    detections_value = detections_payload.get("detections")
    if not isinstance(detections_value, list) or not detections_value:
        return None

    detections_width = _payload_int(detections_payload, "width")
    detections_height = _payload_int(detections_payload, "height")
    if detections_width is None or detections_height is None:
        return None

    score_height = int(score_map.shape[0])
    score_width = int(score_map.shape[1])
    ranked: list[RankedDetection] = []
    for index, raw_detection in enumerate(detections_value):
        if not isinstance(raw_detection, dict):
            ranked.append(RankedDetection(index=index, detection={}, metric_score=None))
            continue
        bbox_value = raw_detection.get("bbox")
        if not isinstance(bbox_value, list) and not isinstance(bbox_value, tuple):
            ranked.append(RankedDetection(index=index, detection=dict(raw_detection), metric_score=None))
            continue
        scaled_bbox = rescale_bbox_to_score_map(
            bbox_value,
            detections_width=detections_width,
            detections_height=detections_height,
            score_width=score_width,
            score_height=score_height,
        )
        if scaled_bbox is None:
            ranked.append(RankedDetection(index=index, detection=dict(raw_detection), metric_score=None))
            continue
        x1, y1, x2, y2 = scaled_bbox
        roi = score_map[y1:y2, x1:x2]
        metric_score = aggregate_roi_score(roi, score_aggregation)
        ranked.append(RankedDetection(index=index, detection=dict(raw_detection), metric_score=metric_score))

    valid_ranked: list[RankedDetection] = []
    invalid_ranked: list[RankedDetection] = []
    for item in ranked:
        if item.metric_score is None:
            invalid_ranked.append(item)
        else:
            valid_ranked.append(item)

    reverse = sort_direction == "desc"
    valid_ranked.sort(key=lambda item: float(item.metric_score), reverse=reverse)
    ordered = valid_ranked + invalid_ranked
    if not ordered:
        return None

    output_payload = dict(detections_payload)
    output_payload["detections"] = [item.detection for item in ordered]
    return output_payload


class DetectionSorterServiceNode(ServiceNode):
    def __init__(self, *, node_id: str, node: Any, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=["detections"],
            data_out_ports=["detections"],
            state_fields=[state.name for state in list(node.stateFields or [])],
        )
        self._initial_state = dict(initial_state or {})
        self._config_loaded = False
        self._score_shm_name = ""
        self._sort_direction: SortDirection = "desc"
        self._score_aggregation: ScoreAggregation = "mean"
        self._last_error = ""
        self._latest_detections: dict[str, Any] | None = None
        self._score_reader: VideoShmReader | None = None
        self._score_reader_name = ""

    def _read_initial_or_cached_state(self, field: str, default: Any) -> Any:
        missing = object()
        cached_value = self.get_state_cached(field, missing)
        if cached_value is not missing:
            return cached_value
        if field in self._initial_state:
            return self._initial_state[field]
        return default

    async def _ensure_config_loaded(self) -> None:
        if self._config_loaded:
            return
        self._score_shm_name = _coerce_str(self._read_initial_or_cached_state("scoreShmName", ""))
        self._sort_direction = _coerce_sort_direction(self._read_initial_or_cached_state("sortDirection", "desc"))
        self._score_aggregation = _coerce_score_aggregation(self._read_initial_or_cached_state("scoreAggregation", "mean"))
        self._config_loaded = True

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        if name == "sortDirection":
            direction = _coerce_str(value).lower()
            if direction not in ("asc", "desc"):
                raise ValueError("invalid sortDirection (expected asc or desc)")
            return direction
        if name == "scoreAggregation":
            aggregation = _coerce_str(value).lower()
            if aggregation not in ("mean", "max", "sum", "median"):
                raise ValueError("invalid scoreAggregation (expected mean, max, sum, or median)")
            return aggregation
        if name == "scoreShmName":
            return _coerce_str(value)
        return value

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        del ts_ms
        await self._ensure_config_loaded()
        name = str(field or "").strip()
        if name == "scoreShmName":
            self._score_shm_name = _coerce_str(value, default=self._score_shm_name)
            self._close_score_reader()
            return
        if name == "sortDirection":
            self._sort_direction = _coerce_sort_direction(value, default=self._sort_direction)
            return
        if name == "scoreAggregation":
            self._score_aggregation = _coerce_score_aggregation(value, default=self._score_aggregation)
            return

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        del ts_ms
        if port != "detections":
            return
        await self._ensure_config_loaded()
        if not isinstance(value, dict):
            await self._set_last_error("detections payload must be an object")
            return
        self._latest_detections = dict(value)
        try:
            output_payload = self._sort_latest_detections()
        except Exception as exc:
            logger.exception("detection sorter failed node=%s", self.node_id)
            await self._set_last_error(f"{type(exc).__name__}: {exc}")
            return
        if output_payload is None:
            return
        await self._set_last_error("")
        await self.emit("detections", output_payload, ts_ms=_payload_int(output_payload, "tsMs"))

    def _close_score_reader(self) -> None:
        if self._score_reader is not None:
            self._score_reader.close()
            self._score_reader = None
        self._score_reader_name = ""

    def _ensure_score_reader(self) -> VideoShmReader:
        score_shm_name = str(self._score_shm_name).strip()
        if not score_shm_name:
            raise ValueError("scoreShmName is empty")
        if self._score_reader is not None and self._score_reader_name == score_shm_name:
            return self._score_reader
        self._close_score_reader()
        reader = VideoShmReader(score_shm_name)
        reader.open(use_event=False)
        self._score_reader = reader
        self._score_reader_name = score_shm_name
        return reader

    def _sort_latest_detections(self) -> dict[str, Any] | None:
        if self._latest_detections is None:
            return None
        detections_value = self._latest_detections.get("detections")
        if not isinstance(detections_value, list) or not detections_value:
            return None
        reader = self._ensure_score_reader()
        header, payload = reader.read_latest_frame()
        if header is None or payload is None:
            raise ValueError("score shm has no readable frame")
        try:
            if not detections_and_score_map_are_synced(self._latest_detections, header):
                return None
            score_map = decode_score_map_from_frame(header=header, payload=payload)
            return sort_detection_payload(
                self._latest_detections,
                score_map=score_map,
                sort_direction=self._sort_direction,
                score_aggregation=self._score_aggregation,
            )
        finally:
            payload.release()

    async def _set_last_error(self, message: str) -> None:
        normalized = str(message or "")
        if normalized == self._last_error:
            return
        self._last_error = normalized
        await self.set_state("lastError", self._last_error)
