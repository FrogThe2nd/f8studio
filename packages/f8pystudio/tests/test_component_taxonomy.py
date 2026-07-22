from __future__ import annotations

from types import SimpleNamespace

import pytest

from f8pysdk.specs import F8ComponentRecord
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentSourceKind
from f8pystudio.assets.components.component_taxonomy import (
    ComponentRole,
    ComponentTagDimension,
    build_component_tags,
    component_matches_role,
    component_taxonomy_from_tags,
    partition_component_tags,
    replace_component_tag_dimension,
    validate_component_tags,
)
from f8pystudio.assets.ui.component_catalog_entries import ComponentCatalogEntriesMixin


def test_component_taxonomy_extracts_reserved_dimensions() -> None:
    taxonomy = component_taxonomy_from_tags(
        [
            "role:output",
            "workflow:video",
            "signal:position",
            "signal:vibrate",
            "protocol:buttplug",
            "level:starter",
            "community-tag",
        ]
    )

    assert taxonomy.role == ComponentRole.OUTPUT
    assert taxonomy.workflows == frozenset({"video"})
    assert taxonomy.signals == frozenset({"position", "vibrate"})
    assert taxonomy.protocols == frozenset({"buttplug"})
    assert taxonomy.levels == frozenset({"starter"})


def test_component_role_filter_requires_one_matching_role() -> None:
    assert component_matches_role(["role:shape"], ComponentRole.SHAPE) is True
    assert component_matches_role(["role:shape"], ComponentRole.OUTPUT) is False
    assert component_matches_role([], ComponentRole.SHAPE) is False
    assert component_matches_role([], None) is True


@pytest.mark.parametrize(
    "tags",
    [
        ["role:shape", "role:output"],
        ["role:unknown"],
        ["role:Shape"],
        ["signal:Position"],
        ["protocol:"],
    ],
)
def test_component_tag_validation_rejects_invalid_reserved_tags(tags: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_component_tags(tags)


def test_component_tag_validation_allows_unreserved_future_tags() -> None:
    validate_component_tags(["author:example", "future-tag", "workflow:custom_game"])


def test_component_tag_partition_preserves_custom_namespaced_tags() -> None:
    partition = partition_component_tags(["role:shape", "signal:position", "author:example", "community-tag"])

    assert partition.reserved_tags == ("role:shape", "signal:position")
    assert partition.free_tags == ("author:example", "community-tag")


def test_replacing_one_dimension_preserves_other_and_unknown_tags() -> None:
    tags = replace_component_tag_dimension(
        ["role:shape", "signal:position", "protocol:tcode", "author:example"],
        ComponentTagDimension.SIGNAL,
        ["vibrate", "rotate"],
    )

    assert tags == [
        "role:shape",
        "protocol:tcode",
        "author:example",
        "signal:vibrate",
        "signal:rotate",
    ]


def test_structured_component_tags_round_trip_free_tags() -> None:
    tags = build_component_tags(
        role=ComponentRole.OUTPUT,
        workflows=["video", "game_mod"],
        signals=["position"],
        protocols=["buttplug"],
        levels=["starter"],
        free_tags=["author:example", "community-tag", "community-tag"],
    )

    assert tags == [
        "role:output",
        "workflow:video",
        "workflow:game_mod",
        "signal:position",
        "protocol:buttplug",
        "level:starter",
        "author:example",
        "community-tag",
    ]


def test_structured_component_tags_reject_reserved_values_in_free_tags() -> None:
    with pytest.raises(ValueError, match="must be entered in the signal field"):
        build_component_tags(
            role=None,
            workflows=[],
            signals=[],
            protocols=[],
            levels=[],
            free_tags=["signal:position"],
        )


def test_catalog_role_filter_uses_component_tags() -> None:
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-output",
            name="Output",
            tags=["role:output"],
            content={},
        ),
        source=F8ComponentSourceKind.local,
    )
    host = SimpleNamespace(_current_component_role_filter=lambda: ComponentRole.OUTPUT)

    assert ComponentCatalogEntriesMixin._matches_role_filter(host, entry) is True  # type: ignore[arg-type]
