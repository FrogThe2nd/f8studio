from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import msgspec

from f8pysdk.specs import F8DataPortSpec, F8NumberTypeSchema, F8StringTypeSchema

from .component_taxonomy import component_taxonomy_from_tags


class SemanticSignal(StrEnum):
    POSITION = "position"
    VIBRATE = "vibrate"
    ROTATE = "rotate"
    TCODE = "tcode"


@dataclass(frozen=True)
class ComponentCompatibility:
    compatible: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


_NORMALIZED_NUMERIC_SIGNALS = frozenset(
    {
        SemanticSignal.POSITION,
        SemanticSignal.VIBRATE,
        SemanticSignal.ROTATE,
    }
)


def evaluate_component_compatibility(
    *,
    source_port: F8DataPortSpec,
    signal: SemanticSignal,
    component_tags: Iterable[str],
) -> ComponentCompatibility:
    taxonomy = component_taxonomy_from_tags(component_tags)
    if signal.value not in taxonomy.signals:
        return ComponentCompatibility(
            compatible=False,
            reasons=(f"component does not declare signal:{signal.value}",),
        )

    schema = source_port.valueSchema
    if signal in _NORMALIZED_NUMERIC_SIGNALS:
        if not isinstance(schema, F8NumberTypeSchema):
            return ComponentCompatibility(
                compatible=False,
                reasons=(f"source port {source_port.name} must use a number schema",),
            )
        return _evaluate_normalized_number_schema(source_port=source_port, schema=schema)

    if signal == SemanticSignal.TCODE:
        if isinstance(schema, F8StringTypeSchema):
            return ComponentCompatibility(compatible=True)
        return ComponentCompatibility(
            compatible=False,
            reasons=(f"source port {source_port.name} must use a string schema for TCode",),
        )

    return ComponentCompatibility(
        compatible=False,
        reasons=(f"unsupported semantic signal: {signal.value}",),
    )


def _evaluate_normalized_number_schema(
    *,
    source_port: F8DataPortSpec,
    schema: F8NumberTypeSchema,
) -> ComponentCompatibility:
    minimum = _optional_float(schema.minimum)
    maximum = _optional_float(schema.maximum)
    reasons: list[str] = []
    warnings: list[str] = []
    if minimum is not None and minimum < 0.0:
        reasons.append(f"source port {source_port.name} minimum {minimum:g} is below 0")
    if maximum is not None and maximum > 1.0:
        reasons.append(f"source port {source_port.name} maximum {maximum:g} is above 1")
    if minimum is None or maximum is None:
        warnings.append(f"source port {source_port.name} does not declare a complete 0..1 range")
    return ComponentCompatibility(
        compatible=not reasons,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _optional_float(value: float | msgspec.UnsetType) -> float | None:
    if isinstance(value, msgspec.UnsetType):
        return None
    return float(value)


__all__ = [
    "ComponentCompatibility",
    "SemanticSignal",
    "evaluate_component_compatibility",
]
