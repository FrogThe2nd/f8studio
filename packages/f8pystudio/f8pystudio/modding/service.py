from __future__ import annotations

from pathlib import Path
from typing import Any

from f8pysdk.codec import dump_json
from f8unitymods_setup import __version__ as UNITYMODS_SETUP_VERSION

from f8pystudio.assets.common import JsonObject, new_asset_id, now_iso

from .graph_templates import skeleton_stream_graph_build_plan
from .models import (
    DEFAULT_SKELETON_UDP_PORT,
    F8ModdingRecipeRecord,
    ModdingBackendKind,
    ModdingDetectionReport,
    ModdingEngineKind,
    ModdingInstallOption,
    ModdingInstallResult,
    ModdingPlan,
    ModdingRecipeDraftOriginKind,
    ModdingTarget,
    ModdingVerificationReport,
    json_safe_value,
    modding_record_content,
    typed_dict,
)
from .recipe_repository import ModdingRecipeDraftService
from .redaction import sanitized_recipe_content
from .unity_adapter import UnityModdingAdapter
from .verification import verify_udp_skeleton_stream


class ModdingAutomationService:
    def __init__(self, *, recipe_db_path: Path | None = None) -> None:
        self._recipes = ModdingRecipeDraftService(recipe_db_path)
        self._unity = UnityModdingAdapter()

    def detect_target(self, *, target_path: str) -> dict[str, Any]:
        target = ModdingTarget(selectedPath=_required_text(target_path, "targetPath"))
        try:
            report = self._unity.detect(target)
        except (FileNotFoundError, OSError, RuntimeError, ValueError, ImportError) as exc:
            report = ModdingDetectionReport(
                target=target,
                engine=ModdingEngineKind.unknown,
                backend=ModdingBackendKind.unknown,
                warnings=[f"{type(exc).__name__}: {exc}"],
                raw={"detectError": f"{type(exc).__name__}: {exc}"},
            )
        return {"report": dump_json(report, mode="json")}

    def preview_install(self, *, target_path: str, options_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        options = _options_from_payload(options_payload)
        detected = self._unity.detect(ModdingTarget(selectedPath=_required_text(target_path, "targetPath")))
        if detected.engine is not ModdingEngineKind.unity:
            raise ValueError("Only Unity modding preview is implemented in the MVP")
        plan = self._unity.plan(detected, options)
        return {"plan": dump_json(plan, mode="json")}

    def apply_install(self, *, plan_payload: dict[str, Any], confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ValueError("modding.applyInstall requires confirm=true")
        plan = _plan_from_payload(plan_payload)
        if plan.report.engine is not ModdingEngineKind.unity:
            raise ValueError("Only Unity modding install is implemented in the MVP")
        result = self._unity.install(plan, confirm=True)
        return {"install": dump_json(result, mode="json")}

    def verify_stream(
        self,
        *,
        port: int = DEFAULT_SKELETON_UDP_PORT,
        host: str = "127.0.0.1",
        timeout_s: float = 3.0,
        max_samples: int = 8,
    ) -> dict[str, Any]:
        report = verify_udp_skeleton_stream(
            port=int(port),
            host=str(host or "127.0.0.1"),
            timeout_s=float(timeout_s),
            max_samples=int(max_samples),
        )
        return {"verification": dump_json(report, mode="json")}

    def create_recipe(
        self,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        detection_payload: dict[str, Any] | None = None,
        install_payload: dict[str, Any] | None = None,
        verification_payload: dict[str, Any] | None = None,
        graph_payload: dict[str, Any] | None = None,
        notes: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("modding.createRecipe requires confirm=true")
        detection = _optional_report_from_payload(detection_payload)
        install = _optional_install_result_from_payload(install_payload)
        verification = _optional_verification_from_payload(verification_payload)
        record = self._recipe_record_from_inputs(
            name=name,
            description=description,
            tags=[] if tags is None else list(tags),
            detection=detection,
            install=install,
            verification=verification,
            graph_payload={} if graph_payload is None else dict(graph_payload),
            notes=notes,
        )
        saved = self._recipes.create_draft_from_record(
            record,
            origin_kind=ModdingRecipeDraftOriginKind.from_install,
            publish_target_asset_id=None,
            publish_base_remote_version_number=None,
            draft_id=record.recipeId,
        )
        return {"recipe": dump_json(saved, mode="json")}

    def recipe_list(self) -> dict[str, Any]:
        return {"recipes": [dump_json(draft, mode="json") for draft in self._recipes.list_drafts()]}

    def recipe_load(self, *, recipe_id: str) -> dict[str, Any]:
        draft = self._recipes.draft(_required_text(recipe_id, "recipeId"))
        if draft is None:
            raise FileNotFoundError(f"Modding recipe not found: {recipe_id}")
        return {"recipe": dump_json(draft, mode="json")}

    def recipe_export(self, *, recipe_id: str, path: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("modding.recipeExport requires confirm=true")
        out_path = self._recipes.export_draft(_required_text(recipe_id, "recipeId"), _required_text(path, "path"))
        return {"path": str(out_path)}

    def _recipe_record_from_inputs(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        detection: ModdingDetectionReport | None,
        install: ModdingInstallResult | None,
        verification: ModdingVerificationReport | None,
        graph_payload: JsonObject,
        notes: str,
    ) -> F8ModdingRecipeRecord:
        recipe_id = new_asset_id()
        engine = detection.engine if detection is not None else ModdingEngineKind.unknown
        backend = detection.backend if detection is not None else ModdingBackendKind.unknown
        game_profile = _game_profile_payload(detection)
        installer = _installer_payload(detection=detection, install=install)
        payloads = _payloads_from_install(install)
        py_studio = _py_studio_payload(graph_payload, verification)
        verification_content = _verification_payload(verification)
        content = modding_record_content(
            engine=engine,
            backend=backend,
            game_profile=game_profile,
            installer=installer,
            payloads=payloads,
            py_studio=py_studio,
            verification=verification_content,
            notes=notes,
        )
        sanitized = sanitized_recipe_content(content)
        timestamp = now_iso()
        return F8ModdingRecipeRecord(
            recipeId=recipe_id,
            name=str(name or "").strip() or "Untitled Modding Recipe",
            description=str(description or ""),
            tags=[str(tag).strip() for tag in list(tags or []) if str(tag).strip()],
            content=sanitized,
            lastTargetPath="" if detection is None else detection.target.selectedPath,
            createdAt=timestamp,
            updatedAt=timestamp,
        )


def _game_profile_payload(detection: ModdingDetectionReport | None) -> JsonObject:
    if detection is None:
        return {"profileId": "", "name": "", "processAliases": []}
    aliases: list[str] = []
    if detection.target.processName:
        aliases.append(detection.target.processName)
    return {
        "profileId": detection.matchedProfileId,
        "profileHash": str(detection.raw.get("profile_sha256") or detection.raw.get("profile_hash") or ""),
        "name": detection.matchedProfileName,
        "processAliases": aliases,
        "engineVersion": detection.engineVersion,
        "architecture": detection.architecture,
    }


def _installer_payload(
    *,
    detection: ModdingDetectionReport | None,
    install: ModdingInstallResult | None,
) -> JsonObject:
    selected_exporter = ""
    if install is not None:
        selected_exporter = install.plan.report.selectedExporter
    elif detection is not None:
        selected_exporter = detection.selectedExporter
    plugin_versions = {} if install is None else dict(install.installedPluginVersions)
    options = {} if install is None else dump_json(install.plan.options, mode="json")
    return {
        "tool": "f8unitymods",
        "toolVersion": UNITYMODS_SETUP_VERSION,
        "toolCommit": str(plugin_versions.get("toolCommit") or ""),
        "requiredToolVersion": f">={UNITYMODS_SETUP_VERSION}",
        "selectedExporter": selected_exporter or "auto",
        "optionalUtilities": options,
        "releaseTags": plugin_versions,
    }


def _payloads_from_install(install: ModdingInstallResult | None) -> JsonObject:
    if install is None:
        return {"exporterConfig": {}, "profile": {}}
    return {
        "exporterConfig": {"paths": list(install.configPaths)},
        "profile": {"paths": list(install.profilePaths)},
        "installSummary": dict(install.raw),
    }


def _py_studio_payload(graph_payload: JsonObject, verification: ModdingVerificationReport | None) -> JsonObject:
    graph_plan = skeleton_stream_graph_build_plan(
        port=DEFAULT_SKELETON_UDP_PORT if verification is None else int(verification.udpPort)
    )
    raw_nodes = graph_payload.get("nodes")
    linked_component_ids: list[str] = []
    if isinstance(raw_nodes, list):
        for item in raw_nodes:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("nodeId") or "").strip()
            if node_id:
                linked_component_ids.append(node_id)
    selected_plan = dict(graph_payload) if graph_payload else graph_plan
    return {
        "linkedComponentIds": linked_component_ids,
        "linkedProjectIds": [],
        "graphFragment": dict(graph_payload),
        "defaultGraphBuildPlan": selected_plan,
        "stableSelectors": typed_dict(selected_plan.get("stableSelectors")),
        "axis": typed_dict(selected_plan.get("axis")),
        "calibration": typed_dict(selected_plan.get("calibration")),
        "safety": typed_dict(selected_plan.get("safety")),
    }


def _verification_payload(verification: ModdingVerificationReport | None) -> JsonObject:
    if verification is None:
        return {
            "streamSchema": "f8.skeleton.binary.v2",
            "udpPort": DEFAULT_SKELETON_UDP_PORT,
            "sampleKeys": [],
            "timestamp": "",
        }
    return {
        "streamSchema": "f8.skeleton.binary.v2",
        "udpPort": int(verification.udpPort),
        "sampleKeys": list(verification.decodedSkeletonKeys),
        "skeletons": list(verification.decodedSkeletons),
        "packetCount": int(verification.packetCount),
        "decodedFrameCount": int(verification.decodedFrameCount),
        "sampleCount": int(verification.sampleCount),
        "listenerStatus": verification.listenerStatus,
        "decoderErrors": list(verification.recentDecoderErrors),
        "evidence": dict(verification.pyStudioEvidence),
        "timestamp": verification.verifiedAt,
    }


def _options_from_payload(payload: dict[str, Any] | None) -> ModdingInstallOption:
    data = {} if payload is None else dict(payload)
    return ModdingInstallOption(
        exporter=str(data.get("exporter") or "auto"),
        udpPort=int(data.get("udpPort") or DEFAULT_SKELETON_UDP_PORT),
        installRuntimeUnityEditor=bool(data.get("installRuntimeUnityEditor", False)),
        installCinematicUnityExplorer=bool(data.get("installCinematicUnityExplorer", False)),
        installConfigurationManager=bool(data.get("installConfigurationManager", False)),
        installUniversalUnityDemosaics=bool(data.get("installUniversalUnityDemosaics", False)),
        offline=bool(data.get("offline", False)),
        refreshRemoteCache=bool(data.get("refreshRemoteCache", False)),
        forceReinstall=bool(data.get("forceReinstall", False)),
        preferLocalConfigs=_optional_bool(data.get("preferLocalConfigs")),
        allowRemoteConfigs=_optional_bool(data.get("allowRemoteConfigs")),
        releaseTag=str(data.get("releaseTag") or ""),
        skipExporter=bool(data.get("skipExporter", False)),
    )


def _plan_from_payload(payload: dict[str, Any]) -> ModdingPlan:
    from f8pysdk.codec import validate_as

    return validate_as(ModdingPlan, payload)


def _optional_report_from_payload(payload: dict[str, Any] | None) -> ModdingDetectionReport | None:
    if payload is None:
        return None
    from f8pysdk.codec import validate_as

    data = dict(payload)
    report = data.get("report")
    if isinstance(report, dict):
        return validate_as(ModdingDetectionReport, report)
    return validate_as(ModdingDetectionReport, data)


def _optional_install_result_from_payload(payload: dict[str, Any] | None) -> ModdingInstallResult | None:
    if payload is None:
        return None
    from f8pysdk.codec import validate_as

    data = dict(payload)
    install = data.get("install")
    if isinstance(install, dict):
        return validate_as(ModdingInstallResult, install)
    return validate_as(ModdingInstallResult, data)


def _optional_verification_from_payload(payload: dict[str, Any] | None) -> ModdingVerificationReport | None:
    if payload is None:
        return None
    from f8pysdk.codec import validate_as

    data = dict(payload)
    verification = data.get("verification")
    if isinstance(verification, dict):
        return validate_as(ModdingVerificationReport, verification)
    return validate_as(ModdingVerificationReport, data)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


__all__ = ["ModdingAutomationService"]
