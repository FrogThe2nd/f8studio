from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol

from .shm.video import VideoShmReader, VideoShmWriter


@dataclass
class LatestVideoFrame:
    width: int
    height: int
    pitch: int
    fmt: int
    frame_id: int
    ts_ms: int
    payload: memoryview
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def frame_bytes(self) -> int:
        return int(self.pitch) * int(self.height)

    def payload_bytes(self) -> bytes:
        if self._released:
            raise RuntimeError("video frame payload has been released")
        return bytes(self.payload)

    def release(self) -> None:
        if self._released:
            return
        self.payload.release()
        self._released = True

    def __enter__(self) -> "LatestVideoFrame":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class LatestVideoFrameTransport(Protocol):
    def close(self) -> None: ...

    def publish_frame(
        self,
        *,
        width: int,
        height: int,
        pitch: int,
        payload: bytes | bytearray | memoryview,
        fmt: int,
        ts_ms: int | None = None,
    ) -> None: ...

    def poll_latest(self) -> LatestVideoFrame | None: ...

    def wait_latest(self, timeout_ms: int) -> LatestVideoFrame | None: ...


class LegacyShmLatestVideoFrameTransport:
    def __init__(self, *, reader: VideoShmReader | None = None, writer: VideoShmWriter | None = None) -> None:
        self._reader = reader
        self._writer = writer
        self._last_signature: tuple[int, int, int] | None = None

    @classmethod
    def open_reader(cls, shm_name: str, *, use_event: bool = True) -> "LegacyShmLatestVideoFrameTransport":
        reader = VideoShmReader(str(shm_name))
        reader.open(use_event=bool(use_event))
        return cls(reader=reader)

    @classmethod
    def open_writer(
        cls,
        shm_name: str,
        *,
        size: int,
        slot_count: int = 2,
    ) -> "LegacyShmLatestVideoFrameTransport":
        writer = VideoShmWriter(shm_name=str(shm_name), size=int(size), slot_count=int(slot_count))
        writer.open()
        return cls(writer=writer)

    @classmethod
    def open_read_writer(
        cls,
        shm_name: str,
        *,
        size: int,
        slot_count: int = 2,
        use_event: bool = True,
    ) -> "LegacyShmLatestVideoFrameTransport":
        writer = VideoShmWriter(shm_name=str(shm_name), size=int(size), slot_count=int(slot_count))
        writer.open()
        try:
            reader = VideoShmReader(str(shm_name))
            reader.open(use_event=bool(use_event))
        except (FileNotFoundError, OSError, RuntimeError):
            writer.close(unlink=True)
            raise
        return cls(reader=reader, writer=writer)

    @property
    def reader(self) -> VideoShmReader | None:
        return self._reader

    @property
    def writer(self) -> VideoShmWriter | None:
        return self._writer

    def close(self, *, unlink: bool = False) -> None:
        reader = self._reader
        writer = self._writer
        self._reader = None
        self._writer = None
        self._last_signature = None
        if reader is not None:
            reader.close()
        if writer is not None:
            writer.close(unlink=bool(unlink))

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
        del ts_ms
        writer = self._writer
        if writer is None:
            raise RuntimeError("legacy SHM video transport is not opened for writing")
        width_i = int(width)
        height_i = int(height)
        pitch_i = int(pitch)
        fmt_i = int(fmt)
        if width_i <= 0 or height_i <= 0 or pitch_i <= 0:
            raise ValueError("width, height, and pitch must be positive")
        if fmt_i <= 0:
            raise ValueError("fmt must be positive")
        payload_view = memoryview(payload).cast("B")
        try:
            frame_bytes = pitch_i * height_i
            if len(payload_view) < frame_bytes:
                raise ValueError("payload is smaller than pitch * height")
            writer.write_frame(width=width_i, height=height_i, pitch=pitch_i, payload=payload_view, fmt=fmt_i)
        finally:
            payload_view.release()

    def poll_latest(self) -> LatestVideoFrame | None:
        reader = self._reader
        if reader is None:
            raise RuntimeError("legacy SHM video transport is not opened for reading")
        header, payload = reader.read_latest_frame()
        if header is None or payload is None:
            return None
        frame_id = int(header.frame_id)
        ts_ms = int(header.ts_ms)
        signature = (frame_id, ts_ms, int(header.notify_seq))
        if signature == self._last_signature:
            payload.release()
            return None
        self._last_signature = signature
        return LatestVideoFrame(
            width=int(header.width),
            height=int(header.height),
            pitch=int(header.pitch),
            fmt=int(header.fmt),
            frame_id=frame_id,
            ts_ms=ts_ms,
            payload=payload,
        )

    def wait_latest(self, timeout_ms: int) -> LatestVideoFrame | None:
        reader = self._reader
        if reader is None:
            raise RuntimeError("legacy SHM video transport is not opened for reading")
        frame = self.poll_latest()
        if frame is not None:
            return frame
        timeout_ms_i = max(0, int(timeout_ms))
        deadline = time.monotonic() + (float(timeout_ms_i) / 1000.0)
        while True:
            remaining_ms = int(max(0.0, (deadline - time.monotonic()) * 1000.0))
            if remaining_ms <= 0:
                return None
            if not reader.wait_new_frame(timeout_ms=remaining_ms):
                return None
            frame = self.poll_latest()
            if frame is not None:
                return frame


__all__ = [
    "LatestVideoFrame",
    "LatestVideoFrameTransport",
    "LegacyShmLatestVideoFrameTransport",
]
