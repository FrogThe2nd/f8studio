from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SkeletonBone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "pos": list(self.position),
            "rot": list(self.rotation),
        }


@dataclass(frozen=True, slots=True)
class SkeletonAnimation:
    normalized_time: float
    layer_index: int
    clip_name: str
    pose_key: str

    def to_payload(self) -> dict[str, object]:
        return {
            "normalizedTime": self.normalized_time,
            "layerIndex": self.layer_index,
            "clipName": self.clip_name,
            "poseKey": self.pose_key,
        }


@dataclass(frozen=True, slots=True)
class SkeletonIdentity:
    profile_id: str
    role: str
    role_index: int
    exporter_version: str

    @property
    def stable_key(self) -> str:
        profile_id = self.profile_id.strip()
        role = self.role.strip().lower()
        if not profile_id or not role or self.role_index < 0:
            return ""
        return f"{profile_id}:{role}:{self.role_index}"

    def to_payload(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "role": self.role,
            "roleIndex": self.role_index,
            "exporterVersion": self.exporter_version,
            "stableKey": self.stable_key,
        }


@dataclass(frozen=True, slots=True)
class SkeletonTrailer:
    extension_version: int
    frame_id: int
    chunk_index: int
    chunk_count: int
    total_bone_count: int
    character_id: int
    identity: SkeletonIdentity | None = None
    animation: SkeletonAnimation | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "magic": "LMEX",
            "extVersion": self.extension_version,
            "frameId": self.frame_id,
            "chunkIndex": self.chunk_index,
            "chunkCount": self.chunk_count,
            "totalBoneCount": self.total_bone_count,
            "characterId": self.character_id,
        }
        if self.identity is not None:
            payload.update(self.identity.to_payload())
        if self.animation is not None:
            payload["anim"] = self.animation.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class SkeletonPacket:
    model_name: str
    timestamp_ms: int
    schema: str
    bones: tuple[SkeletonBone, ...]
    trailer: SkeletonTrailer | None = None

    @property
    def stable_key(self) -> str:
        if self.trailer is not None and self.trailer.identity is not None:
            stable_key = self.trailer.identity.stable_key
            if stable_key:
                return stable_key
        return self.model_name

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "skeleton_binary",
            "modelName": self.model_name,
            "stableKey": self.stable_key,
            "timestampMs": self.timestamp_ms,
            "schema": self.schema,
            "boneCount": len(self.bones),
            "bones": [bone.to_payload() for bone in self.bones],
            "trailer": None if self.trailer is None else self.trailer.to_payload(),
        }


__all__ = [
    "SkeletonAnimation",
    "SkeletonBone",
    "SkeletonIdentity",
    "SkeletonPacket",
    "SkeletonTrailer",
]
