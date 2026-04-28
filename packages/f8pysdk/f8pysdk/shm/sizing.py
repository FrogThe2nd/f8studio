from __future__ import annotations

from .audio import _AUDIO_HEADER_STRUCT, _CHUNK_HEADER_STRUCT, SAMPLE_FORMAT_F32LE, SAMPLE_FORMAT_S16LE
from .naming import DEFAULT_AUDIO_SHM_BYTES, DEFAULT_VIDEO_SHM_BYTES, DEFAULT_VIDEO_SHM_SLOTS
from .video import _VIDEO_HEADER_STRUCT


def video_min_bytes(slot_count: int = DEFAULT_VIDEO_SHM_SLOTS) -> int:
    slot_count = int(max(1, slot_count))
    header_bytes = _VIDEO_HEADER_STRUCT.size
    min_slot_payload = 32 * 32 * 4
    return int(header_bytes + slot_count * min_slot_payload)


def video_required_bytes(max_width: int, max_height: int, slot_count: int = DEFAULT_VIDEO_SHM_SLOTS) -> int:
    slot_count = int(max(1, slot_count))
    max_width = int(max(0, max_width))
    max_height = int(max(0, max_height))
    header_bytes = _VIDEO_HEADER_STRUCT.size
    per_frame = max_width * max_height * 4
    return int(max(video_min_bytes(slot_count), header_bytes + slot_count * per_frame))


def video_recommended_bytes(max_width: int, max_height: int, slot_count: int = DEFAULT_VIDEO_SHM_SLOTS) -> int:
    return int(max(DEFAULT_VIDEO_SHM_BYTES, video_required_bytes(max_width, max_height, slot_count)))


def audio_required_bytes(sample_rate: int, channels: int, frames_per_chunk: int, chunk_count: int, fmt: str = "f32le") -> int:
    _ = int(sample_rate)
    channels = int(channels)
    frames_per_chunk = int(frames_per_chunk)
    chunk_count = int(chunk_count)
    if channels <= 0 or frames_per_chunk <= 0 or chunk_count <= 0:
        return 0
    normalized_fmt = str(fmt).lower()
    if normalized_fmt == "f32le":
        sample_format = SAMPLE_FORMAT_F32LE
    elif normalized_fmt == "s16le":
        sample_format = SAMPLE_FORMAT_S16LE
    else:
        return 0
    bytes_per_sample = 4 if sample_format == SAMPLE_FORMAT_F32LE else 2
    header_bytes = _AUDIO_HEADER_STRUCT.size
    chunk_header_bytes = _CHUNK_HEADER_STRUCT.size
    bytes_per_frame = bytes_per_sample * channels
    payload_per_chunk = bytes_per_frame * frames_per_chunk
    chunk_stride = chunk_header_bytes + payload_per_chunk
    return int(header_bytes + chunk_stride * chunk_count)


def audio_recommended_bytes(sample_rate: int, channels: int, frames_per_chunk: int, chunk_count: int, fmt: str = "f32le") -> int:
    return int(max(DEFAULT_AUDIO_SHM_BYTES, audio_required_bytes(sample_rate, channels, frames_per_chunk, chunk_count, fmt=fmt)))
