from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_ROOT = REPO_ROOT / "docs" / "scenarios"


def test_male_referenced_l0_geom_preserves_reference_direction() -> None:
    text = (SCENARIOS_ROOT / "vam-male-referenced-motion.md").read_text(encoding="utf-8")

    assert "l0_geom = _clamp01(axis_meters / ref_length)" in text
    assert "l0_geom = 1.0 - _clamp01(axis_meters / ref_length)" not in text
    assert "L0_geom = clamp01(axisMeters / referenceFrame.length)" in text
    assert "near `ReferenceStart` is close\nto `0`" in text


def test_contact_l0_distance_is_documented_as_raw_meters_not_reference_normalized() -> None:
    text = (SCENARIOS_ROOT / "vam-female-female-motion.md").read_text(encoding="utf-8")

    assert "`L0_distance_m` is a surface-contact distance, not insertion depth." in text
    assert "not reference-length normalized" in text
    assert 'units": {\n        "L0": "m"' in text
