from __future__ import annotations

from typing import Any

import pytest

from f8pystudio.agents.tools.studio import StudioAutomationTools


def test_studio_automation_tools_forward_modding_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeClient:
        def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append((method, params))
            return {"ok": True}

    monkeypatch.setattr(
        "f8pystudio.agents.tools.studio.AutomationClient.from_connection_file",
        lambda path: FakeClient(),
    )

    tools = StudioAutomationTools(connection_file="conn.json")
    plan: dict[str, Any] = {"report": {}, "options": {}, "actions": []}

    assert tools.modding_detect_target("H:\\Games\\Game.exe") == {"ok": True}
    assert tools.modding_preview_install("H:\\Games\\Game.exe", options={"exporter": "skeleton"}) == {"ok": True}
    assert tools.modding_apply_install(plan, confirm=True) == {"ok": True}
    assert tools.modding_verify_stream(port=39540, timeout_s=0.1) == {"ok": True}
    assert tools.modding_create_recipe("Recipe", detection={"report": {}}, confirm=True) == {"ok": True}
    assert tools.modding_recipe_list() == {"ok": True}
    assert tools.modding_recipe_load("recipe-a") == {"ok": True}
    assert tools.modding_recipe_export("recipe-a", "recipe.json", confirm=True) == {"ok": True}

    assert calls == [
        ("modding.detectTarget", {"targetPath": "H:\\Games\\Game.exe"}),
        ("modding.previewInstall", {"targetPath": "H:\\Games\\Game.exe", "options": {"exporter": "skeleton"}}),
        ("modding.applyInstall", {"plan": plan, "confirm": True}),
        ("modding.verifyStream", {"port": 39540, "host": "127.0.0.1", "timeoutS": 0.1, "maxSamples": 8}),
        (
            "modding.createRecipe",
            {
                "name": "Recipe",
                "description": "",
                "tags": None,
                "detection": {"report": {}},
                "install": None,
                "verification": None,
                "graph": None,
                "notes": "",
                "confirm": True,
            },
        ),
        ("modding.recipeList", None),
        ("modding.recipeLoad", {"recipeId": "recipe-a"}),
        ("modding.recipeExport", {"recipeId": "recipe-a", "path": "recipe.json", "confirm": True}),
    ]


def test_studio_automation_tools_modding_actions_require_confirm() -> None:
    tools = StudioAutomationTools()

    with pytest.raises(ValueError, match="confirm=true"):
        tools.modding_apply_install({}, confirm=False)
    with pytest.raises(ValueError, match="confirm=true"):
        tools.modding_create_recipe("Recipe", confirm=False)
    with pytest.raises(ValueError, match="confirm=true"):
        tools.modding_recipe_export("recipe-a", "recipe.json", confirm=False)
