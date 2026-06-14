from __future__ import annotations

from enum import Enum
from typing import Any

from msgspec import Struct, field

from f8pystudio.assets.common import JsonObject, now_iso

DEFAULT_SKELETON_UDP_PORT = 39540
MODDING_RECIPE_SCHEMA_VERSION = "f8moddingrecipe/1"


class ModdingEngineKind(Enum):
    unity = "unity"
    unreal = "unreal"
    vam = "vam"
    unknown = "unknown"


class ModdingBackendKind(Enum):
    mono = "mono"
    il2cpp = "il2cpp"
    ue4ss = "ue4ss"
    vam_mvrscript = "vam_mvrscript"
    unknown = "unknown"


class ModdingRecipeDraftOriginKind(Enum):
    new = "new"
    copy_local = "copy_local"
    copy_remote = "copy_remote"
    from_install = "from_install"


class ModdingTarget(Struct, kw_only=True):
    selectedPath: str
    resolvedGameRoot: str = ""
    executablePath: str = ""
    processName: str = ""


class ModdingDetectionReport(Struct, kw_only=True):
    target: ModdingTarget
    engine: ModdingEngineKind = ModdingEngineKind.unknown
    backend: ModdingBackendKind = ModdingBackendKind.unknown
    architecture: str = "unknown"
    engineVersion: str = ""
    existingLoaderState: JsonObject = field(default_factory=dict)
    matchedProfileId: str = ""
    matchedProfileName: str = ""
    selectedExporter: str = "auto"
    warnings: list[str] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


class ModdingInstallOption(Struct, kw_only=True):
    exporter: str = "auto"
    udpPort: int = DEFAULT_SKELETON_UDP_PORT
    installRuntimeUnityEditor: bool = False
    installCinematicUnityExplorer: bool = False
    installConfigurationManager: bool = False
    installUniversalUnityDemosaics: bool = False
    offline: bool = False
    refreshRemoteCache: bool = False
    forceReinstall: bool = False
    preferLocalConfigs: bool | None = None
    allowRemoteConfigs: bool | None = None
    releaseTag: str = ""
    skipExporter: bool = False


class ModdingInstallAction(Struct, kw_only=True):
    action: str
    description: str = ""
    writes: list[str] = field(default_factory=list)
    preserves: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    payload: JsonObject = field(default_factory=dict)


class ModdingPlan(Struct, kw_only=True):
    report: ModdingDetectionReport
    options: ModdingInstallOption = field(default_factory=ModdingInstallOption)
    actions: list[ModdingInstallAction] = field(default_factory=list)
    blockingErrors: list[str] = field(default_factory=list)
    filesToCreateOrUpdate: list[str] = field(default_factory=list)
    filesToPreserveOrBackup: list[str] = field(default_factory=list)
    previewPayload: JsonObject = field(default_factory=dict)
    graphBuildPlan: JsonObject = field(default_factory=dict)


class ModdingInstallResult(Struct, kw_only=True):
    plan: ModdingPlan
    executedActions: list[JsonObject] = field(default_factory=list)
    backupPaths: list[str] = field(default_factory=list)
    installedPluginVersions: JsonObject = field(default_factory=dict)
    configPaths: list[str] = field(default_factory=list)
    profilePaths: list[str] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


class ModdingVerificationReport(Struct, kw_only=True):
    udpPort: int = DEFAULT_SKELETON_UDP_PORT
    listenerStatus: str = "not_started"
    decodedSkeletonKeys: list[str] = field(default_factory=list)
    sampleCount: int = 0
    pyStudioEvidence: JsonObject = field(default_factory=dict)
    recentDecoderErrors: list[str] = field(default_factory=list)
    graphBuildPlan: JsonObject = field(default_factory=dict)
    verifiedAt: str = field(default_factory=now_iso)


class F8ModdingRecipeRecord(Struct, kw_only=True):
    recipeId: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    content: JsonObject = field(default_factory=dict)
    lastTargetPath: str = ""
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8ModdingRecipeDraftEntry(Struct, kw_only=True):
    draftId: str
    record: F8ModdingRecipeRecord
    originKind: ModdingRecipeDraftOriginKind | None = None
    publishTargetAssetId: str | None = None
    publishBaseRemoteVersionNumber: int | None = None
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


def normalize_backend_kind(value: object) -> ModdingBackendKind:
    text = str(value or "").strip().lower()
    if text == "mono":
        return ModdingBackendKind.mono
    if text == "il2cpp":
        return ModdingBackendKind.il2cpp
    if text == "ue4ss":
        return ModdingBackendKind.ue4ss
    if text in {"vam", "vam_mvrscript", "mvrscript"}:
        return ModdingBackendKind.vam_mvrscript
    return ModdingBackendKind.unknown


def normalize_exporter_key(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"skeleton", "default", "f8skeletonstreamer"}:
        return "skeleton"
    if text in {"live2d", "f8live2dstreamer"}:
        return "live2d"
    return "auto"


def modding_record_content(
    *,
    engine: ModdingEngineKind,
    backend: ModdingBackendKind,
    game_profile: JsonObject,
    installer: JsonObject,
    payloads: JsonObject,
    py_studio: JsonObject,
    verification: JsonObject,
    notes: str = "",
) -> JsonObject:
    return {
        "schemaVersion": MODDING_RECIPE_SCHEMA_VERSION,
        "engine": engine.value,
        "backend": backend.value,
        "gameProfile": dict(game_profile),
        "installer": dict(installer),
        "payloads": dict(payloads),
        "pyStudio": dict(py_studio),
        "verification": dict(verification),
        "notes": str(notes or ""),
    }


def typed_dict(value: object) -> JsonObject:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "DEFAULT_SKELETON_UDP_PORT",
    "MODDING_RECIPE_SCHEMA_VERSION",
    "F8ModdingRecipeDraftEntry",
    "F8ModdingRecipeRecord",
    "ModdingBackendKind",
    "ModdingDetectionReport",
    "ModdingEngineKind",
    "ModdingInstallAction",
    "ModdingInstallOption",
    "ModdingInstallResult",
    "ModdingPlan",
    "ModdingRecipeDraftOriginKind",
    "ModdingTarget",
    "ModdingVerificationReport",
    "json_safe_value",
    "modding_record_content",
    "normalize_backend_kind",
    "normalize_exporter_key",
    "string_list",
    "typed_dict",
]
