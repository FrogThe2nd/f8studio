from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Protocol, cast

from .graph_templates import skeleton_stream_graph_build_plan
from .models import (
    DEFAULT_SKELETON_UDP_PORT,
    ModdingBackendKind,
    ModdingDetectionReport,
    ModdingEngineKind,
    ModdingInstallAction,
    ModdingInstallOption,
    ModdingInstallResult,
    ModdingPlan,
    ModdingTarget,
    json_safe_value,
    normalize_backend_kind,
    normalize_exporter_key,
    typed_dict,
)

F8UNITYMODS_ROOT_ENV = "F8UNITYMODS_ROOT"
DEFAULT_F8UNITYMODS_ROOT = Path("H:/Feel8/f8unitymods")


class _UnityGameSetupModule(Protocol):
    def run_detect(self, target: str, config: Any) -> dict[str, Any]: ...

    def run_diagnose(
        self,
        target: str,
        config: Any,
        *,
        exporter: str,
        prefer_local_configs: bool | None,
        allow_remote_configs: bool | None,
        refresh_remote_cache: bool,
        release_tag: str,
        force_reinstall: bool,
        skip_exporter: bool,
        rue: bool,
        cue: bool,
        config_manager: bool,
        uud: bool,
        offline: bool,
    ) -> dict[str, Any]: ...

    def run_install(
        self,
        target: str,
        config: Any,
        *,
        exporter: str,
        prefer_local_configs: bool | None,
        allow_remote_configs: bool | None,
        refresh_remote_cache: bool,
        release_tag: str,
        force_reinstall: bool,
        rue: bool,
        cue: bool,
        config_manager: bool,
        uud: bool,
        skip_exporter: bool,
        offline: bool,
        interaction_meta: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class _UnityCommonModule(Protocol):
    def load_setup_config(self, path: Path | None = None) -> Any: ...


class UnityModdingAdapter:
    def __init__(self, *, unitymods_root: Path | None = None) -> None:
        self._unitymods_root = _resolve_unitymods_root(unitymods_root)

    @property
    def unitymods_root(self) -> Path:
        return self._unitymods_root

    def detect(self, target: ModdingTarget) -> ModdingDetectionReport:
        setup, common = _load_unitymods_modules(self._unitymods_root)
        config = common.load_setup_config()
        raw = setup.run_detect(target.selectedPath, config)
        return _detection_report_from_raw(target=target, raw=raw)

    def plan(self, report: ModdingDetectionReport, options: ModdingInstallOption) -> ModdingPlan:
        setup, common = _load_unitymods_modules(self._unitymods_root)
        config = common.load_setup_config()
        target_path = report.target.executablePath or report.target.resolvedGameRoot or report.target.selectedPath
        raw = setup.run_diagnose(
            target=target_path,
            config=config,
            exporter=_unity_exporter_flag(options.exporter),
            prefer_local_configs=options.preferLocalConfigs,
            allow_remote_configs=options.allowRemoteConfigs,
            refresh_remote_cache=bool(options.refreshRemoteCache),
            release_tag=str(options.releaseTag or ""),
            force_reinstall=bool(options.forceReinstall),
            skip_exporter=bool(options.skipExporter),
            rue=bool(options.installRuntimeUnityEditor),
            cue=bool(options.installCinematicUnityExplorer),
            config_manager=bool(options.installConfigurationManager),
            uud=bool(options.installUniversalUnityDemosaics),
            offline=bool(options.offline),
        )
        fresh_report = _detection_report_from_raw(target=report.target, raw=typed_dict(raw.get("detection")))
        selected_exporter = typed_dict(raw.get("selected_exporter"))
        exporter_key = str(selected_exporter.get("key") or fresh_report.selectedExporter or "auto")
        graph_plan = skeleton_stream_graph_build_plan(port=int(options.udpPort or DEFAULT_SKELETON_UDP_PORT))
        return ModdingPlan(
            report=ModdingDetectionReport(
                target=fresh_report.target,
                engine=fresh_report.engine,
                backend=fresh_report.backend,
                architecture=fresh_report.architecture,
                engineVersion=fresh_report.engineVersion,
                existingLoaderState=fresh_report.existingLoaderState,
                matchedProfileId=fresh_report.matchedProfileId,
                matchedProfileName=fresh_report.matchedProfileName,
                selectedExporter=exporter_key,
                warnings=fresh_report.warnings,
                raw=fresh_report.raw,
            ),
            options=options,
            actions=_actions_from_diagnose(raw),
            blockingErrors=_blocking_errors_from_diagnose(raw),
            filesToCreateOrUpdate=_files_to_create_or_update(raw),
            filesToPreserveOrBackup=_files_to_preserve_or_backup(raw),
            previewPayload=typed_dict(json_safe_value(raw)),
            graphBuildPlan=graph_plan,
        )

    def install(self, plan: ModdingPlan, *, confirm: bool) -> ModdingInstallResult:
        if not confirm:
            raise ValueError("modding.applyInstall requires confirm=true")
        if plan.blockingErrors:
            raise ValueError("modding.applyInstall cannot run while blockingErrors are present")
        setup, common = _load_unitymods_modules(self._unitymods_root)
        config = common.load_setup_config()
        target_path = plan.report.target.executablePath or plan.report.target.resolvedGameRoot or plan.report.target.selectedPath
        raw = setup.run_install(
            target=target_path,
            config=config,
            exporter=_unity_exporter_flag(plan.options.exporter),
            prefer_local_configs=plan.options.preferLocalConfigs,
            allow_remote_configs=plan.options.allowRemoteConfigs,
            refresh_remote_cache=bool(plan.options.refreshRemoteCache),
            release_tag=str(plan.options.releaseTag or ""),
            force_reinstall=bool(plan.options.forceReinstall),
            rue=bool(plan.options.installRuntimeUnityEditor),
            cue=bool(plan.options.installCinematicUnityExplorer),
            config_manager=bool(plan.options.installConfigurationManager),
            uud=bool(plan.options.installUniversalUnityDemosaics),
            skip_exporter=bool(plan.options.skipExporter),
            offline=bool(plan.options.offline),
            interaction_meta={"interaction_used": False, "target_prompted": False, "exporter_prompted": False},
        )
        return ModdingInstallResult(
            plan=plan,
            executedActions=_executed_actions_from_install(raw),
            backupPaths=_backup_paths_from_install(raw),
            installedPluginVersions=_installed_versions_from_install(raw),
            configPaths=_config_paths_from_install(raw),
            profilePaths=_profile_paths_from_install(raw),
            raw=typed_dict(json_safe_value(raw)),
        )


def _detection_report_from_raw(*, target: ModdingTarget, raw: dict[str, Any]) -> ModdingDetectionReport:
    backend = normalize_backend_kind(raw.get("backend"))
    warnings: list[str] = []
    if backend is ModdingBackendKind.unknown:
        warnings.append("Unity backend is unknown; expected mono or il2cpp.")
    architecture = str(raw.get("arch") or "unknown").strip() or "unknown"
    if architecture == "unknown":
        warnings.append("Unity architecture is unknown; expected x86 or x64.")
    game_root = str(raw.get("game_root") or target.resolvedGameRoot or "")
    exe_path = str(raw.get("exe_path") or target.executablePath or "")
    process_name = str(raw.get("process_name") or target.processName or "")
    resolved_target = ModdingTarget(
        selectedPath=target.selectedPath,
        resolvedGameRoot=game_root,
        executablePath=exe_path,
        processName=process_name,
    )
    has_bepinex = bool(raw.get("has_bepinex", False))
    loader_state = {
        "hasBepInEx": has_bepinex,
        "bepInExVariant": str(raw.get("bepinex_variant") or ""),
        "bepInExVersion": str(raw.get("bepinex_version") or ""),
        "bepInExMajor": raw.get("bepinex_major"),
    }
    return ModdingDetectionReport(
        target=resolved_target,
        engine=ModdingEngineKind.unity,
        backend=backend,
        architecture=architecture,
        engineVersion=str(raw.get("unity_version") or ""),
        existingLoaderState=loader_state,
        matchedProfileId=str(raw.get("profile_id") or ""),
        matchedProfileName=str(raw.get("game_type") or ""),
        selectedExporter=normalize_exporter_key(raw.get("exporter_key")),
        warnings=warnings,
        raw=typed_dict(json_safe_value(raw)),
    )


def _actions_from_diagnose(raw: dict[str, Any]) -> list[ModdingInstallAction]:
    plan = typed_dict(raw.get("plan"))
    raw_actions = raw.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = plan.get("actions")
    actions: list[ModdingInstallAction] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            action_name = str(item or "").strip()
            if not action_name:
                continue
            actions.append(
                ModdingInstallAction(
                    action=action_name,
                    description=_action_description(action_name),
                    writes=_action_write_hints(action_name),
                    preserves=_action_preserve_hints(action_name),
                    backups=_action_backup_hints(action_name),
                    payload={"source": "f8unitymods.diagnose"},
                )
            )
    return actions


def _blocking_errors_from_diagnose(raw: dict[str, Any]) -> list[str]:
    plan = typed_dict(raw.get("plan"))
    blocking = plan.get("blocking_errors")
    if not isinstance(blocking, list):
        return []
    return [str(item) for item in blocking if str(item or "").strip()]


def _files_to_create_or_update(raw: dict[str, Any]) -> list[str]:
    actions = _actions_from_diagnose(raw)
    files: list[str] = []
    for action in actions:
        files.extend(action.writes)
    return _dedupe_text(files)


def _files_to_preserve_or_backup(raw: dict[str, Any]) -> list[str]:
    actions = _actions_from_diagnose(raw)
    files: list[str] = []
    for action in actions:
        files.extend(action.preserves)
        files.extend(action.backups)
    return _dedupe_text(files)


def _executed_actions_from_install(raw: dict[str, Any]) -> list[dict[str, Any]]:
    actions = raw.get("actions")
    if isinstance(actions, list):
        return [typed_dict(json_safe_value(item)) for item in actions]
    return []


def _backup_paths_from_install(raw: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    actions = raw.get("actions")
    if not isinstance(actions, list):
        return paths
    for action in actions:
        action_map = typed_dict(action)
        value = action_map.get("backup_existing_bepinex")
        if value is not None:
            paths.append(str(value))
    return _dedupe_text(paths)


def _installed_versions_from_install(raw: dict[str, Any]) -> dict[str, Any]:
    selected_exporter = typed_dict(raw.get("selected_exporter"))
    out: dict[str, Any] = {
        "backend": str(raw.get("backend") or ""),
        "bepInEx": str(raw.get("bepinex_version") or ""),
        "selectedExporter": selected_exporter,
    }
    actions = raw.get("actions")
    if isinstance(actions, list):
        utility_actions: dict[str, Any] = {}
        for action in actions:
            action_map = typed_dict(action)
            for key, value in action_map.items():
                if str(key).startswith("install_") and str(key) != "install_exporter":
                    utility_actions[str(key)] = json_safe_value(value)
        out["utilities"] = utility_actions
    return out


def _config_paths_from_install(raw: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    exporter_config = typed_dict(raw.get("exporter_config"))
    config_path = str(exporter_config.get("path") or "").strip()
    if config_path:
        paths.append(config_path)
    actions = raw.get("actions")
    if isinstance(actions, list):
        for action in actions:
            action_map = typed_dict(action)
            cue_config = typed_dict(action_map.get("install_cinematic_unity_explorer_config"))
            cue_path = str(cue_config.get("path") or "").strip()
            if cue_path:
                paths.append(cue_path)
    return _dedupe_text(paths)


def _profile_paths_from_install(raw: dict[str, Any]) -> list[str]:
    profile = typed_dict(raw.get("profile"))
    profile_path = str(profile.get("path") or "").strip()
    return [profile_path] if profile_path else []


def _action_description(action_name: str) -> str:
    if action_name == "install_bepinex":
        return "Install the BepInEx loader matching the Unity backend and architecture."
    if action_name == "backup_and_reinstall_bepinex":
        return "Back up the existing BepInEx install and replace it with a matching loader."
    if action_name == "keep_existing_bepinex":
        return "Keep the existing compatible BepInEx install."
    if action_name == "install_exporter":
        return "Install the selected Feel8 skeleton or Live2D streamer plugin."
    if action_name == "install_exporter_config":
        return "Install or update exporter configuration."
    if action_name == "install_profile":
        return "Install profile metadata for the detected game."
    if action_name == "install_runtime_unity_editor":
        return "Install RuntimeUnityEditor utility plugin."
    if action_name == "install_cinematic_unity_explorer":
        return "Install CinematicUnityExplorer utility plugin."
    if action_name == "install_configuration_manager":
        return "Install BepInEx ConfigurationManager utility plugin."
    if action_name == "install_universal_unity_demosaics":
        return "Install UniversalUnityDemosaics utility plugin."
    return action_name.replace("_", " ")


def _action_write_hints(action_name: str) -> list[str]:
    if action_name in {"install_bepinex", "backup_and_reinstall_bepinex"}:
        return ["<game>/BepInEx/**", "<game>/doorstop_config.ini", "<game>/winhttp.dll"]
    if action_name in {"install_exporter", "install_profile"}:
        return ["<game>/BepInEx/plugins/F8SkeletonStreamer/**", "<game>/BepInEx/plugins/F8Live2DStreamer/**"]
    if action_name == "install_exporter_config":
        return ["<game>/BepInEx/config/**"]
    if action_name == "install_runtime_unity_editor":
        return ["<game>/BepInEx/plugins/RuntimeUnityEditor/**"]
    if action_name == "install_cinematic_unity_explorer":
        return ["<game>/BepInEx/plugins/CinematicUnityExplorer/**", "<game>/BepInEx/config/**"]
    if action_name == "install_configuration_manager":
        return ["<game>/BepInEx/plugins/ConfigurationManager/**"]
    if action_name == "install_universal_unity_demosaics":
        return ["<game>/BepInEx/plugins/UniversalUnityDemosaics/**"]
    return []


def _action_preserve_hints(action_name: str) -> list[str]:
    if action_name in {"keep_existing_bepinex", "install_profile", "install_exporter_config"}:
        return ["existing unmanaged BepInEx configs/profiles are preserved unless marked managed by f8unitymods"]
    return []


def _action_backup_hints(action_name: str) -> list[str]:
    if action_name == "backup_and_reinstall_bepinex":
        return ["<game>/BepInEx.backup-*"]
    return []


def _dedupe_text(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _unity_exporter_flag(value: object) -> str:
    key = normalize_exporter_key(value)
    if key == "skeleton":
        return "skeleton"
    if key == "live2d":
        return "live2d"
    return "auto"


def _resolve_unitymods_root(explicit_root: Path | None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root)
    env_value = str(os.environ.get(F8UNITYMODS_ROOT_ENV) or "").strip()
    if env_value:
        return Path(env_value)
    return DEFAULT_F8UNITYMODS_ROOT


def _load_unitymods_modules(root: Path) -> tuple[_UnityGameSetupModule, _UnityCommonModule]:
    tools_dir = Path(root) / "tools"
    game_setup_path = tools_dir / "game_setup.py"
    common_path = tools_dir / "common.py"
    if not game_setup_path.is_file():
        raise FileNotFoundError(f"f8unitymods game_setup.py not found: {game_setup_path}")
    if not common_path.is_file():
        raise FileNotFoundError(f"f8unitymods common.py not found: {common_path}")
    previous_common = sys.modules.get("common")
    with _temporary_sys_path(tools_dir):
        common_module = _load_module_from_path("f8unitymods_tools_common", common_path)
        sys.modules["common"] = common_module
        try:
            setup_module = _load_module_from_path("f8unitymods_tools_game_setup", game_setup_path)
        finally:
            if previous_common is None:
                sys.modules.pop("common", None)
            else:
                sys.modules["common"] = previous_common
    return cast(_UnityGameSetupModule, setup_module), cast(_UnityCommonModule, common_module)


def _load_module_from_path(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        raise ImportError(f"Cannot create import spec for {path}")
    loader = spec.loader
    if loader is None:
        raise ImportError(f"Import spec has no loader for {path}")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@contextmanager
def _temporary_sys_path(path: Path) -> Iterator[None]:
    text = str(path)
    sys.path.insert(0, text)
    try:
        yield
    finally:
        if text in sys.path:
            sys.path.remove(text)


__all__ = ["F8UNITYMODS_ROOT_ENV", "UnityModdingAdapter"]
