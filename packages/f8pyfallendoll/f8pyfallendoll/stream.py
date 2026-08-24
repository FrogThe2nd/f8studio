from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .constants import DEFAULT_MAX_READ_BYTES

_GAME_ID = "fallen-doll"
_SPOOL_NAME = "fd-skeleton.ndjson"
_TARGET_BASIS_BY_BONE = {
    "R_Hand": {"up": "+local_z", "right": "-local_y"},
}


@dataclass(frozen=True)
class TailRead:
    offset: int
    text: str
    truncated: bool
    skipped_bytes: int


@dataclass(frozen=True)
class ParsedFrame:
    skeletons: list[dict[str, Any]]
    source_timestamp_ms: int | None
    rejected_lines: int
    dropped_payloads: int


@dataclass(frozen=True)
class SelectedFrame:
    skeletons: list[dict[str, Any]]
    reference_skeleton: dict[str, Any] | None
    target_skeleton: dict[str, Any] | None
    reference_bone: dict[str, Any] | None
    target_bone: dict[str, Any] | None
    status: dict[str, Any]


def resolve_runtime_dir(environment: Mapping[str, str] | None = None, *, home: Path | None = None) -> Path:
    env = os.environ if environment is None else environment
    exact_dir = str(env.get("FD_TCODE_RUNTIME_DIR") or "").strip()
    if exact_dir:
        return Path(exact_dir).expanduser().resolve()

    games_dir = str(env.get("F8STUDIO_GAMES_DIR") or "").strip()
    if games_dir:
        return (Path(games_dir).expanduser() / _GAME_ID / "runtime").resolve()

    home_dir = Path.home() if home is None else home
    return (home_dir / ".f8" / "studio" / "games" / _GAME_ID / "runtime").resolve()


def resolve_spool_path(runtime_dir: str, environment: Mapping[str, str] | None = None) -> Path:
    configured = str(runtime_dir or "").strip()
    root = Path(configured).expanduser().resolve() if configured else resolve_runtime_dir(environment)
    return root / _SPOOL_NAME


def read_appended(path: Path, offset: int, *, max_read_bytes: int = DEFAULT_MAX_READ_BYTES) -> TailRead:
    size = path.stat().st_size
    truncated = size < offset
    start = 0 if truncated else max(0, int(offset))
    available = max(0, size - start)
    skipped = 0
    bounded_max = max(1, int(max_read_bytes))
    if available > bounded_max:
        skipped = available - bounded_max
        start += skipped
        available = bounded_max
    if available <= 0:
        return TailRead(offset=size, text="", truncated=truncated, skipped_bytes=skipped)
    with path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read(available)
    return TailRead(
        offset=start + len(raw),
        text=raw.decode("utf-8", errors="replace"),
        truncated=truncated,
        skipped_bytes=skipped,
    )


def parse_latest_frame(lines: list[str], *, arrival_timestamp_ms: int) -> ParsedFrame:
    valid_payloads: list[dict[str, Any]] = []
    rejected = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            rejected += 1
            continue
        payload = _validated_payload(decoded)
        if payload is None:
            rejected += 1
            continue
        valid_payloads.append(payload)

    timestamps = [
        timestamp for payload in valid_payloads if (timestamp := _integer(payload.get("timestampMs"))) is not None
    ]
    newest_timestamp = max(timestamps) if timestamps else None
    latest_by_key: dict[str, dict[str, Any]] = {}
    dropped = 0
    for payload in valid_payloads:
        source_timestamp = _integer(payload.get("timestampMs"))
        if newest_timestamp is not None and source_timestamp != newest_timestamp:
            dropped += 1
            continue
        key = stable_key(payload)
        if not key:
            rejected += 1
            continue
        normalized = dict(payload)
        normalized["sourceTimestampMs"] = source_timestamp
        normalized["timestampMs"] = int(arrival_timestamp_ms)
        normalized["receivedAtMs"] = int(arrival_timestamp_ms)
        trailer = _trailer(normalized)
        normalized["trailer"] = {**trailer, "timeSource": "fallendoll-source-arrival-wall-clock-latest-frame"}
        latest_by_key[key] = normalized

    return ParsedFrame(
        skeletons=[latest_by_key[key] for key in sorted(latest_by_key)],
        source_timestamp_ms=newest_timestamp,
        rejected_lines=rejected,
        dropped_payloads=dropped,
    )


def select_frame(
    skeletons: list[dict[str, Any]],
    *,
    reference_role: str,
    target_role: str,
    enabled_reference_participants: list[str],
    enabled_target_participants: list[str],
    enabled_reference_bones: list[str],
    enabled_target_bones: list[str],
) -> SelectedFrame:
    reference = _select_participant(skeletons, role=reference_role, enabled_keys=enabled_reference_participants)
    target = _select_participant(skeletons, role=target_role, enabled_keys=enabled_target_participants)
    reference_bone = _select_bone(reference, enabled_reference_bones)
    target_bone = _with_target_basis(_select_bone(target, enabled_target_bones))
    valid = reference_bone is not None and target_bone is not None
    reason = "ok"
    if reference is None:
        reason = "no_reference_participant"
    elif target is None:
        reason = "no_target_participant"
    elif reference_bone is None:
        reason = "no_reference_bone"
    elif target_bone is None:
        reason = "no_target_bone"
    identity = _trailer(reference if reference is not None else target)
    status = {
        "valid": valid,
        "reason": reason,
        "referenceKey": stable_key(reference),
        "targetKey": stable_key(target),
        "referenceBone": "" if reference_bone is None else str(reference_bone["name"]),
        "targetBone": "" if target_bone is None else str(target_bone["name"]),
        "hanimeId": str(identity.get("hanimeId") or ""),
        "hanimeAsset": str(identity.get("hanimeAsset") or ""),
        "hanimeCategory": str(identity.get("hanimeCategory") or ""),
    }
    return SelectedFrame(
        skeletons=list(skeletons),
        reference_skeleton=reference,
        target_skeleton=target,
        reference_bone=reference_bone,
        target_bone=target_bone,
        status=status,
    )


def available_participants(skeletons: list[dict[str, Any]]) -> list[str]:
    return sorted(key for skeleton in skeletons if (key := stable_key(skeleton)))


def available_bones(skeleton: dict[str, Any] | None) -> list[str]:
    if skeleton is None:
        return []
    bones = skeleton.get("bones")
    if not isinstance(bones, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for bone in bones:
        parsed = _validated_bone(bone)
        if parsed is None:
            continue
        name = str(parsed["name"])
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def stable_key(skeleton: dict[str, Any] | None) -> str:
    if skeleton is None:
        return ""
    explicit = str(skeleton.get("stableKey") or "").strip()
    if explicit:
        return explicit
    trailer = _trailer(skeleton)
    profile_id = str(trailer.get("profileId") or "").strip()
    role = str(trailer.get("role") or "").strip().lower()
    role_index = _integer(trailer.get("roleIndex"))
    if profile_id and role and role_index is not None and role_index >= 0:
        return f"{profile_id}:{role}:{role_index}"
    return str(skeleton.get("modelName") or "").strip()


def _validated_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if str(value.get("type") or "") != "skeleton_binary":
        return None
    bones = value.get("bones")
    if not isinstance(bones, list):
        return None
    trailer = _trailer(value)
    if str(trailer.get("profileId") or "").strip() != "fallen-doll":
        return None
    if trailer.get("hanimeActive") is not True:
        return None
    if not stable_key(value):
        return None
    return {str(key): field_value for key, field_value in value.items()}


def _select_participant(
    skeletons: list[dict[str, Any]],
    *,
    role: str,
    enabled_keys: list[str],
) -> dict[str, Any] | None:
    normalized_role = str(role or "").strip().lower()
    enabled = set(enabled_keys)
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for skeleton in skeletons:
        trailer = _trailer(skeleton)
        if str(trailer.get("role") or "").strip().lower() != normalized_role:
            continue
        key = stable_key(skeleton)
        if key not in enabled:
            continue
        ranked.append(
            (
                _integer_or_default(trailer.get("participantPriority"), 1024),
                _integer_or_default(trailer.get("roleIndex"), 1024),
                key,
                skeleton,
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return ranked[0][3]


def _select_bone(skeleton: dict[str, Any] | None, enabled_bones: list[str]) -> dict[str, Any] | None:
    if skeleton is None:
        return None
    bones = skeleton.get("bones")
    if not isinstance(bones, list):
        return None
    by_name: dict[str, dict[str, Any]] = {}
    for raw_bone in bones:
        bone = _validated_bone(raw_bone)
        if bone is not None:
            by_name[str(bone["name"])] = bone
    enabled = set(enabled_bones)
    preferred = _string_list(_trailer(skeleton).get("preferredBones"))
    for name in preferred:
        if name in enabled and name in by_name:
            return by_name[name]
    return None


def _validated_bone(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    pos = value.get("pos")
    rot = value.get("rot")
    if not name or not isinstance(pos, list) or len(pos) != 3 or not isinstance(rot, list) or len(rot) != 4:
        return None
    numbers = [_finite_float(item) for item in [*pos, *rot]]
    if any(item is None for item in numbers):
        return None
    finite = [float(item) for item in numbers if item is not None]
    return {"name": name, "pos": finite[:3], "rot": finite[3:]}


def _with_target_basis(bone: dict[str, Any] | None) -> dict[str, Any] | None:
    if bone is None:
        return None
    basis = _TARGET_BASIS_BY_BONE.get(str(bone.get("name") or ""))
    return dict(bone) if basis is None else {**bone, "basis": dict(basis)}


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _trailer(skeleton: dict[str, Any] | None) -> dict[str, Any]:
    if skeleton is None:
        return {}
    value = skeleton.get("trailer")
    return value if isinstance(value, dict) else {}


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _integer_or_default(value: Any, default: int) -> int:
    result = _integer(value)
    return default if result is None else result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
