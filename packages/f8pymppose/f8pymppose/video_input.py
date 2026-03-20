from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pysdk.shm.video import VideoShmReader


@dataclass(frozen=True)
class FrameContext:
    frame_id: int
    ts_ms: int
    width: int
    height: int
    pitch: int
    payload: bytes


class VideoShmInput:
    def __init__(self) -> None:
        self._shm: VideoShmReader | None = None
        self._open_name = ""

    @property
    def open_name(self) -> str:
        return self._open_name

    @property
    def is_open(self) -> bool:
        return self._shm is not None

    def close(self) -> None:
        if self._shm is not None:
            self._shm.close()
        self._shm = None
        self._open_name = ""

    def open(self, shm_name: str) -> None:
        self.close()
        shm = VideoShmReader(shm_name)
        shm.open(use_event=True)
        self._shm = shm
        self._open_name = shm_name

    def read_frame(self) -> FrameContext | None:
        assert self._shm is not None
        self._shm.wait_new_frame(timeout_ms=10)
        header, payload = self._shm.read_latest_bgra()
        if header is None or payload is None:
            return None

        width = int(header.width)
        height = int(header.height)
        pitch = int(header.pitch)
        if width <= 0 or height <= 0 or pitch <= 0:
            return None

        frame_bytes = pitch * height
        if len(payload) < frame_bytes:
            return None

        return FrameContext(
            frame_id=int(header.frame_id),
            ts_ms=int(header.ts_ms),
            width=width,
            height=height,
            pitch=pitch,
            payload=payload,
        )


def frame_rgb_from_context(frame: FrameContext, *, np_module: Any) -> Any:
    buf = np_module.frombuffer(frame.payload, dtype=np_module.uint8)
    rows = buf.reshape((frame.height, frame.pitch))
    bgra = rows[:, : frame.width * 4].reshape((frame.height, frame.width, 4))
    frame_bgr = bgra[:, :, 0:3]
    return np_module.ascontiguousarray(frame_bgr[:, :, ::-1])
