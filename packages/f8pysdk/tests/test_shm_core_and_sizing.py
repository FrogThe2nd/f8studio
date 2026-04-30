from __future__ import annotations

import uuid

from f8pysdk.shm.audio import AudioShmReader, AudioShmWriter
from f8pysdk.shm import core
from f8pysdk.shm.sizing import audio_required_bytes, video_min_bytes, video_required_bytes
from f8pysdk.shm.video import _VIDEO_HEADER_STRUCT, VideoShmWriter


def test_open_shared_memory_readonly_uses_track_false_when_supported(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class FakeSharedMemory:
        def __init__(self, *, name: str, create: bool, size: int, track: bool):
            recorded["name"] = name
            recorded["create"] = create
            recorded["size"] = size
            recorded["track"] = track

    monkeypatch.setattr(core, "_SUPPORTS_SHARED_MEMORY_TRACK", True)
    monkeypatch.setattr(core, "SharedMemory", FakeSharedMemory)

    core.open_shared_memory_readonly("demo.region")

    assert recorded == {
        "name": "demo.region",
        "create": False,
        "size": 0,
        "track": False,
    }


def test_audio_required_bytes_rejects_unknown_format() -> None:
    assert audio_required_bytes(48_000, 2, 480, 200, fmt="pcm24") == 0


def test_video_sizing_uses_struct_header_size() -> None:
    header_bytes = _VIDEO_HEADER_STRUCT.size
    assert video_min_bytes(2) == header_bytes + (2 * 32 * 32 * 4)
    assert video_required_bytes(8, 4, 2) == max(video_min_bytes(2), header_bytes + (2 * 8 * 4 * 4))


def test_audio_shm_roundtrip_accepts_memoryview_payload() -> None:
    shm_name = f"test.shm.audio.{uuid.uuid4().hex}"
    writer = AudioShmWriter(shm_name=shm_name, size=1024 * 1024, channels=2, frames_per_chunk=4, chunk_count=4)
    reader = AudioShmReader(shm_name=shm_name)
    try:
        writer.open()
        reader.open(use_event=False)

        samples = bytes(range(32))
        writer.write_chunk_f32(memoryview(samples), frames=4)

        header, chunk, payload = reader.read_chunk_f32(1)
        assert header is not None
        assert chunk is not None
        assert payload is not None
        assert bytes(payload) == samples
        payload.release()
    finally:
        reader.close()
        writer.close(unlink=True)


def test_video_writer_open_reuses_existing_shm_name() -> None:
    shm_name = f"test.shm.video.reuse.{uuid.uuid4().hex}"
    writer_a = VideoShmWriter(shm_name=shm_name, size=1024 * 1024, slot_count=2)
    writer_b = VideoShmWriter(shm_name=shm_name, size=1024 * 1024, slot_count=2)
    try:
        writer_a.open()
        writer_b.open()
    finally:
        writer_b.close()
        writer_a.close(unlink=True)
