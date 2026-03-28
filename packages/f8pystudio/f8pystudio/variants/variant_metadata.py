from __future__ import annotations

from typing import Any

from f8pysdk import F8VariantRef
from f8pysdk.msgspec_codec import dump_json, validate_as

from .variant_models import F8NodeVariantRecord
from .variant_repository import variant_record


def variant_ref_to_json(ref: F8VariantRef) -> dict[str, Any]:
    return dump_json(ref, mode="json")


def variant_ref_from_record(record: F8NodeVariantRecord) -> F8VariantRef:
    return record.variant_ref()


def variant_ref_from_dict(value: dict[str, Any]) -> F8VariantRef:
    return validate_as(F8VariantRef, value)


def normalize_variant_sys_metadata(f8_sys: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(f8_sys, dict):
        return {}

    raw_ref = f8_sys.get("variantRef")
    if isinstance(raw_ref, dict):
        try:
            ref = variant_ref_from_dict(raw_ref)
            out = dict(f8_sys)
            out["variantRef"] = variant_ref_to_json(ref)
            out.pop("variantId", None)
            out.pop("variantName", None)
            return out
        except Exception:
            return dict(f8_sys)

    legacy_variant_id = str(f8_sys.get("variantId") or "").strip()
    if legacy_variant_id:
        record = variant_record(legacy_variant_id)
        if record is not None:
            out = dict(f8_sys)
            out["variantRef"] = variant_ref_to_json(variant_ref_from_record(record))
            out.pop("variantId", None)
            out.pop("variantName", None)
            return out

    return dict(f8_sys)
