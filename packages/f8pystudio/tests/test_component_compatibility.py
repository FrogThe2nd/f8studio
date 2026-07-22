from __future__ import annotations

import pytest

from f8pysdk.specs import F8DataPortSpec, number_schema, string_schema
from f8pystudio.assets.components.component_compatibility import (
    SemanticSignal,
    evaluate_component_compatibility,
)


def test_normalized_position_matches_tagged_output_component() -> None:
    decision = evaluate_component_compatibility(
        source_port=F8DataPortSpec(
            name="position",
            valueSchema=number_schema(minimum=0.0, maximum=1.0),
        ),
        signal=SemanticSignal.POSITION,
        component_tags=["role:output", "signal:position", "protocol:handy"],
    )

    assert decision.compatible is True
    assert decision.reasons == ()
    assert decision.warnings == ()


@pytest.mark.parametrize(
    ("minimum", "maximum", "reason_fragment"),
    [
        (-1.0, 1.0, "below 0"),
        (0.0, 20.0, "above 1"),
    ],
)
def test_out_of_range_numeric_signal_requires_shape_before_output(
    minimum: float,
    maximum: float,
    reason_fragment: str,
) -> None:
    decision = evaluate_component_compatibility(
        source_port=F8DataPortSpec(
            name="value",
            valueSchema=number_schema(minimum=minimum, maximum=maximum),
        ),
        signal=SemanticSignal.POSITION,
        component_tags=["role:output", "signal:position"],
    )

    assert decision.compatible is False
    assert reason_fragment in decision.reasons[0]


def test_unconstrained_number_is_compatible_with_range_warning() -> None:
    decision = evaluate_component_compatibility(
        source_port=F8DataPortSpec(name="value", valueSchema=number_schema()),
        signal=SemanticSignal.VIBRATE,
        component_tags=["role:output", "signal:vibrate"],
    )

    assert decision.compatible is True
    assert decision.warnings == ("source port value does not declare a complete 0..1 range",)


def test_missing_signal_tag_is_not_compatible() -> None:
    decision = evaluate_component_compatibility(
        source_port=F8DataPortSpec(
            name="position",
            valueSchema=number_schema(minimum=0.0, maximum=1.0),
        ),
        signal=SemanticSignal.POSITION,
        component_tags=["role:output", "protocol:handy"],
    )

    assert decision.compatible is False
    assert decision.reasons == ("component does not declare signal:position",)


def test_tcode_requires_string_source() -> None:
    compatible = evaluate_component_compatibility(
        source_port=F8DataPortSpec(name="tcode", valueSchema=string_schema()),
        signal=SemanticSignal.TCODE,
        component_tags=["role:output", "signal:tcode", "protocol:serial"],
    )
    incompatible = evaluate_component_compatibility(
        source_port=F8DataPortSpec(name="value", valueSchema=number_schema()),
        signal=SemanticSignal.TCODE,
        component_tags=["role:output", "signal:tcode", "protocol:serial"],
    )

    assert compatible.compatible is True
    assert incompatible.compatible is False
    assert "string schema" in incompatible.reasons[0]
