from __future__ import annotations

import json
from pathlib import Path

from ..session_migration import SESSION_SCHEMA_VERSION, extract_layout
from .component_catalog import ComponentCatalogService
from .component_models import F8ComponentEntry, F8ComponentRecord, F8ComponentSourceKind
from .common import JsonObject, json_object_loads, json_string_list_loads


def _service() -> ComponentCatalogService:
    return ComponentCatalogService()


def list_component_entries(*, include_uninstalled: bool = False) -> list[F8ComponentEntry]:
    return _service().list_entries(include_uninstalled=include_uninstalled)


def component_entry(component_id: str, *, include_uninstalled: bool = True) -> F8ComponentEntry | None:
    return _service().entry(component_id, include_uninstalled=include_uninstalled)


def upsert_component(record: F8ComponentRecord) -> F8ComponentRecord:
    _ = _service().upsert_local_entry(F8ComponentEntry(record=record, source=F8ComponentSourceKind.local))
    return record


def delete_component(component_id: str) -> bool:
    return _service().delete_local_entry(component_id)


def import_component_from_json(path: str, *, metadata: dict[str, object] | None = None) -> F8ComponentRecord:
    in_path = Path(str(path or "").strip())
    if not in_path.is_file():
        raise FileNotFoundError(f"Component JSON not found: {in_path}")
    raw = json_object_loads(in_path.read_text(encoding="utf-8"))
    _ = extract_layout(raw)
    meta = {} if metadata is None else dict(metadata)
    record = F8ComponentRecord(
        componentId=str(meta.get("componentId") or ""),
        name=str(meta.get("name") or in_path.stem or "Imported Component"),
        description=str(meta.get("description") or ""),
        usageNotes=str(meta.get("usageNotes") or ""),
        tags=_metadata_tags(meta),
        schemaVersion=_content_schema_version(raw),
        content=raw,
    )
    return upsert_component(record)


def export_component_to_json(component_id: str, path: str) -> Path:
    entry = component_entry(component_id, include_uninstalled=True)
    if entry is None:
        raise FileNotFoundError(f"Component not found: {component_id}")
    out_path = Path(str(path or "").strip())
    if not str(out_path):
        raise ValueError("Export path is empty")
    if out_path.suffix.lower() != ".json":
        out_path = out_path.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ = out_path.write_text(
        json.dumps(entry.record.content, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return out_path


def _metadata_tags(metadata: dict[str, object]) -> list[str]:
    raw_tags = metadata.get("tags")
    if raw_tags is None:
        return []
    return json_string_list_loads(raw_tags)


def _content_schema_version(content: JsonObject) -> str:
    raw_schema_version = content.get("schemaVersion")
    if raw_schema_version is None:
        return SESSION_SCHEMA_VERSION
    return str(raw_schema_version)


__all__ = [
    "list_component_entries",
    "component_entry",
    "upsert_component",
    "delete_component",
    "import_component_from_json",
    "export_component_to_json",
]
