from __future__ import annotations

import os
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal

from f8pysdk.bus import ServiceBus, ServiceBusConfig
from f8pysdk.shm.video import VideoShmHeader, VideoShmReader
from f8pysdk.video_transport import LatestVideoFrame, ZenohLatestVideoFrameTransport

VideoSourceTransport = Literal["zenoh", "legacy_shm"]


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    items: list[str] = []
    for part in str(raw or "").split(","):
        item = part.strip()
        if item:
            items.append(item)
    return tuple(items)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return int(default)
    try:
        value = int(raw.strip())
    except ValueError:
        return int(default)
    return max(0, value)


@dataclass(frozen=True)
class VideoFrameSourceConfig:
    config_path: str | None = None
    connect: tuple[str, ...] = ()
    listen: tuple[str, ...] = ()
    shm_pool_bytes: int = 256 * 1024 * 1024

    @classmethod
    def from_bus(cls, bus: Any | None) -> "VideoFrameSourceConfig":
        if isinstance(bus, ServiceBus):
            cfg: ServiceBusConfig = bus.config
            return cls(
                config_path=cfg.zenoh_config_path,
                connect=cfg.zenoh_connect,
                listen=cfg.zenoh_listen,
                shm_pool_bytes=cfg.zenoh_shm_pool_bytes,
            )
        config_path_raw = os.environ.get("F8_ZENOH_CONFIG", "").strip()
        return cls(
            config_path=config_path_raw or None,
            connect=_env_tuple("F8_ZENOH_CONNECT"),
            listen=_env_tuple("F8_ZENOH_LISTEN"),
            shm_pool_bytes=_env_int("F8_ZENOH_SHM_POOL_BYTES", 256 * 1024 * 1024),
        )


@dataclass
class VideoFramePacket:
    width: int
    height: int
    pitch: int
    fmt: int
    frame_id: int
    ts_ms: int
    payload: memoryview
    _released: bool = False

    @property
    def frame_bytes(self) -> int:
        return int(self.pitch) * int(self.height)

    def release(self) -> None:
        if self._released:
            return
        self.payload.release()
        self._released = True

    def __enter__(self) -> "VideoFramePacket":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class LatestVideoFrameSource:
    def __init__(self, *, config: VideoFrameSourceConfig) -> None:
        self._config = config
        self._shm_reader: VideoShmReader | None = None
        self._shm_open_name = ""
        self._zenoh_reader: ZenohLatestVideoFrameTransport | None = None
        self._zenoh_open_key = ""
        self._last_signature: tuple[VideoSourceTransport, str, int, int] | None = None

    def close(self) -> None:
        self._close_shm()
        self._close_zenoh()
        self._last_signature = None

    def reset(self) -> None:
        self.close()

    def read_latest(
        self,
        *,
        video_transport: str,
        video_key: str,
        shm_name: str,
        timeout_ms: int,
        dedupe: bool = True,
    ) -> VideoFramePacket | None:
        transport = self._select_transport(video_transport=video_transport, video_key=video_key, shm_name=shm_name)
        if transport == "zenoh":
            return self._read_zenoh_latest(video_key=str(video_key).strip(), timeout_ms=timeout_ms, dedupe=dedupe)
        return self._read_shm_latest(shm_name=str(shm_name).strip(), timeout_ms=timeout_ms, dedupe=dedupe)

    @staticmethod
    def _select_transport(*, video_transport: str, video_key: str, shm_name: str) -> VideoSourceTransport:
        transport = str(video_transport or "").strip().lower()
        key = str(video_key or "").strip()
        if transport == "zenoh":
            if key:
                return "zenoh"
            return "legacy_shm"
        if transport in ("legacy_shm", "shm"):
            return "legacy_shm"
        if key:
            return "zenoh"
        _ = shm_name
        return "legacy_shm"

    def _read_zenoh_latest(self, *, video_key: str, timeout_ms: int, dedupe: bool) -> VideoFramePacket | None:
        if not video_key:
            return None
        reader = self._ensure_zenoh_reader(video_key)
        frame = reader.wait_latest(max(0, int(timeout_ms)))
        if frame is None:
            return None
        return self._packet_from_zenoh(video_key=video_key, frame=frame, dedupe=dedupe)

    def _packet_from_zenoh(
        self, *, video_key: str, frame: LatestVideoFrame, dedupe: bool
    ) -> VideoFramePacket | None:
        signature = ("zenoh", str(video_key), int(frame.frame_id), int(frame.ts_ms))
        if dedupe and signature == self._last_signature:
            frame.release()
            return None
        if dedupe:
            self._last_signature = signature
        return VideoFramePacket(
            width=int(frame.width),
            height=int(frame.height),
            pitch=int(frame.pitch),
            fmt=int(frame.fmt),
            frame_id=int(frame.frame_id),
            ts_ms=int(frame.ts_ms),
            payload=frame.payload,
        )

    def _read_shm_latest(self, *, shm_name: str, timeout_ms: int, dedupe: bool) -> VideoFramePacket | None:
        if not shm_name:
            return None
        reader = self._ensure_shm_reader(shm_name)
        reader.wait_new_frame(timeout_ms=max(0, int(timeout_ms)))
        header, payload = reader.read_latest_frame()
        if header is None or payload is None:
            return None
        return self._packet_from_shm(shm_name=shm_name, header=header, payload=payload, dedupe=dedupe)

    def _packet_from_shm(
        self,
        *,
        shm_name: str,
        header: VideoShmHeader,
        payload: memoryview,
        dedupe: bool,
    ) -> VideoFramePacket | None:
        signature = ("legacy_shm", str(shm_name), int(header.frame_id), int(header.ts_ms))
        if dedupe and signature == self._last_signature:
            payload.release()
            return None
        if dedupe:
            self._last_signature = signature
        return VideoFramePacket(
            width=int(header.width),
            height=int(header.height),
            pitch=int(header.pitch),
            fmt=int(header.fmt),
            frame_id=int(header.frame_id),
            ts_ms=int(header.ts_ms),
            payload=payload,
        )

    def _ensure_zenoh_reader(self, key_expr: str) -> ZenohLatestVideoFrameTransport:
        key = str(key_expr or "").strip()
        if self._zenoh_reader is not None and self._zenoh_open_key == key:
            return self._zenoh_reader
        self._close_zenoh()
        reader = ZenohLatestVideoFrameTransport.open_subscriber(
            key,
            config_path=self._config.config_path,
            connect=self._config.connect,
            listen=self._config.listen,
            shm_pool_bytes=self._config.shm_pool_bytes,
        )
        self._zenoh_reader = reader
        self._zenoh_open_key = key
        self._last_signature = None
        return reader

    def _ensure_shm_reader(self, shm_name: str) -> VideoShmReader:
        name = str(shm_name or "").strip()
        if self._shm_reader is not None and self._shm_open_name == name:
            return self._shm_reader
        self._close_shm()
        reader = VideoShmReader(name)
        reader.open(use_event=True)
        self._shm_reader = reader
        self._shm_open_name = name
        self._last_signature = None
        return reader

    def _close_zenoh(self) -> None:
        reader = self._zenoh_reader
        self._zenoh_reader = None
        self._zenoh_open_key = ""
        if reader is not None:
            reader.close()

    def _close_shm(self) -> None:
        reader = self._shm_reader
        self._shm_reader = None
        self._shm_open_name = ""
        if reader is not None:
            reader.close()
