from __future__ import annotations

import uuid

from f8pysdk.shm import VIDEO_FORMAT_BGRA32
from f8pysdk.video_transport import LegacyShmLatestVideoFrameTransport


def _shm_name(prefix: str) -> str:
    return f"test.{prefix}.{uuid.uuid4().hex}"


def test_legacy_shm_latest_video_frame_transport_roundtrip() -> None:
    shm_name = _shm_name("latest_video")
    writer = LegacyShmLatestVideoFrameTransport.open_writer(shm_name, size=1024 * 1024, slot_count=2)
    reader = LegacyShmLatestVideoFrameTransport.open_reader(shm_name, use_event=False)
    try:
        width = 4
        height = 3
        pitch = width * 4
        payload = bytes(range(pitch * height))

        writer.publish_frame(width=width, height=height, pitch=pitch, payload=payload, fmt=VIDEO_FORMAT_BGRA32)

        frame = reader.poll_latest()
        assert frame is not None
        try:
            assert frame.width == width
            assert frame.height == height
            assert frame.pitch == pitch
            assert frame.fmt == VIDEO_FORMAT_BGRA32
            assert frame.frame_id == 1
            assert frame.payload_bytes() == payload
        finally:
            frame.release()

        assert reader.poll_latest() is None

        next_payload = bytes((255 - (i % 251) for i in range(pitch * height)))
        writer.publish_frame(width=width, height=height, pitch=pitch, payload=next_payload, fmt=VIDEO_FORMAT_BGRA32)
        next_frame = reader.wait_latest(timeout_ms=100)
        assert next_frame is not None
        try:
            assert next_frame.frame_id == 2
            assert next_frame.payload_bytes() == next_payload
        finally:
            next_frame.release()
    finally:
        reader.close()
        writer.close(unlink=True)


def test_legacy_shm_latest_video_frame_transport_skips_backlog() -> None:
    shm_name = _shm_name("latest_video_backlog")
    writer = LegacyShmLatestVideoFrameTransport.open_writer(shm_name, size=1024 * 1024, slot_count=2)
    reader = LegacyShmLatestVideoFrameTransport.open_reader(shm_name, use_event=False)
    try:
        width = 2
        height = 2
        pitch = width * 4
        latest_payload = b""
        for seq in range(5):
            latest_payload = bytes(((seq + i) % 256 for i in range(pitch * height)))
            writer.publish_frame(
                width=width,
                height=height,
                pitch=pitch,
                payload=latest_payload,
                fmt=VIDEO_FORMAT_BGRA32,
            )

        frame = reader.poll_latest()
        assert frame is not None
        try:
            assert frame.frame_id == 5
            assert frame.payload_bytes() == latest_payload
        finally:
            frame.release()

        assert reader.poll_latest() is None
    finally:
        reader.close()
        writer.close(unlink=True)


def test_legacy_shm_latest_video_frame_transport_rejects_wrong_mode() -> None:
    shm_name = _shm_name("latest_video_mode")
    writer = LegacyShmLatestVideoFrameTransport.open_writer(shm_name, size=1024 * 1024, slot_count=2)
    try:
        try:
            writer.poll_latest()
        except RuntimeError as exc:
            assert "not opened for reading" in str(exc)
        else:
            raise AssertionError("poll_latest should reject writer-only transports")
    finally:
        writer.close(unlink=True)
