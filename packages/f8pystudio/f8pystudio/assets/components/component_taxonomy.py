from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class ComponentRole(StrEnum):
    SOURCE = "source"
    DETECT = "detect"
    SHAPE = "shape"
    OUTPUT = "output"
    VIEW = "view"
    COMPLETE = "complete"


class ComponentTagDimension(StrEnum):
    ROLE = "role"
    WORKFLOW = "workflow"
    SIGNAL = "signal"
    PROTOCOL = "protocol"
    LEVEL = "level"


COMPONENT_ROLE_LABELS: dict[ComponentRole, str] = {
    ComponentRole.SOURCE: "Source",
    ComponentRole.DETECT: "Detect",
    ComponentRole.SHAPE: "Shape",
    ComponentRole.OUTPUT: "Output",
    ComponentRole.VIEW: "View",
    ComponentRole.COMPLETE: "Complete",
}


_RESERVED_PREFIXES = frozenset(dimension.value for dimension in ComponentTagDimension)
_TAG_VALUE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class ComponentTaxonomy:
    role: ComponentRole | None
    workflows: frozenset[str]
    signals: frozenset[str]
    protocols: frozenset[str]
    levels: frozenset[str]


@dataclass(frozen=True)
class ComponentTagPartition:
    reserved_tags: tuple[str, ...]
    free_tags: tuple[str, ...]


def _normalized_tags(tags: Iterable[str]) -> tuple[str, ...]:
    return tuple(str(tag or "").strip() for tag in tags if str(tag or "").strip())


def validate_component_tags(tags: Iterable[str]) -> None:
    roles: set[ComponentRole] = set()
    for tag in _normalized_tags(tags):
        if ":" not in tag:
            continue
        prefix, value = tag.split(":", 1)
        if prefix not in _RESERVED_PREFIXES:
            continue
        if tag != tag.lower() or not _TAG_VALUE_PATTERN.fullmatch(value):
            raise ValueError(f"reserved component tag must be normalized lowercase: {tag}")
        if prefix == "role":
            try:
                roles.add(ComponentRole(value))
            except ValueError as exc:
                raise ValueError(f"unknown component role tag: {tag}") from exc
    if len(roles) > 1:
        role_values = ", ".join(sorted(role.value for role in roles))
        raise ValueError(f"component must declare at most one role tag; found: {role_values}")


def component_taxonomy_from_tags(tags: Iterable[str]) -> ComponentTaxonomy:
    normalized = _normalized_tags(tags)
    role_values: set[ComponentRole] = set()
    for prefix, value in (_split_reserved_tag(tag) for tag in normalized):
        if prefix != "role":
            continue
        role = _component_role_or_none(value)
        if role is not None:
            role_values.add(role)
    role = next(iter(role_values)) if len(role_values) == 1 else None
    return ComponentTaxonomy(
        role=role,
        workflows=_values_for_prefix(normalized, "workflow"),
        signals=_values_for_prefix(normalized, "signal"),
        protocols=_values_for_prefix(normalized, "protocol"),
        levels=_values_for_prefix(normalized, "level"),
    )


def partition_component_tags(tags: Iterable[str]) -> ComponentTagPartition:
    reserved_tags: list[str] = []
    free_tags: list[str] = []
    for tag in _normalized_tags(tags):
        prefix, _value = _split_reserved_tag(tag)
        if prefix:
            reserved_tags.append(tag)
        else:
            free_tags.append(tag)
    return ComponentTagPartition(
        reserved_tags=tuple(reserved_tags),
        free_tags=tuple(free_tags),
    )


def replace_component_tag_dimension(
    tags: Iterable[str],
    dimension: ComponentTagDimension,
    values: Iterable[str],
) -> list[str]:
    """Replace one taxonomy dimension while preserving every other tag."""

    target_prefix = dimension.value
    preserved = [tag for tag in _normalized_tags(tags) if _split_reserved_tag(tag)[0] != target_prefix]
    normalized_values = _normalized_dimension_values(values)
    updated = [*preserved, *(f"{target_prefix}:{value}" for value in normalized_values)]
    validate_component_tags(updated)
    return updated


def build_component_tags(
    *,
    role: ComponentRole | None,
    workflows: Iterable[str],
    signals: Iterable[str],
    protocols: Iterable[str],
    levels: Iterable[str],
    free_tags: Iterable[str],
) -> list[str]:
    """Build the existing flat tag representation from structured authoring fields."""

    normalized_free_tags = _deduplicated_tags(free_tags)
    for tag in normalized_free_tags:
        prefix, _value = _split_reserved_tag(tag)
        if prefix:
            raise ValueError(f"reserved component tag '{tag}' must be entered in the {prefix} field")

    tags: list[str] = []
    if role is not None:
        tags.append(f"role:{role.value}")
    tags.extend(f"workflow:{value}" for value in _normalized_dimension_values(workflows))
    tags.extend(f"signal:{value}" for value in _normalized_dimension_values(signals))
    tags.extend(f"protocol:{value}" for value in _normalized_dimension_values(protocols))
    tags.extend(f"level:{value}" for value in _normalized_dimension_values(levels))
    tags.extend(normalized_free_tags)
    validate_component_tags(tags)
    return tags


def component_matches_role(tags: Iterable[str], role: ComponentRole | None) -> bool:
    if role is None:
        return True
    return component_taxonomy_from_tags(tags).role == role


def _split_reserved_tag(tag: str) -> tuple[str, str]:
    if ":" not in tag:
        return "", ""
    prefix, value = tag.split(":", 1)
    if prefix not in _RESERVED_PREFIXES:
        return "", ""
    return prefix, value


def _component_role_or_none(value: str) -> ComponentRole | None:
    try:
        return ComponentRole(value)
    except ValueError:
        return None


def _values_for_prefix(tags: tuple[str, ...], target_prefix: str) -> frozenset[str]:
    return frozenset(
        value for prefix, value in (_split_reserved_tag(tag) for tag in tags) if prefix == target_prefix and bool(value)
    )


def _normalized_dimension_values(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(value or "").strip().lower() for value in values if str(value or "").strip())
    for value in normalized:
        if not _TAG_VALUE_PATTERN.fullmatch(value):
            raise ValueError("component taxonomy values must use lowercase letters, numbers, '-' or '_'")
    return _deduplicated_tags(normalized)


def _deduplicated_tags(tags: Iterable[str]) -> tuple[str, ...]:
    ordered: dict[str, None] = {}
    for tag in _normalized_tags(tags):
        ordered.setdefault(tag, None)
    return tuple(ordered)


__all__ = [
    "COMPONENT_ROLE_LABELS",
    "ComponentRole",
    "ComponentTagDimension",
    "ComponentTagPartition",
    "ComponentTaxonomy",
    "build_component_tags",
    "component_matches_role",
    "component_taxonomy_from_tags",
    "partition_component_tags",
    "replace_component_tag_dimension",
    "validate_component_tags",
]
