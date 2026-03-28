from __future__ import annotations

from typing import Any

from f8pysdk import F8VariantRecord, F8VariantRef
from f8pysdk.msgspec_codec import dump_json, validate_as


def variant_ref_to_json(ref: F8VariantRef) -> dict[str, Any]:
    return dump_json(ref, mode="json")


def variant_ref_from_record(record: F8VariantRecord) -> F8VariantRef:
    return record.variant_ref()


def variant_ref_from_dict(value: dict[str, Any]) -> F8VariantRef:
    return validate_as(F8VariantRef, value)


def normalize_variant_sys_metadata(f8_sys: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(f8_sys, dict):
        return {}

    out = dict(f8_sys)
    out.pop("variantId", None)
    out.pop("variantName", None)

    raw_ref = f8_sys.get("variantRef")
    if isinstance(raw_ref, dict):
        try:
            ref = variant_ref_from_dict(raw_ref)
            out["variantRef"] = variant_ref_to_json(ref)
            return out
        except Exception:
            out.pop("variantRef", None)
            return out

    return out
