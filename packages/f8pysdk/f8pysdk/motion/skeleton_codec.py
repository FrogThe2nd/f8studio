from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

from .models import SkeletonAnimation, SkeletonBone, SkeletonIdentity, SkeletonPacket, SkeletonTrailer

_MAX_DATAGRAM_BYTES = 4 * 1024 * 1024
_MAX_STRING_BYTES = 64 * 1024
_MAX_BONE_COUNT = 100_000
_LMEX_MAGIC = b"LMEX"
_ANIM_MAGIC = b"ANIM"


class SkeletonPacketDecodeError(ValueError):
    """Raised when a datagram is not a valid F8 skeleton packet."""


@dataclass(slots=True)
class _Reader:
    data: bytes
    offset: int = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def read_bytes(self, size: int, label: str) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise SkeletonPacketDecodeError(f"Truncated {label} at byte {self.offset}")
        value = self.data[self.offset : self.offset + size]
        self.offset += size
        return value

    def read_struct(self, format_string: str, label: str) -> tuple[Any, ...]:
        size = struct.calcsize(format_string)
        raw = self.read_bytes(size, label)
        try:
            return struct.unpack(format_string, raw)
        except struct.error as exc:
            raise SkeletonPacketDecodeError(f"Invalid {label}: {exc}") from exc

    def read_aligned_string(self, label: str) -> str:
        terminator = self.data.find(b"\x00", self.offset)
        if terminator < 0:
            raise SkeletonPacketDecodeError(f"Missing terminator for {label} at byte {self.offset}")
        raw_size = terminator - self.offset
        if raw_size > _MAX_STRING_BYTES:
            raise SkeletonPacketDecodeError(f"{label} exceeds {_MAX_STRING_BYTES} bytes")
        raw = self.data[self.offset:terminator]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkeletonPacketDecodeError(f"Invalid UTF-8 in {label}: {exc}") from exc
        self.offset = terminator + 1
        padding = (4 - (self.offset & 0x03)) & 0x03
        padding_bytes = self.read_bytes(padding, f"{label} alignment")
        if any(padding_bytes):
            raise SkeletonPacketDecodeError(f"Non-zero alignment bytes after {label}")
        return value


def decode_skeleton_packet(raw: bytes | bytearray | memoryview) -> SkeletonPacket:
    data = bytes(raw)
    if not data:
        raise SkeletonPacketDecodeError("Skeleton packet is empty")
    if len(data) > _MAX_DATAGRAM_BYTES:
        raise SkeletonPacketDecodeError(f"Skeleton packet exceeds {_MAX_DATAGRAM_BYTES} bytes")

    reader = _Reader(data=data)
    model_name = reader.read_aligned_string("modelName")
    if not model_name.strip():
        raise SkeletonPacketDecodeError("modelName is empty")
    (timestamp_ms,) = reader.read_struct("<Q", "timestampMs")
    schema = reader.read_aligned_string("schema")
    if not schema.strip():
        raise SkeletonPacketDecodeError("schema is empty")
    (bone_count,) = reader.read_struct("<i", "boneCount")
    if bone_count < 0 or bone_count > _MAX_BONE_COUNT:
        raise SkeletonPacketDecodeError(f"boneCount is outside 0..{_MAX_BONE_COUNT}: {bone_count}")

    bones: list[SkeletonBone] = []
    for bone_index in range(bone_count):
        bone_name = reader.read_aligned_string(f"bones[{bone_index}].name")
        values = reader.read_struct("<fffffff", f"bones[{bone_index}].transform")
        x, y, z, qw, qx, qy, qz = values
        bones.append(
            SkeletonBone(
                name=bone_name,
                position=(float(x), float(y), float(z)),
                rotation=(float(qw), float(qx), float(qy), float(qz)),
            )
        )

    trailer = _decode_trailer(reader)
    if reader.remaining != 0:
        raise SkeletonPacketDecodeError(f"Unexpected {reader.remaining} trailing bytes at byte {reader.offset}")
    return SkeletonPacket(
        model_name=model_name,
        timestamp_ms=int(timestamp_ms),
        schema=schema,
        bones=tuple(bones),
        trailer=trailer,
    )


def _decode_trailer(reader: _Reader) -> SkeletonTrailer | None:
    if reader.remaining == 0:
        return None
    magic = reader.read_bytes(4, "trailer magic")
    if magic != _LMEX_MAGIC:
        raise SkeletonPacketDecodeError(f"Unexpected trailer magic {magic!r}")
    extension_version, frame_id, chunk_index, chunk_count, total_bone_count, character_id = reader.read_struct(
        "<HQiiii", "LMEX trailer"
    )
    if extension_version not in (1, 2):
        raise SkeletonPacketDecodeError(f"Unsupported LMEX extension version: {extension_version}")
    if chunk_count <= 0:
        raise SkeletonPacketDecodeError(f"chunkCount must be positive: {chunk_count}")
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise SkeletonPacketDecodeError(f"chunkIndex {chunk_index} is outside chunkCount {chunk_count}")
    if total_bone_count < 0 or total_bone_count > _MAX_BONE_COUNT:
        raise SkeletonPacketDecodeError(f"Invalid totalBoneCount: {total_bone_count}")

    identity: SkeletonIdentity | None = None
    if extension_version >= 2:
        profile_id = reader.read_aligned_string("identity.profileId")
        role = reader.read_aligned_string("identity.role")
        (role_index,) = reader.read_struct("<i", "identity.roleIndex")
        exporter_version = reader.read_aligned_string("identity.exporterVersion")
        identity = SkeletonIdentity(
            profile_id=profile_id,
            role=role,
            role_index=int(role_index),
            exporter_version=exporter_version,
        )

    animation: SkeletonAnimation | None = None
    if reader.remaining:
        animation_magic = reader.read_bytes(4, "animation magic")
        if animation_magic != _ANIM_MAGIC:
            raise SkeletonPacketDecodeError(f"Unexpected animation magic {animation_magic!r}")
        (normalized_time,) = reader.read_struct("<f", "animation.normalizedTime")
        (layer_index,) = reader.read_struct("<i", "animation.layerIndex")
        clip_name = reader.read_aligned_string("animation.clipName")
        pose_key = reader.read_aligned_string("animation.poseKey")
        animation = SkeletonAnimation(
            normalized_time=float(normalized_time),
            layer_index=int(layer_index),
            clip_name=clip_name,
            pose_key=pose_key,
        )

    return SkeletonTrailer(
        extension_version=int(extension_version),
        frame_id=int(frame_id),
        chunk_index=int(chunk_index),
        chunk_count=int(chunk_count),
        total_bone_count=int(total_bone_count),
        character_id=int(character_id),
        identity=identity,
        animation=animation,
    )


def decode_skeleton_datagram(raw: bytes | bytearray | memoryview) -> dict[str, Any]:
    data = bytes(raw)
    stripped = data.lstrip()
    if stripped.startswith((b"{", b"[")):
        try:
            decoded = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkeletonPacketDecodeError(f"Invalid skeleton JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise SkeletonPacketDecodeError("Skeleton JSON root must be an object")
        return {str(key): value for key, value in decoded.items()}
    return decode_skeleton_packet(data).to_payload()


__all__ = ["SkeletonPacketDecodeError", "decode_skeleton_datagram", "decode_skeleton_packet"]
