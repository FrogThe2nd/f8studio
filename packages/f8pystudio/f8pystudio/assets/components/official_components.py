from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from f8pysdk.codec import validate_as
from f8pysdk.specs import F8ComponentRecord

from ...nodegraph.session_schema import extract_layout
from .component_models import F8ComponentEntry, F8ComponentSourceKind
from .component_taxonomy import validate_component_tags


BUNDLED_OFFICIAL_COMPONENT_TAG = "distribution:bundled"

_PHYSICAL_OUTPUT_OPERATOR_CLASSES = frozenset(
    {
        "f8.buttplug_out",
        "f8.handy_out",
        "f8.lovense_out",
        "f8.serial_out",
    }
)
_LOCAL_SELECTION_FIELDS = frozenset(
    {
        "connectionKey",
        "defaultToy",
        "port",
        "selectedDevice",
    }
)


def bundled_official_component_entries() -> list[F8ComponentEntry]:
    return list(_cached_bundled_official_component_entries())


@lru_cache(maxsize=1)
def _cached_bundled_official_component_entries() -> tuple[F8ComponentEntry, ...]:
    resource_root = files("f8pystudio").joinpath("resources", "components")
    entries: list[F8ComponentEntry] = []
    for resource in sorted(resource_root.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json"):
            continue
        try:
            raw_payload: Any = json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid bundled component resource {resource.name}: {exc}") from exc
        entries.append(_entry_from_bundled_payload(raw_payload, resource_name=resource.name))
    if not entries:
        raise ValueError("no bundled official components were found")
    return tuple(sorted(entries, key=lambda entry: (entry.record.name.lower(), entry.record.componentId)))


def _entry_from_bundled_payload(raw_payload: Any, *, resource_name: str) -> F8ComponentEntry:
    if not isinstance(raw_payload, dict):
        raise ValueError(f"bundled component {resource_name} must contain a JSON object")
    if raw_payload.get("assetType") != "component":
        raise ValueError(f"bundled component {resource_name} must use assetType=component")
    version_number = int(raw_payload.get("versionNumber") or 0)
    if version_number < 1:
        raise ValueError(f"bundled component {resource_name} must have a positive versionNumber")
    record = validate_as(F8ComponentRecord, raw_payload.get("record"))
    if str(raw_payload.get("componentId") or "") != str(record.componentId):
        raise ValueError(f"bundled component {resource_name} id does not match record.componentId")
    _validate_bundled_component_record(record, resource_name=resource_name)
    return F8ComponentEntry(
        record=record,
        source=F8ComponentSourceKind.remote_official,
        ownerDisplayName="Feel8",
        remoteVersionNumber=version_number,
        installed=True,
        hasCachedContent=True,
    )


def _validate_bundled_component_record(record: F8ComponentRecord, *, resource_name: str) -> None:
    tags = list(record.tags or [])
    validate_component_tags(tags)
    if BUNDLED_OFFICIAL_COMPONENT_TAG not in tags:
        raise ValueError(f"bundled component {resource_name} is missing {BUNDLED_OFFICIAL_COMPONENT_TAG}")
    layout = extract_layout(record.content)
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError(f"bundled component {resource_name} must contain at least one node")
    for node_id, node_payload in nodes.items():
        if not isinstance(node_payload, dict):
            raise ValueError(f"bundled component {resource_name} has invalid node {node_id}")
        spec_payload = node_payload.get("f8_spec")
        if not isinstance(spec_payload, dict):
            raise ValueError(f"bundled component {resource_name} node {node_id} has no typed spec")
        operator_class = str(spec_payload.get("operatorClass") or "").strip()
        if operator_class not in _PHYSICAL_OUTPUT_OPERATOR_CLASSES:
            continue
        custom = node_payload.get("custom")
        if not isinstance(custom, dict) or custom.get("enabled") is not False:
            raise ValueError(f"bundled component {resource_name} physical output {node_id} must set enabled=false")
        for field in _LOCAL_SELECTION_FIELDS:
            value = custom.get(field)
            if value not in (None, ""):
                raise ValueError(f"bundled component {resource_name} physical output {node_id} must clear {field}")


def component_entry_is_bundled_official(entry: F8ComponentEntry | None) -> bool:
    if entry is None or entry.source != F8ComponentSourceKind.remote_official:
        return False
    return BUNDLED_OFFICIAL_COMPONENT_TAG in set(entry.record.tags or [])


__all__ = [
    "BUNDLED_OFFICIAL_COMPONENT_TAG",
    "bundled_official_component_entries",
    "component_entry_is_bundled_official",
]
