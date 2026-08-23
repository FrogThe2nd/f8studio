from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from f8pyfallendoll.stream import (
    parse_latest_frame,
    read_appended,
    resolve_runtime_dir,
    select_frame,
)


def _bone(name: str, x: float = 0.0) -> dict[str, Any]:
    return {"name": name, "pos": [x, 0.0, 0.0], "rot": [1.0, 0.0, 0.0, 0.0]}


def _payload(
    *,
    timestamp_ms: int,
    role: str,
    role_index: int,
    priority: int,
    preferred_bones: list[str],
    bones: list[dict[str, Any]],
) -> dict[str, Any]:
    stable_key = f"fallen-doll:{role}:{role_index}"
    return {
        "type": "skeleton_binary",
        "schema": "fallen-doll-ue-world-v1",
        "modelName": stable_key,
        "stableKey": stable_key,
        "timestampMs": timestamp_ms,
        "bones": bones,
        "trailer": {
            "profileId": "fallen-doll",
            "hanimeActive": True,
            "hanimeId": "Hand02",
            "hanimeAsset": "/Game/HAnime/Hand02",
            "hanimeCategory": "hand",
            "role": role,
            "roleIndex": role_index,
            "participantPriority": priority,
            "preferredBones": preferred_bones,
        },
    }


def test_resolve_runtime_dir_uses_specific_override_first(tmp_path: Path) -> None:
    exact = tmp_path / "exact"
    games = tmp_path / "games"
    resolved = resolve_runtime_dir(
        {"FD_TCODE_RUNTIME_DIR": str(exact), "F8STUDIO_GAMES_DIR": str(games)},
        home=tmp_path / "home",
    )
    assert resolved == exact.resolve()


def test_parse_latest_frame_filters_profile_gate_and_old_frames() -> None:
    old = _payload(
        timestamp_ms=100,
        role="male",
        role_index=0,
        priority=0,
        preferred_bones=["Penis02"],
        bones=[_bone("Penis02")],
    )
    male = {**old, "timestampMs": 120}
    female = _payload(
        timestamp_ms=120,
        role="female",
        role_index=0,
        priority=0,
        preferred_bones=["R_Hand"],
        bones=[_bone("R_Hand")],
    )
    inactive = _payload(
        timestamp_ms=120,
        role="female",
        role_index=1,
        priority=1,
        preferred_bones=["L_Hand"],
        bones=[_bone("L_Hand")],
    )
    inactive["trailer"]["hanimeActive"] = False

    parsed = parse_latest_frame(
        [json.dumps(old), json.dumps(male), json.dumps(female), json.dumps(inactive), "not-json"],
        arrival_timestamp_ms=999,
    )

    assert [item["stableKey"] for item in parsed.skeletons] == ["fallen-doll:female:0", "fallen-doll:male:0"]
    assert all(item["timestampMs"] == 999 for item in parsed.skeletons)
    assert all(item["sourceTimestampMs"] == 120 for item in parsed.skeletons)
    assert parsed.dropped_payloads == 1
    assert parsed.rejected_lines == 2


def test_select_frame_uses_participant_priority_and_preferred_bone_order() -> None:
    male_low_priority = _payload(
        timestamp_ms=120,
        role="male",
        role_index=0,
        priority=5,
        preferred_bones=["Penis01"],
        bones=[_bone("Penis01")],
    )
    male_primary = _payload(
        timestamp_ms=120,
        role="male",
        role_index=1,
        priority=0,
        preferred_bones=["Penis02", "Penis01"],
        bones=[_bone("Penis01"), _bone("Penis02")],
    )
    female = _payload(
        timestamp_ms=120,
        role="female",
        role_index=0,
        priority=0,
        preferred_bones=["R_Hand", "L_Hand"],
        bones=[_bone("L_Hand"), _bone("R_Hand")],
    )

    selected = select_frame(
        [male_low_priority, male_primary, female],
        reference_role="male",
        target_role="female",
        enabled_reference_participants=["fallen-doll:male:0", "fallen-doll:male:1"],
        enabled_target_participants=["fallen-doll:female:0"],
        enabled_reference_bones=["Penis01", "Penis02"],
        enabled_target_bones=["L_Hand", "R_Hand"],
    )

    assert selected.status["valid"] is True
    assert selected.status["referenceKey"] == "fallen-doll:male:1"
    assert selected.reference_bone is not None and selected.reference_bone["name"] == "Penis02"
    assert selected.target_bone is not None and selected.target_bone["name"] == "R_Hand"


def test_read_appended_detects_truncation(tmp_path: Path) -> None:
    spool = tmp_path / "fd-skeleton.ndjson"
    spool.write_bytes(b"first\nsecond\n")
    initial = read_appended(spool, 0)
    assert initial.text == "first\nsecond\n"
    assert initial.truncated is False

    spool.write_bytes(b"new\n")
    truncated = read_appended(spool, initial.offset)
    assert truncated.text == "new\n"
    assert truncated.truncated is True
