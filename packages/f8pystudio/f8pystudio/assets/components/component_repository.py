from __future__ import annotations

import json
from pathlib import Path

from f8pysdk.codec import copy_model
from f8pystudio.nodegraph.session_schema import extract_layout
from .component_drafts import ComponentDraftService, draft_as_catalog_entry
from .component_catalog import ComponentCatalogService
from .component_catalog import component_entry_is_installed
from .component_models import (
    F8ComponentDraftEntry,
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
)
from ..common import JsonObject, json_object_loads, json_string_list_loads


def _service() -> ComponentCatalogService:
    return ComponentCatalogService()


def _draft_service() -> ComponentDraftService:
    service = _service()
    return ComponentDraftService(db_path=service.db_path)


def list_component_entries(*, include_uninstalled: bool = False) -> list[F8ComponentEntry]:
    entries = _draft_service().list_catalog_entries() + _service().load_remote_entries()
    if include_uninstalled:
        return sorted(entries, key=_component_sort_key)
    return sorted([entry for entry in entries if component_entry_is_installed(entry)], key=_component_sort_key)


def component_entry(component_id: str, *, include_uninstalled: bool = True) -> F8ComponentEntry | None:
    normalized_component_id = str(component_id or "").strip()
    if not normalized_component_id:
        return None
    draft = _draft_service().draft(normalized_component_id)
    if draft is not None:
        return draft_as_catalog_entry(draft)
    entry = _service().remote_entry(normalized_component_id)
    if entry is None:
        return None
    if include_uninstalled or component_entry_is_installed(entry):
        return entry
    return None


def upsert_component(record: F8ComponentRecord) -> F8ComponentRecord:
    draft_service = _draft_service()
    existing_draft = draft_service.draft(str(record.componentId))
    if existing_draft is not None:
        saved = draft_service.save_draft(
            F8ComponentDraftEntry(
                draftId=existing_draft.draftId,
                record=record,
                originKind=existing_draft.originKind,
                publishTargetAssetId=existing_draft.publishTargetAssetId,
                publishBaseRemoteVersionNumber=existing_draft.publishBaseRemoteVersionNumber,
                createdAt=existing_draft.createdAt,
                updatedAt=existing_draft.updatedAt,
            )
        )
        return saved.record
    saved = draft_service.create_draft_from_record(
        record,
        origin_kind=F8ComponentDraftOriginKind.new,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id=str(record.componentId),
    )
    return saved.record


def delete_component(component_id: str) -> bool:
    return _draft_service().delete_draft(component_id)


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
    return str(content["schemaVersion"])


def _component_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
    return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))


__all__ = [
    "list_component_entries",
    "component_entry",
    "upsert_component",
    "delete_component",
    "import_component_from_json",
    "export_component_to_json",
]
