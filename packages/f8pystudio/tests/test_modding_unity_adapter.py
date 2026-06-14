from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from f8pystudio.modding.models import ModdingBackendKind, ModdingEngineKind, ModdingInstallOption, ModdingTarget
from f8pystudio.modding.unity_adapter import UnityModdingAdapter


def test_unity_adapter_maps_detect_diagnose_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeCommon:
        def load_setup_config(self, path: Path | None = None) -> dict[str, str]:
            calls.append(("load_setup_config", {"path": path}))
            return {"config": "ok"}

    class FakeSetup:
        def run_detect(self, target: str, config: Any) -> dict[str, Any]:
            calls.append(("detect", {"target": target, "config": config}))
            return _detect_payload(tmp_path)

        def run_diagnose(self, target: str, config: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("diagnose", {"target": target, **kwargs}))
            return {
                "detection": _detect_payload(tmp_path),
                "selected_exporter": {"key": "default", "project_name": "F8SkeletonStreamer"},
                "installer_options": {"prefer_local_configs": True},
                "plan": {
                    "actions": ["install_bepinex", "install_exporter", "install_profile"],
                    "blocking_errors": [],
                },
                "offline": False,
            }

        def run_install(self, target: str, config: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(("install", {"target": target, **kwargs}))
            return {
                **_detect_payload(tmp_path),
                "selected_exporter": {"key": "default", "project_name": "F8SkeletonStreamer"},
                "actions": [
                    {"install_bepinex": "installed"},
                    {
                        "install_exporter": {
                            "plugin_dir": str(tmp_path / "Game" / "BepInEx" / "plugins" / "F8SkeletonStreamer"),
                            "release_tag": "v1",
                            "asset_name": "F8SkeletonStreamer.zip",
                            "config_path": str(tmp_path / "Game" / "BepInEx" / "config" / "f8.cfg"),
                        }
                    },
                    {"install_profile": {"path": str(tmp_path / "Game" / "profile.json"), "status": "installed"}},
                ],
                "exporter_config": {"path": str(tmp_path / "Game" / "BepInEx" / "config" / "f8.cfg")},
                "profile": {"path": str(tmp_path / "Game" / "profile.json"), "source": "local"},
            }

    monkeypatch.setattr(
        "f8pystudio.modding.unity_adapter._load_unitymods_modules",
        lambda root: (FakeSetup(), FakeCommon()),
    )
    adapter = UnityModdingAdapter(unitymods_root=tmp_path)

    report = adapter.detect(ModdingTarget(selectedPath=str(tmp_path / "Game" / "Game.exe")))
    plan = adapter.plan(
        report,
        ModdingInstallOption(
            exporter="skeleton",
            installCinematicUnityExplorer=True,
            installConfigurationManager=True,
            udpPort=39540,
        ),
    )
    result = adapter.install(plan, confirm=True)

    assert report.engine is ModdingEngineKind.unity
    assert report.backend is ModdingBackendKind.mono
    assert report.target.processName == "Game.exe"
    assert plan.actions[0].action == "install_bepinex"
    assert plan.graphBuildPlan["nodes"][0]["stateValues"]["port"] == 39540
    assert result.configPaths == [str(tmp_path / "Game" / "BepInEx" / "config" / "f8.cfg")]
    assert result.profilePaths == [str(tmp_path / "Game" / "profile.json")]
    diagnose_call = next(call for call in calls if call[0] == "diagnose")
    assert diagnose_call[1]["exporter"] == "skeleton"
    assert diagnose_call[1]["cue"] is True
    assert diagnose_call[1]["config_manager"] is True


def test_unity_adapter_install_requires_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeCommon:
        def load_setup_config(self, path: Path | None = None) -> dict[str, str]:
            return {}

    class FakeSetup:
        def run_detect(self, target: str, config: Any) -> dict[str, Any]:
            return _detect_payload(tmp_path)

        def run_diagnose(self, target: str, config: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "detection": _detect_payload(tmp_path),
                "selected_exporter": {"key": "default"},
                "plan": {"actions": ["install_bepinex"], "blocking_errors": []},
            }

    monkeypatch.setattr(
        "f8pystudio.modding.unity_adapter._load_unitymods_modules",
        lambda root: (FakeSetup(), FakeCommon()),
    )
    adapter = UnityModdingAdapter(unitymods_root=tmp_path)
    report = adapter.detect(ModdingTarget(selectedPath=str(tmp_path / "Game" / "Game.exe")))
    plan = adapter.plan(report, ModdingInstallOption())

    with pytest.raises(ValueError, match="confirm=true"):
        adapter.install(plan, confirm=False)


def _detect_payload(tmp_path: Path) -> dict[str, Any]:
    game_root = tmp_path / "Game"
    return {
        "target_input": str(game_root / "Game.exe"),
        "game_root": str(game_root),
        "exe_path": str(game_root / "Game.exe"),
        "process_name": "Game.exe",
        "game_type": "known_game",
        "profile_id": "known_game",
        "exporter_key": "default",
        "unity_version": "2021.3.1f1",
        "backend": "mono",
        "arch": "x64",
        "has_bepinex": False,
        "bepinex_variant": "",
        "bepinex_version": "",
        "bepinex_major": None,
    }
