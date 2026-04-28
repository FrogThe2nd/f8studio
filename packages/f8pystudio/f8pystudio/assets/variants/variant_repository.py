from __future__ import annotations

from pathlib import Path

from f8pysdk.codec import copy_model

from f8pysdk.specs import F8VariantLibrary, F8VariantRecord

from .variant_drafts import VariantDraftService, draft_as_catalog_entry
from .variant_catalog import (
    entries_to_library,
    local_entry_from_record,
    VariantCatalogService,
    _entry_sort_key,
    _records_name_conflict,
    ensure_unique_variant_name as _catalog_ensure_unique_variant_name,
    is_entry_usable,
    local_variants_file_path,
    normalize_variant_name,
    remote_cache_file_path,
    variants_file_path,
)
from .variant_events import emit_variants_changed
from .variant_models import (
    F8VariantDraftEntry,
    F8VariantDraftOriginKind,
    F8VariantEntry,
    variant_now_iso,
)
from ..common import new_asset_id
from ..common.asset_file_exchange import read_variant_asset_file, write_variant_asset_file


def _service() -> VariantCatalogService:
    return VariantCatalogService()


def _draft_service() -> VariantDraftService:
    service = _service()
    return VariantDraftService(db_path=service.db_path)


def load_library() -> F8VariantLibrary:
    return entries_to_library(_draft_service().list_catalog_entries())


def save_library(file_model: F8VariantLibrary) -> None:
    existing_drafts = _draft_service().list_drafts()
    for draft in existing_drafts:
        _draft_service().delete_draft(draft.draftId)
    existing_records: list[F8VariantRecord] = []
    for library_entry in list(file_model.entries or []):
        normalized_name = ensure_unique_variant_name(
            str(library_entry.record.baseNodeType or ""),
            str(library_entry.record.name or ""),
            exclude_variant_id=str(library_entry.record.variantId),
            existing_records=existing_records,
        )
        saved_record = upsert_variant(
            copy_model(
                library_entry.record,
                update={"name": normalized_name},
            )
        )
        existing_records.append(saved_record)


def list_entries_for_base(base_node_type: str, *, include_uninstalled: bool = False) -> list[F8VariantEntry]:
    base = str(base_node_type or "").strip()
    if not base:
        return []
    draft_entries = [
        entry
        for entry in _draft_service().list_catalog_entries()
        if str(entry.record.baseNodeType or "").strip() == base
    ]
    remote_entries = [
        entry
        for entry in _service().load_remote_entries()
        if str(entry.record.baseNodeType or "").strip() == base
    ]
    entries = draft_entries + remote_entries
    if include_uninstalled:
        return sorted(entries, key=_entry_sort_key)
    return sorted([entry for entry in entries if is_entry_usable(entry)], key=_entry_sort_key)


def list_variants_for_base(base_node_type: str) -> list[F8VariantRecord]:
    base = str(base_node_type or "").strip()
    if not base:
        return []
    return [
        draft.record
        for draft in _draft_service().list_drafts()
        if str(draft.record.baseNodeType or "").strip() == base
    ]


def list_variants_grouped_by_base(*, include_uninstalled: bool = False) -> dict[str, list[F8VariantRecord]]:
    return {
        base_node_type: [entry.record for entry in entries]
        for base_node_type, entries in list_variant_entries_grouped_by_base(include_uninstalled=include_uninstalled).items()
    }


def list_variant_entries_grouped_by_base(*, include_uninstalled: bool = False) -> dict[str, list[F8VariantEntry]]:
    merged_entries: dict[str, F8VariantEntry] = {}
    for source_entries in [_service().load_remote_entries(), _draft_service().list_catalog_entries()]:
        for entry in source_entries:
            variant_id = str(entry.record.variantId or "").strip()
            if not variant_id:
                continue
            merged_entries[variant_id] = entry
    grouped_entries: dict[str, list[F8VariantEntry]] = {}
    for entry in merged_entries.values():
        if not include_uninstalled and not is_entry_usable(entry):
            continue
        base_node_type = str(entry.record.baseNodeType or "").strip()
        if not base_node_type:
            continue
        existing_entries = grouped_entries.get(base_node_type)
        if existing_entries is None:
            grouped_entries[base_node_type] = [entry]
        else:
            existing_entries.append(entry)
    return grouped_entries


def _local_records() -> list[F8VariantRecord]:
    return [draft.record for draft in _draft_service().list_drafts()]


def is_variant_name_conflict(base_node_type: str, name: str, *, exclude_variant_id: str | None = None) -> bool:
    return _records_name_conflict(
        _local_records(),
        base_node_type=base_node_type,
        name=name,
        exclude_variant_id=exclude_variant_id,
    )


def ensure_unique_variant_name(
    base_node_type: str,
    desired_name: str,
    *,
    exclude_variant_id: str | None = None,
    existing_records: list[F8VariantRecord] | None = None,
) -> str:
    records = _local_records() if existing_records is None else list(existing_records)
    return _catalog_ensure_unique_variant_name(
        base_node_type,
        desired_name,
        exclude_variant_id=exclude_variant_id,
        existing_records=records,
    )


def variant_exists(variant_id: str) -> bool:
    return variant_entry(variant_id, include_uninstalled=True) is not None


def variant_record(variant_id: str) -> F8VariantRecord | None:
    entry = variant_entry(variant_id, include_uninstalled=True)
    return None if entry is None else entry.record


def variant_entry(variant_id: str, *, include_uninstalled: bool = True) -> F8VariantEntry | None:
    normalized_variant_id = str(variant_id or "").strip()
    if not normalized_variant_id:
        return None
    draft = _draft_service().draft(normalized_variant_id)
    if draft is not None:
        return draft_as_catalog_entry(draft)
    entry = _service().remote_entry(normalized_variant_id)
    if entry is None:
        return None
    if include_uninstalled or is_entry_usable(entry):
        return entry
    return None


def local_variant_entry_by_name(base_node_type: str, name: str) -> F8VariantEntry | None:
    normalized_base_node_type = str(base_node_type or "").strip()
    normalized_name = normalize_variant_name(name)
    if not normalized_base_node_type or not normalized_name:
        return None
    for entry in _draft_service().list_catalog_entries():
        if str(entry.record.baseNodeType or "").strip() != normalized_base_node_type:
            continue
        if normalize_variant_name(entry.record.name) != normalized_name:
            continue
        return entry
    return None


def upsert_variant_entry(entry: F8VariantEntry) -> F8VariantEntry:
    _validate_unique_variant_name(entry.record, exclude_variant_id=str(entry.record.variantId))
    draft_service = _draft_service()
    draft = draft_service.draft(str(entry.record.variantId))
    if draft is None:
        saved = draft_service.create_draft_from_record(
            entry.record,
            origin_kind=entry.draftOriginKind or F8VariantDraftOriginKind.new,
            publish_target_asset_id=entry.draftOriginAssetId,
            publish_base_remote_version_number=entry.draftOriginVersionNumber,
            draft_id=str(entry.record.variantId),
        )
    else:
        saved = draft_service.save_draft(
            F8VariantDraftEntry(
                draftId=draft.draftId,
                record=entry.record,
                originKind=entry.draftOriginKind or draft.originKind,
                publishTargetAssetId=entry.draftOriginAssetId or draft.publishTargetAssetId,
                publishBaseRemoteVersionNumber=entry.draftOriginVersionNumber or draft.publishBaseRemoteVersionNumber,
                createdAt=draft.createdAt,
                updatedAt=draft.updatedAt,
            )
        )
    emit_variants_changed()
    return draft_as_catalog_entry(saved)


def upsert_variant(record: F8VariantRecord) -> F8VariantRecord:
    _validate_unique_variant_name(record, exclude_variant_id=str(record.variantId))
    draft_service = _draft_service()
    existing_draft = draft_service.draft(str(record.variantId))
    if existing_draft is not None:
        saved = draft_service.save_draft(
            F8VariantDraftEntry(
                draftId=existing_draft.draftId,
                record=record,
                originKind=existing_draft.originKind,
                publishTargetAssetId=existing_draft.publishTargetAssetId,
                publishBaseRemoteVersionNumber=existing_draft.publishBaseRemoteVersionNumber,
                createdAt=existing_draft.createdAt,
                updatedAt=existing_draft.updatedAt,
            )
        )
        emit_variants_changed()
        return saved.record
    saved = draft_service.create_draft_from_record(
        record,
        origin_kind=F8VariantDraftOriginKind.new,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id=str(record.variantId),
    )
    emit_variants_changed()
    return saved.record


def delete_variant(variant_id: str) -> bool:
    deleted = _draft_service().delete_draft(variant_id)
    if deleted:
        emit_variants_changed()
    return deleted


def import_from_json(path: str) -> F8VariantRecord:
    payload = read_variant_asset_file(path)
    imported_record = copy_model(
        payload.record,
        update={
            "variantId": new_asset_id(),
            "name": ensure_unique_variant_name(
                str(payload.record.baseNodeType or ""),
                str(payload.record.name or ""),
            ),
            "updatedAt": variant_now_iso(),
        },
    )
    return upsert_variant(imported_record)


def export_to_json(variant_id: str, path: str) -> Path:
    entry = variant_entry(variant_id, include_uninstalled=True)
    if entry is None:
        raise FileNotFoundError(f"Variant not found: {variant_id}")
    return write_variant_asset_file(
        path,
        record=entry.record,
        version_number=_entry_version_number(entry),
    )


def _entry_version_number(entry: F8VariantEntry) -> int:
    if entry.remoteVersionNumber is not None and int(entry.remoteVersionNumber) > 0:
        return int(entry.remoteVersionNumber)
    return 1


def _validate_unique_variant_name(record: F8VariantRecord, *, exclude_variant_id: str | None) -> None:
    if is_variant_name_conflict(
        str(record.baseNodeType or ""),
        str(record.name or ""),
        exclude_variant_id=exclude_variant_id,
    ):
        raise ValueError(f"Variant name '{normalize_variant_name(record.name)}' already exists.")


__all__ = [
    "variants_file_path",
    "local_variants_file_path",
    "remote_cache_file_path",
    "load_library",
    "save_library",
    "list_entries_for_base",
    "list_variants_for_base",
    "list_variants_grouped_by_base",
    "list_variant_entries_grouped_by_base",
    "normalize_variant_name",
    "is_variant_name_conflict",
    "ensure_unique_variant_name",
    "variant_exists",
    "variant_record",
    "variant_entry",
    "local_variant_entry_by_name",
    "upsert_variant_entry",
    "upsert_variant",
    "delete_variant",
    "import_from_json",
    "export_to_json",
    "emit_variants_changed",
]
