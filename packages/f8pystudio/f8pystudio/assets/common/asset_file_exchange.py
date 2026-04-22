from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from msgspec import Struct

from f8pysdk.codec import dump_json, validate_as

from .common import json_object_from_value, json_object_loads

if TYPE_CHECKING:
    from ..components.component_models import F8ComponentRecord
    from ..variants.variant_models import F8VariantRecord
else:
    F8ComponentRecord = object
    F8VariantRecord = object


class F8ComponentAssetFile(Struct, kw_only=True):
    componentId: str
    assetType: str
    versionNumber: int
    record: F8ComponentRecord


class F8VariantAssetFile(Struct, kw_only=True):
    variantId: str
    assetType: str
    versionNumber: int
    record: F8VariantRecord


def read_component_asset_file(path: str) -> F8ComponentAssetFile:
    in_path = _input_path(path, label="Component asset JSON")
    raw_payload = json_object_loads(in_path.read_text(encoding="utf-8"))
    from ..components.component_models import F8ComponentRecord

    asset_type = str(raw_payload.get("assetType") or "")
    if asset_type != "component":
        raise ValueError(f"Expected component asset payload, got assetType={asset_type!r}")
    record = validate_as(F8ComponentRecord, json_object_from_value(raw_payload.get("record")))
    payload = F8ComponentAssetFile(
        componentId=str(raw_payload.get("componentId") or ""),
        assetType=asset_type,
        versionNumber=int(raw_payload.get("versionNumber") or 0),
        record=record,
    )
    if str(payload.componentId) != str(record.componentId):
        raise ValueError("Component asset payload id does not match record.componentId.")
    return payload


def write_component_asset_file(
    path: str,
    *,
    record: F8ComponentRecord,
    version_number: int,
) -> Path:
    out_path = _output_path(path)
    payload = {
        "componentId": str(record.componentId),
        "assetType": "component",
        "versionNumber": int(version_number),
        "record": dump_json(record, mode="json"),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out_path


def read_variant_asset_file(path: str) -> F8VariantAssetFile:
    in_path = _input_path(path, label="Variant asset JSON")
    raw_payload = json_object_loads(in_path.read_text(encoding="utf-8"))
    from ..variants.variant_models import F8VariantRecord

    asset_type = str(raw_payload.get("assetType") or "")
    if asset_type != "variant":
        raise ValueError(f"Expected variant asset payload, got assetType={asset_type!r}")
    record = validate_as(F8VariantRecord, json_object_from_value(raw_payload.get("record")))
    payload = F8VariantAssetFile(
        variantId=str(raw_payload.get("variantId") or ""),
        assetType=asset_type,
        versionNumber=int(raw_payload.get("versionNumber") or 0),
        record=record,
    )
    if str(payload.variantId) != str(record.variantId):
        raise ValueError("Variant asset payload id does not match record.variantId.")
    return payload


def write_variant_asset_file(
    path: str,
    *,
    record: F8VariantRecord,
    version_number: int,
) -> Path:
    out_path = _output_path(path)
    payload = {
        "variantId": str(record.variantId),
        "assetType": "variant",
        "versionNumber": int(version_number),
        "record": dump_json(record, mode="json"),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out_path


def _input_path(path: str, *, label: str) -> Path:
    in_path = Path(str(path or "").strip())
    if not in_path.is_file():
        raise FileNotFoundError(f"{label} not found: {in_path}")
    return in_path


def _output_path(path: str) -> Path:
    out_path = Path(str(path or "").strip())
    if not str(out_path):
        raise ValueError("Export path is empty")
    if out_path.suffix.lower() != ".json":
        out_path = out_path.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


__all__ = [
    "F8ComponentAssetFile",
    "F8VariantAssetFile",
    "read_component_asset_file",
    "write_component_asset_file",
    "read_variant_asset_file",
    "write_variant_asset_file",
]
