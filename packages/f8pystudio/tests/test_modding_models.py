from __future__ import annotations

from pathlib import Path

import pytest
from f8pysdk.codec import dump_json, validate_as

from f8pystudio.modding.models import (
    MODDING_RECIPE_SCHEMA_VERSION,
    F8ModdingRecipeRecord,
    ModdingBackendKind,
    ModdingEngineKind,
    modding_record_content,
)
from f8pystudio.modding.recipe_repository import ModdingRecipeDraftService, validate_recipe_content_for_publish
from f8pystudio.modding.redaction import sanitized_recipe_content


def test_modding_recipe_record_encode_decode_and_redacts_paths(tmp_path: Path) -> None:
    content = modding_record_content(
        engine=ModdingEngineKind.unity,
        backend=ModdingBackendKind.mono,
        game_profile={"profileId": "known-game", "processAliases": ["Game.exe"], "installPath": "H:\\Games\\Game"},
        installer={"selectedExporter": "skeleton", "configPath": "C:\\Games\\Game\\BepInEx\\config\\f8.cfg"},
        payloads={"profile": {"path": "H:\\Games\\Game\\BepInEx\\plugins\\F8SkeletonStreamer\\profile.json"}},
        py_studio={"linkedComponentIds": ["component-a"]},
        verification={"udpPort": 39540, "sampleKeys": ["Model_A"]},
        notes="manual note",
    )

    sanitized = sanitized_recipe_content(content)
    assert sanitized["schemaVersion"] == MODDING_RECIPE_SCHEMA_VERSION
    assert sanitized["gameProfile"]["installPath"] == "[local-path-redacted]"
    assert sanitized["installer"]["configPath"] == "[local-path-redacted]"

    record = F8ModdingRecipeRecord(recipeId="recipe-a", name="Recipe", content=sanitized)
    payload = dump_json(record, mode="json")
    decoded = validate_as(F8ModdingRecipeRecord, payload)

    assert decoded.recipeId == "recipe-a"
    assert decoded.content["engine"] == "unity"
    validate_recipe_content_for_publish(decoded.content)


def test_recipe_validation_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="absolute local path"):
        validate_recipe_content_for_publish(
            {
                "schemaVersion": MODDING_RECIPE_SCHEMA_VERSION,
                "engine": "unity",
                "backend": "mono",
                "bad": "H:\\Games\\Game",
            }
        )


def test_recipe_draft_service_round_trips_local_only_target_path(tmp_path: Path) -> None:
    service = ModdingRecipeDraftService(db_path=tmp_path / "assets.db")
    record = F8ModdingRecipeRecord(
        recipeId="recipe-a",
        name="Known Unity Game",
        tags=["unity"],
        content=modding_record_content(
            engine=ModdingEngineKind.unity,
            backend=ModdingBackendKind.il2cpp,
            game_profile={"profileId": "known"},
            installer={"selectedExporter": "skeleton"},
            payloads={},
            py_studio={},
            verification={"udpPort": 39540},
        ),
        lastTargetPath="H:\\Games\\Known\\Known.exe",
    )

    draft = service.create_draft_from_record(record, draft_id="recipe-a")
    loaded = service.draft("recipe-a")

    assert draft.record.recipeId == "recipe-a"
    assert loaded is not None
    assert loaded.record.lastTargetPath == "H:\\Games\\Known\\Known.exe"
    exported = tmp_path / "recipe.json"
    service.export_draft("recipe-a", str(exported))
    text = exported.read_text(encoding="utf-8")
    assert "H:\\\\Games" not in text
    assert '"assetType": "modding_recipe"' in text
