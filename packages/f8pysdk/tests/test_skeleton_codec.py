from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from f8pysdk.motion import SkeletonPacketDecodeError, decode_skeleton_datagram, decode_skeleton_packet

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "unity_skeleton_v2.bin"


def test_decodes_csharp_v2_golden_fixture() -> None:
    packet = decode_skeleton_packet(FIXTURE_PATH.read_bytes())

    assert packet.model_name == "1234|Alice"
    assert packet.timestamp_ms == 1_720_000_000_123
    assert packet.schema == "unity.keypoints.realtime.v1"
    assert [bone.name for bone in packet.bones] == ["FemaleRoot", "Vagina"]
    assert packet.bones[0].position == pytest.approx((1.25, -2.5, 3.75))
    assert packet.trailer is not None
    assert packet.trailer.extension_version == 2
    assert packet.trailer.frame_id == 42
    assert packet.trailer.identity is not None
    assert packet.trailer.identity.profile_id == "hs2"
    assert packet.trailer.identity.role == "Female"
    assert packet.trailer.identity.role_index == 0
    assert packet.trailer.identity.exporter_version == "0.2.0"
    assert packet.stable_key == "hs2:female:0"
    assert packet.trailer.animation is not None
    assert packet.trailer.animation.pose_key == "fixture_pose"


def test_decodes_legacy_v1_packet() -> None:
    packet = decode_skeleton_packet(_legacy_v1_packet())

    assert packet.model_name == "99|Legacy"
    assert packet.stable_key == "99|Legacy"
    assert packet.trailer is not None
    assert packet.trailer.extension_version == 1
    assert packet.trailer.identity is None


def test_json_compatibility_is_explicit() -> None:
    raw = json.dumps({"type": "skeleton_binary", "modelName": "json-model", "bones": []}).encode()

    assert decode_skeleton_datagram(raw)["modelName"] == "json-model"


def test_rejects_truncated_binary_with_actionable_error() -> None:
    with pytest.raises(SkeletonPacketDecodeError, match="Truncated|terminator"):
        decode_skeleton_packet(FIXTURE_PATH.read_bytes()[:24])


def _aligned(value: str, offset: int) -> bytes:
    raw = value.encode("utf-8") + b"\x00"
    return raw + (b"\x00" * ((4 - ((offset + len(raw)) & 0x03)) & 0x03))


def _legacy_v1_packet() -> bytes:
    data = bytearray()
    data.extend(_aligned("99|Legacy", len(data)))
    data.extend(struct.pack("<Q", 123))
    data.extend(_aligned("unity.keypoints.realtime.v1", len(data)))
    data.extend(struct.pack("<i", 1))
    data.extend(_aligned("Root", len(data)))
    data.extend(struct.pack("<fffffff", 0.0, 1.0, 2.0, 1.0, 0.0, 0.0, 0.0))
    data.extend(b"LMEX")
    data.extend(struct.pack("<HQiiii", 1, 7, 0, 1, 1, 99))
    return bytes(data)
