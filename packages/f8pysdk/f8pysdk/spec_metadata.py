from __future__ import annotations

from typing import Any, Literal, Mapping

import msgspec

from .generated import F8OperatorSpec, F8ServiceSpec
from .msgspec_codec import validate_as


SpecKind = Literal["service", "operator"]
UNCATEGORIZED_PALETTE_CATEGORY = "uncategorized"


def spec_kind_from_spec(spec: F8ServiceSpec | F8OperatorSpec) -> SpecKind:
    if isinstance(spec, F8OperatorSpec):
        return "operator"
    return "service"


def spec_kind_from_mapping(value: Mapping[str, Any]) -> SpecKind | None:
    raw_kind = str(value.get("specKind") or "").strip().lower()
    if raw_kind == "service":
        return "service"
    if raw_kind == "operator":
        return "operator"
    return None


def palette_category_from_spec(spec: F8ServiceSpec | F8OperatorSpec) -> str:
    raw_value = spec.paletteCategory
    if isinstance(raw_value, msgspec.UnsetType):
        return UNCATEGORIZED_PALETTE_CATEGORY
    value = str(raw_value or "").strip()
    return value or UNCATEGORIZED_PALETTE_CATEGORY


def coerce_spec_payload(value: object) -> F8ServiceSpec | F8OperatorSpec:
    if isinstance(value, (F8OperatorSpec, F8ServiceSpec)):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"Unsupported spec payload type: {type(value)!r}")

    spec_kind = spec_kind_from_mapping(value)
    if spec_kind == "operator":
        return validate_as(F8OperatorSpec, value)
    if spec_kind == "service":
        return validate_as(F8ServiceSpec, value)

    # Compatibility path for persisted payloads that predate `specKind`.
    if "operatorClass" in value:
        return validate_as(F8OperatorSpec, value)
    return validate_as(F8ServiceSpec, value)
