from __future__ import annotations

from pathlib import Path

from f8pysdk.codec import dump_json, validate_as
from f8pystudio.nodegraph.session_schema import extract_layout
from .component_drafts import ComponentDraftService, draft_as_catalog_entry
from .component_catalog import ComponentCatalogService
from .component_catalog import component_entry_is_installed
from .component_models import (
    F8ComponentDraftEntry,
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRecord,
)
from ..common import json_string_list_loads, new_asset_id
from ..common.asset_file_exchange import read_component_asset_file, write_component_asset_file


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
    payload = read_component_asset_file(path)
    _ = extract_layout(payload.record.content)
    meta = {} if metadata is None else dict(metadata)
    desired_name = str(meta.get("name") or payload.record.name or "").strip()
    record = validate_as(
        F8ComponentRecord,
        {
            **dump_json(payload.record, mode="json"),
            "componentId": str(meta.get("componentId") or new_asset_id()),
            "name": ensure_unique_component_name(desired_name),
            "description": str(meta.get("description") or payload.record.description or ""),
            "tags": _metadata_tags(meta) if "tags" in meta else list(payload.record.tags or []),
        },
    )
    return upsert_component(record)


def export_component_to_json(component_id: str, path: str) -> Path:
    entry = component_entry(component_id, include_uninstalled=True)
    if entry is None:
        raise FileNotFoundError(f"Component not found: {component_id}")
    return write_component_asset_file(
        path,
        record=entry.record,
        version_number=_entry_version_number(entry),
    )


def _metadata_tags(metadata: dict[str, object]) -> list[str]:
    raw_tags = metadata.get("tags")
    if raw_tags is None:
        return []
    return json_string_list_loads(raw_tags)


def ensure_unique_component_name(
    desired_name: str,
    *,
    exclude_component_id: str | None = None,
    existing_records: list[F8ComponentRecord] | None = None,
) -> str:
    base_name = normalize_component_name(desired_name) or "Imported Component"
    records = _local_component_records() if existing_records is None else list(existing_records)
    if not _component_name_conflict(records, name=base_name, exclude_component_id=exclude_component_id):
        return base_name
    suffix = 2
    while True:
        candidate = f"{base_name} ({suffix})"
        if not _component_name_conflict(records, name=candidate, exclude_component_id=exclude_component_id):
            return candidate
        suffix += 1


def normalize_component_name(name: str) -> str:
    return str(name or "").strip()


def _component_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
    return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))


def _entry_version_number(entry: F8ComponentEntry) -> int:
    if entry.remoteVersionNumber is not None and int(entry.remoteVersionNumber) > 0:
        return int(entry.remoteVersionNumber)
    return 1


def _local_component_records() -> list[F8ComponentRecord]:
    return [entry.record for entry in _draft_service().list_catalog_entries()]


def _component_name_conflict(
    records: list[F8ComponentRecord],
    *,
    name: str,
    exclude_component_id: str | None = None,
) -> bool:
    normalized_name = normalize_component_name(name)
    normalized_exclude_component_id = str(exclude_component_id or "").strip()
    if not normalized_name:
        return False
    for record in records:
        if normalized_exclude_component_id and str(record.componentId or "").strip() == normalized_exclude_component_id:
            continue
        if normalize_component_name(record.name) == normalized_name:
            return True
    return False


__all__ = [
    "list_component_entries",
    "component_entry",
    "upsert_component",
    "delete_component",
    "import_component_from_json",
    "export_component_to_json",
    "ensure_unique_component_name",
    "normalize_component_name",
]
