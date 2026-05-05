from __future__ import annotations

import json
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol

from .shm.audio import AudioShmReader, SAMPLE_FORMAT_F32LE

log = logging.getLogger(__name__)
_SUBSCRIPTION_SETTLE_S = 0.01

ZENOH_AUDIO_CHUNK_MAGIC = 0xF85A2001
ZENOH_AUDIO_CHUNK_SCHEMA_VERSION = 1

_ZENOH_AUDIO_CHUNK_HEADER_STRUCT = struct.Struct("<9IQQq")


@dataclass
class LatestAudioChunk:
    sample_rate: int
    channels: int
    fmt: int
    frames: int
    bytes_per_frame: int
    seq: int
    frame_index: int
    ts_ms: int
    payload: memoryview
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def payload_bytes(self) -> int:
        return int(self.frames) * int(self.bytes_per_frame)

    def payload_copy(self) -> bytes:
        if self._released:
            raise RuntimeError("audio chunk payload has been released")
        return bytes(self.payload)

    def release(self) -> None:
        if self._released:
            return
        self.payload.release()
        self._released = True

    def __enter__(self) -> "LatestAudioChunk":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class LatestAudioChunkTransport(Protocol):
    def close(self) -> None: ...

    def publish_chunk(
        self,
        *,
        sample_rate: int,
        channels: int,
        payload: bytes | bytearray | memoryview,
        frames: int,
        fmt: int = SAMPLE_FORMAT_F32LE,
        ts_ms: int | None = None,
    ) -> None: ...

    def poll_latest(self) -> LatestAudioChunk | None: ...

    def wait_latest(self, timeout_ms: int) -> LatestAudioChunk | None: ...


class LegacyShmLatestAudioChunkTransport:
    def __init__(self, *, reader: AudioShmReader) -> None:
        self._reader = reader
        self._last_seq = 0

    @classmethod
    def open_reader(cls, shm_name: str, *, use_event: bool = True) -> "LegacyShmLatestAudioChunkTransport":
        reader = AudioShmReader(str(shm_name))
        reader.open(use_event=bool(use_event))
        return cls(reader=reader)

    @property
    def reader(self) -> AudioShmReader:
        return self._reader

    def close(self) -> None:
        self._reader.close()
        self._last_seq = 0

    def publish_chunk(
        self,
        *,
        sample_rate: int,
        channels: int,
        payload: bytes | bytearray | memoryview,
        frames: int,
        fmt: int = SAMPLE_FORMAT_F32LE,
        ts_ms: int | None = None,
    ) -> None:
        del sample_rate, channels, payload, frames, fmt, ts_ms
        raise RuntimeError("legacy SHM audio transport is not opened for writing")

    def poll_latest(self) -> LatestAudioChunk | None:
        header = self._reader.read_header()
        if header is None:
            return None
        seq = int(header.write_seq)
        if seq <= 0 or seq == int(self._last_seq):
            return None
        hdr2, chunk_header, payload = self._reader.read_chunk_f32(seq)
        if hdr2 is None or chunk_header is None or payload is None:
            return None
        self._last_seq = seq
        return LatestAudioChunk(
            sample_rate=int(hdr2.sample_rate),
            channels=int(hdr2.channels),
            fmt=int(hdr2.fmt),
            frames=int(chunk_header.frames),
            bytes_per_frame=int(hdr2.bytes_per_frame),
            seq=int(chunk_header.seq),
            frame_index=int(hdr2.write_frame_index),
            ts_ms=int(chunk_header.ts_ms),
            payload=payload,
        )

    def wait_latest(self, timeout_ms: int) -> LatestAudioChunk | None:
        chunk = self.poll_latest()
        if chunk is not None:
            return chunk
        timeout_ms_i = max(0, int(timeout_ms))
        deadline = time.monotonic() + (float(timeout_ms_i) / 1000.0)
        while True:
            remaining_ms = int(max(0.0, (deadline - time.monotonic()) * 1000.0))
            if remaining_ms <= 0:
                return None
            self._reader.wait_new_chunk(timeout_ms=min(remaining_ms, 10))
            chunk = self.poll_latest()
            if chunk is not None:
                return chunk


def encode_zenoh_audio_chunk(
    *,
    sample_rate: int,
    channels: int,
    payload: bytes | bytearray | memoryview,
    frames: int,
    fmt: int,
    seq: int,
    frame_index: int,
    ts_ms: int,
) -> bytes:
    sample_rate_i = int(sample_rate)
    channels_i = int(channels)
    fmt_i = int(fmt)
    frames_i = int(frames)
    seq_i = int(seq)
    frame_index_i = int(frame_index)
    ts_ms_i = int(ts_ms)
    if sample_rate_i <= 0 or channels_i <= 0 or frames_i <= 0:
        raise ValueError("sample_rate, channels, and frames must be positive")
    if fmt_i <= 0:
        raise ValueError("fmt must be positive")
    if seq_i <= 0:
        raise ValueError("seq must be positive")
    bytes_per_frame = 4 * channels_i if fmt_i == int(SAMPLE_FORMAT_F32LE) else 0
    if bytes_per_frame <= 0:
        raise ValueError("only f32le audio chunks are supported by schema v1")
    payload_view = memoryview(payload).cast("B")
    try:
        payload_bytes = frames_i * bytes_per_frame
        if len(payload_view) < payload_bytes:
            raise ValueError("payload is smaller than frames * bytes_per_frame")
        header = _ZENOH_AUDIO_CHUNK_HEADER_STRUCT.pack(
            ZENOH_AUDIO_CHUNK_MAGIC,
            ZENOH_AUDIO_CHUNK_SCHEMA_VERSION,
            _ZENOH_AUDIO_CHUNK_HEADER_STRUCT.size,
            sample_rate_i,
            channels_i,
            fmt_i,
            frames_i,
            bytes_per_frame,
            payload_bytes,
            seq_i,
            frame_index_i,
            ts_ms_i,
        )
        return header + bytes(payload_view[:payload_bytes])
    finally:
        payload_view.release()


def decode_zenoh_audio_chunk(raw: bytes | bytearray | memoryview) -> LatestAudioChunk | None:
    raw_view = memoryview(raw).cast("B")
    try:
        if len(raw_view) < _ZENOH_AUDIO_CHUNK_HEADER_STRUCT.size:
            return None
        fields = _ZENOH_AUDIO_CHUNK_HEADER_STRUCT.unpack_from(raw_view, 0)
        magic = int(fields[0])
        version = int(fields[1])
        header_bytes = int(fields[2])
        sample_rate = int(fields[3])
        channels = int(fields[4])
        fmt = int(fields[5])
        frames = int(fields[6])
        bytes_per_frame = int(fields[7])
        payload_bytes = int(fields[8])
        seq = int(fields[9])
        frame_index = int(fields[10])
        ts_ms = int(fields[11])
        if magic != ZENOH_AUDIO_CHUNK_MAGIC or version != ZENOH_AUDIO_CHUNK_SCHEMA_VERSION:
            return None
        if header_bytes < _ZENOH_AUDIO_CHUNK_HEADER_STRUCT.size:
            return None
        if sample_rate <= 0 or channels <= 0 or fmt <= 0 or frames <= 0 or bytes_per_frame <= 0 or seq <= 0:
            return None
        if payload_bytes != frames * bytes_per_frame:
            return None
        if header_bytes + payload_bytes > len(raw_view):
            return None
        payload = raw_view[header_bytes : header_bytes + payload_bytes]
        return LatestAudioChunk(
            sample_rate=sample_rate,
            channels=channels,
            fmt=fmt,
            frames=frames,
            bytes_per_frame=bytes_per_frame,
            seq=seq,
            frame_index=frame_index,
            ts_ms=ts_ms,
            payload=payload,
        )
    except (BufferError, TypeError, ValueError, struct.error):
        return None


class ZenohLatestAudioChunkTransport:
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
        self._seq = 0
        self._frame_index = 0
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
    ) -> "ZenohLatestAudioChunkTransport":
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
    ) -> "ZenohLatestAudioChunkTransport":
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
                log.debug("zenoh audio publisher undeclare failed key=%s", self.key_expr, exc_info=exc)
        subscriber = self._subscriber
        self._subscriber = None
        if subscriber is not None:
            try:
                subscriber.undeclare()
            except (RuntimeError, OSError) as exc:
                log.debug("zenoh audio subscriber undeclare failed key=%s", self.key_expr, exc_info=exc)
        session = self._session
        self._session = None
        if session is not None:
            try:
                session.close()
            except (RuntimeError, OSError) as exc:
                log.debug("zenoh audio session close failed key=%s", self.key_expr, exc_info=exc)

    def publish_chunk(
        self,
        *,
        sample_rate: int,
        channels: int,
        payload: bytes | bytearray | memoryview,
        frames: int,
        fmt: int = SAMPLE_FORMAT_F32LE,
        ts_ms: int | None = None,
    ) -> None:
        session = self._session
        if self._closed or session is None:
            raise RuntimeError("zenoh audio transport is closed")
        frames_i = int(frames)
        self._seq += 1
        self._frame_index += max(0, frames_i)
        raw = encode_zenoh_audio_chunk(
            sample_rate=int(sample_rate),
            channels=int(channels),
            payload=payload,
            frames=frames_i,
            fmt=int(fmt),
            seq=int(self._seq),
            frame_index=int(self._frame_index),
            ts_ms=int(ts_ms) if ts_ms is not None else int(time.time() * 1000),
        )
        try:
            import zenoh  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Zenoh audio transport requires the `eclipse-zenoh` Python package") from exc
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

    def poll_latest(self) -> LatestAudioChunk | None:
        with self._cv:
            if self._latest_seq == self._delivered_seq or self._latest_raw is None:
                return None
            raw = self._latest_raw
            seq = self._latest_seq
            self._delivered_seq = seq
        return decode_zenoh_audio_chunk(raw)

    def wait_latest(self, timeout_ms: int) -> LatestAudioChunk | None:
        chunk = self.poll_latest()
        if chunk is not None:
            return chunk
        timeout_s = max(0.0, float(int(timeout_ms)) / 1000.0)
        deadline = time.monotonic() + timeout_s
        with self._cv:
            while not self._closed:
                if self._latest_seq != self._delivered_seq and self._latest_raw is not None:
                    raw = self._latest_raw
                    seq = self._latest_seq
                    self._delivered_seq = seq
                    return decode_zenoh_audio_chunk(raw)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(timeout=remaining)
        return None

    def _on_sample(self, sample: Any) -> None:
        try:
            raw = bytes(sample.payload)
        except (TypeError, ValueError) as exc:
            log.debug("zenoh audio sample decode failed key=%s", self.key_expr, exc_info=exc)
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
        raise RuntimeError("Zenoh audio transport requires the `eclipse-zenoh` Python package") from exc
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
    config.insert_json5("transport/shared_memory/enabled", "true")
    if int(shm_pool_bytes) > 0:
        try:
            config.insert_json5("transport/shared_memory/pool_size", json.dumps(int(shm_pool_bytes)))
        except zenoh.ZError as exc:
            log.debug("zenoh Python config does not expose shared-memory pool_size", exc_info=exc)
    return zenoh.open(config)


def _declare_zenoh_latest_publisher(session: Any, key_expr: str) -> Any:
    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Zenoh audio transport requires the `eclipse-zenoh` Python package") from exc
    return session.declare_publisher(
        str(key_expr),
        encoding=zenoh.Encoding.APPLICATION_OCTET_STREAM,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.REAL_TIME,
        express=True,
        reliability=zenoh.Reliability.BEST_EFFORT,
    )


__all__ = [
    "LatestAudioChunk",
    "LatestAudioChunkTransport",
    "LegacyShmLatestAudioChunkTransport",
    "ZENOH_AUDIO_CHUNK_MAGIC",
    "ZENOH_AUDIO_CHUNK_SCHEMA_VERSION",
    "ZenohLatestAudioChunkTransport",
    "decode_zenoh_audio_chunk",
    "encode_zenoh_audio_chunk",
]
