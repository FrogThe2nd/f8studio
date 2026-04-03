from __future__ import annotations

import logging
from typing import cast

import msgspec
from f8pysdk import F8VariantRecord, F8VariantRef
from f8pysdk.msgspec_codec import validate_as

from ..graph_assets.common import JsonObject, json_object_from_value

logger = logging.getLogger(__name__)


def variant_ref_to_json(ref: F8VariantRef) -> JsonObject:
    operator_class = None if ref.operatorClass is None or isinstance(ref.operatorClass, msgspec.UnsetType) else str(ref.operatorClass)
    return {
        "variantId": str(ref.variantId),
        "kind": str(ref.kind.value),
        "baseNodeType": str(ref.baseNodeType),
        "serviceClass": str(ref.serviceClass),
        "operatorClass": operator_class,
        "name": str(ref.name),
    }


def variant_ref_from_record(record: F8VariantRecord) -> F8VariantRef:
    operator_class = None if record.operatorClass is None or isinstance(record.operatorClass, msgspec.UnsetType) else str(record.operatorClass)
    return F8VariantRef(
        variantId=str(record.variantId),
        kind=record.kind,
        baseNodeType=str(record.baseNodeType),
        serviceClass=str(record.serviceClass),
        operatorClass=operator_class,
        name=str(record.name),
    )


def variant_ref_from_dict(value: JsonObject) -> F8VariantRef:
    return validate_as(F8VariantRef, value)


def normalize_variant_sys_metadata(f8_sys: JsonObject) -> JsonObject:
    out = dict(f8_sys)
    _ = out.pop("variantId", None)
    _ = out.pop("variantName", None)

    raw_ref = f8_sys.get("variantRef")
    if isinstance(raw_ref, dict):
        try:
            ref = variant_ref_from_dict(json_object_from_value(cast(object, raw_ref)))
            out["variantRef"] = variant_ref_to_json(ref)
            return out
        except Exception:
            logger.exception("Failed to normalize variantRef metadata")
            _ = out.pop("variantRef", None)
            return out

    return out
