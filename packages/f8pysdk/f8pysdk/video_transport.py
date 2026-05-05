from __future__ import annotations

import json
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol

from .zenoh_config import apply_zenoh_shared_memory_config

log = logging.getLogger(__name__)
_SUBSCRIPTION_SETTLE_S = 0.01

ZENOH_VIDEO_FRAME_MAGIC = 0xF85A1001
ZENOH_VIDEO_FRAME_SCHEMA_VERSION = 1
VIDEO_FORMAT_BGRA32 = 1
VIDEO_FORMAT_FLOW2_F16 = 2
VIDEO_FORMAT_SCALAR1_F32 = 3

_ZENOH_VIDEO_FRAME_HEADER_STRUCT = struct.Struct("<8IQq")


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


def encode_zenoh_video_frame(
    *,
    width: int,
    height: int,
    pitch: int,
    payload: bytes | bytearray | memoryview,
    fmt: int,
    frame_id: int,
    ts_ms: int,
) -> bytes:
    width_i = int(width)
    height_i = int(height)
    pitch_i = int(pitch)
    fmt_i = int(fmt)
    frame_id_i = int(frame_id)
    ts_ms_i = int(ts_ms)
    if width_i <= 0 or height_i <= 0 or pitch_i <= 0:
        raise ValueError("width, height, and pitch must be positive")
    if fmt_i <= 0:
        raise ValueError("fmt must be positive")
    if frame_id_i <= 0:
        raise ValueError("frame_id must be positive")
    payload_view = memoryview(payload).cast("B")
    try:
        frame_bytes = pitch_i * height_i
        if len(payload_view) < frame_bytes:
            raise ValueError("payload is smaller than pitch * height")
        header = _ZENOH_VIDEO_FRAME_HEADER_STRUCT.pack(
            ZENOH_VIDEO_FRAME_MAGIC,
            ZENOH_VIDEO_FRAME_SCHEMA_VERSION,
            _ZENOH_VIDEO_FRAME_HEADER_STRUCT.size,
            width_i,
            height_i,
            pitch_i,
            fmt_i,
            frame_bytes,
            frame_id_i,
            ts_ms_i,
        )
        return header + bytes(payload_view[:frame_bytes])
    finally:
        payload_view.release()


def decode_zenoh_video_frame(raw: bytes | bytearray | memoryview) -> LatestVideoFrame | None:
    raw_view = memoryview(raw).cast("B")
    try:
        if len(raw_view) < _ZENOH_VIDEO_FRAME_HEADER_STRUCT.size:
            return None
        fields = _ZENOH_VIDEO_FRAME_HEADER_STRUCT.unpack_from(raw_view, 0)
        magic = int(fields[0])
        version = int(fields[1])
        header_bytes = int(fields[2])
        width = int(fields[3])
        height = int(fields[4])
        pitch = int(fields[5])
        fmt = int(fields[6])
        payload_bytes = int(fields[7])
        frame_id = int(fields[8])
        ts_ms = int(fields[9])
        if magic != ZENOH_VIDEO_FRAME_MAGIC or version != ZENOH_VIDEO_FRAME_SCHEMA_VERSION:
            return None
        if header_bytes < _ZENOH_VIDEO_FRAME_HEADER_STRUCT.size:
            return None
        if width <= 0 or height <= 0 or pitch <= 0 or fmt <= 0 or frame_id <= 0:
            return None
        if payload_bytes != pitch * height:
            return None
        if header_bytes + payload_bytes > len(raw_view):
            return None
        payload = raw_view[header_bytes : header_bytes + payload_bytes]
        return LatestVideoFrame(
            width=width,
            height=height,
            pitch=pitch,
            fmt=fmt,
            frame_id=frame_id,
            ts_ms=ts_ms,
            payload=payload,
        )
    except (BufferError, TypeError, ValueError, struct.error):
        return None


class ZenohLatestVideoFrameTransport:
    def __init__(
        self,
        *,
        key_expr: str,
        session: Any,
        subscriber: Any | None = None,
        publisher: Any | None = None,
    ) -> None:
        key = str(key_expr or "").strip()
        if not key:
            raise ValueError("key_expr must be non-empty")
        self.key_expr = key
        self._session = session
        self._subscriber = subscriber
        self._publisher = publisher
        self._closed = False
        self._frame_id = 0
        self._cv = threading.Condition()
        self._latest_raw: bytes | None = None
        self._latest_seq = 0
        self._delivered_seq = 0

    @classmethod
    def open_publisher(
        cls,
        key_expr: str,
        *,
        config_path: str | None = None,
        connect: tuple[str, ...] = (),
        listen: tuple[str, ...] = (),
        shm_pool_bytes: int = 256 * 1024 * 1024,
    ) -> "ZenohLatestVideoFrameTransport":
        session = _open_zenoh_session(
            config_path=config_path,
            connect=connect,
            listen=listen,
            shm_pool_bytes=shm_pool_bytes,
        )
        publisher = _declare_zenoh_latest_publisher(session, key_expr)
        return cls(key_expr=key_expr, session=session, publisher=publisher)

    @classmethod
    def open_subscriber(
        cls,
        key_expr: str,
        *,
        config_path: str | None = None,
        connect: tuple[str, ...] = (),
        listen: tuple[str, ...] = (),
        shm_pool_bytes: int = 256 * 1024 * 1024,
    ) -> "ZenohLatestVideoFrameTransport":
        session = _open_zenoh_session(
            config_path=config_path,
            connect=connect,
            listen=listen,
            shm_pool_bytes=shm_pool_bytes,
        )
        transport = cls(key_expr=key_expr, session=session)

        def _on_sample(sample: Any) -> None:
            transport._on_sample(sample)

        transport._subscriber = session.declare_subscriber(str(key_expr), _on_sample)
        time.sleep(_SUBSCRIPTION_SETTLE_S)
        return transport

    @classmethod
    def open_pubsub(
        cls,
        key_expr: str,
        *,
        config_path: str | None = None,
        connect: tuple[str, ...] = (),
        listen: tuple[str, ...] = (),
        shm_pool_bytes: int = 256 * 1024 * 1024,
    ) -> "ZenohLatestVideoFrameTransport":
        session = _open_zenoh_session(
            config_path=config_path,
            connect=connect,
            listen=listen,
            shm_pool_bytes=shm_pool_bytes,
        )
        transport = cls(key_expr=key_expr, session=session)

        def _on_sample(sample: Any) -> None:
            transport._on_sample(sample)

        transport._subscriber = session.declare_subscriber(str(key_expr), _on_sample)
        transport._publisher = _declare_zenoh_latest_publisher(session, key_expr)
        time.sleep(_SUBSCRIPTION_SETTLE_S)
        return transport

    def close(self) -> None:
        with self._cv:
            if self._closed:
                return
            self._closed = True
            self._latest_raw = None
            self._cv.notify_all()
        publisher = self._publisher
        self._publisher = None
        if publisher is not None:
            try:
                publisher.undeclare()
            except (RuntimeError, OSError) as exc:
                log.debug("zenoh video publisher undeclare failed key=%s", self.key_expr, exc_info=exc)
        subscriber = self._subscriber
        self._subscriber = None
        if subscriber is not None:
            try:
                subscriber.undeclare()
            except (RuntimeError, OSError) as exc:
                log.debug("zenoh video subscriber undeclare failed key=%s", self.key_expr, exc_info=exc)
        session = self._session
        self._session = None
        if session is not None:
            try:
                session.close()
            except (RuntimeError, OSError) as exc:
                log.debug("zenoh video session close failed key=%s", self.key_expr, exc_info=exc)

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
        session = self._session
        if self._closed or session is None:
            raise RuntimeError("zenoh video transport is closed")
        self._frame_id += 1
        raw = encode_zenoh_video_frame(
            width=width,
            height=height,
            pitch=pitch,
            payload=payload,
            fmt=fmt,
            frame_id=self._frame_id,
            ts_ms=int(ts_ms) if ts_ms is not None else int(time.time() * 1000),
        )
        try:
            import zenoh  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Zenoh video transport requires the `eclipse-zenoh` Python package") from exc
        publisher = self._publisher
        if publisher is not None:
            publisher.put(raw, encoding=zenoh.Encoding.APPLICATION_OCTET_STREAM)
            return
        session.put(
            self.key_expr,
            raw,
            encoding=zenoh.Encoding.APPLICATION_OCTET_STREAM,
            congestion_control=zenoh.CongestionControl.DROP,
            priority=zenoh.Priority.REAL_TIME,
            express=True,
        )

    def poll_latest(self) -> LatestVideoFrame | None:
        with self._cv:
            if self._latest_seq == self._delivered_seq or self._latest_raw is None:
                return None
            raw = self._latest_raw
            seq = self._latest_seq
            self._delivered_seq = seq
        return decode_zenoh_video_frame(raw)

    def wait_latest(self, timeout_ms: int) -> LatestVideoFrame | None:
        frame = self.poll_latest()
        if frame is not None:
            return frame
        timeout_s = max(0.0, float(int(timeout_ms)) / 1000.0)
        deadline = time.monotonic() + timeout_s
        with self._cv:
            while not self._closed:
                if self._latest_seq != self._delivered_seq and self._latest_raw is not None:
                    raw = self._latest_raw
                    seq = self._latest_seq
                    self._delivered_seq = seq
                    return decode_zenoh_video_frame(raw)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(timeout=remaining)
        return None

    def _on_sample(self, sample: Any) -> None:
        try:
            raw = bytes(sample.payload)
        except (TypeError, ValueError) as exc:
            log.debug("zenoh video sample decode failed key=%s", self.key_expr, exc_info=exc)
            return
        with self._cv:
            if self._closed:
                return
            self._latest_raw = raw
            self._latest_seq += 1
            self._cv.notify_all()


def _open_zenoh_session(
    *,
    config_path: str | None,
    connect: tuple[str, ...],
    listen: tuple[str, ...],
    shm_pool_bytes: int,
) -> Any:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh video transport requires the `eclipse-zenoh` Python package") from exc
    if config_path:
        config = zenoh.Config.from_file(str(config_path))
    else:
        config = zenoh.Config()
    connect_items = tuple(str(item).strip() for item in connect if str(item).strip())
    listen_items = tuple(str(item).strip() for item in listen if str(item).strip())
    if connect_items:
        config.insert_json5("connect/endpoints", json.dumps(list(connect_items)))
    if listen_items:
        config.insert_json5("listen/endpoints", json.dumps(list(listen_items)))
    apply_zenoh_shared_memory_config(
        config,
        zenoh_module=zenoh,
        shm_pool_bytes=int(shm_pool_bytes),
        log_context="video",
    )
    return zenoh.open(config)


def _declare_zenoh_latest_publisher(session: Any, key_expr: str) -> Any:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh video transport requires the `eclipse-zenoh` Python package") from exc
    return session.declare_publisher(
        str(key_expr),
        encoding=zenoh.Encoding.APPLICATION_OCTET_STREAM,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.REAL_TIME,
        express=True,
        reliability=zenoh.Reliability.BEST_EFFORT,
    )


__all__ = [
    "LatestVideoFrame",
    "LatestVideoFrameTransport",
    "VIDEO_FORMAT_BGRA32",
    "VIDEO_FORMAT_FLOW2_F16",
    "VIDEO_FORMAT_SCALAR1_F32",
    "ZENOH_VIDEO_FRAME_MAGIC",
    "ZENOH_VIDEO_FRAME_SCHEMA_VERSION",
    "ZenohLatestVideoFrameTransport",
    "decode_zenoh_video_frame",
    "encode_zenoh_video_frame",
]
