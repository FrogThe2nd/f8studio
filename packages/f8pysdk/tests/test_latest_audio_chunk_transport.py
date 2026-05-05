from __future__ import annotations

import struct
from dataclasses import dataclass

import pytest

from f8pysdk.audio_transport import (
    ZENOH_AUDIO_CHUNK_MAGIC,
    ZENOH_AUDIO_CHUNK_SCHEMA_VERSION,
    ZenohLatestAudioChunkTransport,
    _declare_zenoh_latest_publisher,
    decode_zenoh_audio_chunk,
    encode_zenoh_audio_chunk,
)
from f8pysdk.shm.audio import SAMPLE_FORMAT_F32LE


class _Session:
    def close(self) -> None:
        return


class _PublisherSession:
    def __init__(self) -> None:
        self.key_expr = ""
        self.options: dict[str, object] = {}

    def declare_publisher(self, key_expr: str, **options: object) -> object:
        self.key_expr = str(key_expr)
        self.options = dict(options)
        return object()


@dataclass(frozen=True)
class _Sample:
    payload: bytes


def _payload(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def test_zenoh_audio_chunk_roundtrip() -> None:
    payload = _payload(0.25, -0.25, 0.5, -0.5)
    raw = encode_zenoh_audio_chunk(
        sample_rate=48_000,
        channels=2,
        payload=payload,
        frames=2,
        fmt=SAMPLE_FORMAT_F32LE,
        seq=7,
        frame_index=960,
        ts_ms=1234,
    )

    magic, version = struct.unpack_from("<II", raw, 0)
    assert magic == ZENOH_AUDIO_CHUNK_MAGIC
    assert version == ZENOH_AUDIO_CHUNK_SCHEMA_VERSION

    chunk = decode_zenoh_audio_chunk(raw)
    assert chunk is not None
    try:
        assert chunk.sample_rate == 48_000
        assert chunk.channels == 2
        assert chunk.fmt == SAMPLE_FORMAT_F32LE
        assert chunk.frames == 2
        assert chunk.bytes_per_frame == 8
        assert chunk.seq == 7
        assert chunk.frame_index == 960
        assert chunk.ts_ms == 1234
        assert chunk.payload_copy() == payload
    finally:
        chunk.release()


def test_zenoh_audio_declared_publisher_uses_latest_chunk_qos() -> None:
    zenoh = pytest.importorskip("zenoh")
    session = _PublisherSession()

    publisher = _declare_zenoh_latest_publisher(session, "f8/test/audio/qos")

    assert publisher is not None
    assert session.key_expr == "f8/test/audio/qos"
    assert session.options["encoding"] == zenoh.Encoding.APPLICATION_OCTET_STREAM
    assert session.options["congestion_control"] == zenoh.CongestionControl.DROP
    assert session.options["priority"] == zenoh.Priority.REAL_TIME
    assert session.options["express"] is True
    assert session.options["reliability"] == zenoh.Reliability.BEST_EFFORT


def test_zenoh_audio_subscriber_keeps_latest_chunk_only() -> None:
    transport = ZenohLatestAudioChunkTransport(
        key_expr="f8/svc/audiocap/nodes/audiocap/data/audio",
        session=_Session(),
    )
    raw_a = encode_zenoh_audio_chunk(
        sample_rate=48_000,
        channels=1,
        payload=_payload(0.1, 0.2),
        frames=2,
        fmt=SAMPLE_FORMAT_F32LE,
        seq=1,
        frame_index=2,
        ts_ms=100,
    )
    raw_b = encode_zenoh_audio_chunk(
        sample_rate=48_000,
        channels=1,
        payload=_payload(0.3, 0.4),
        frames=2,
        fmt=SAMPLE_FORMAT_F32LE,
        seq=2,
        frame_index=4,
        ts_ms=120,
    )

    transport._on_sample(_Sample(raw_a))
    transport._on_sample(_Sample(raw_b))
    chunk = transport.poll_latest()
    assert chunk is not None
    try:
        assert chunk.seq == 2
        assert chunk.frame_index == 4
        assert chunk.payload_copy() == _payload(0.3, 0.4)
    finally:
        chunk.release()
        transport.close()

    assert transport.poll_latest() is None
