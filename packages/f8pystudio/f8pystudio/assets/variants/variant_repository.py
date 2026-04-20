from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from f8pysdk.codec import copy_model
from f8pysdk.codec import dump_json, validate_as

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
    F8VariantSourceKind,
)


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
            publish_base_remote_revision=entry.draftOriginRevision,
            draft_id=str(entry.record.variantId),
        )
    else:
        saved = draft_service.save_draft(
            F8VariantDraftEntry(
                draftId=draft.draftId,
                record=entry.record,
                originKind=entry.draftOriginKind or draft.originKind,
                publishTargetAssetId=entry.draftOriginAssetId or draft.publishTargetAssetId,
                publishBaseRemoteRevision=entry.draftOriginRevision or draft.publishBaseRemoteRevision,
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
                publishBaseRemoteRevision=existing_draft.publishBaseRemoteRevision,
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
        publish_base_remote_revision=None,
        draft_id=str(record.variantId),
    )
    emit_variants_changed()
    return saved.record


def delete_variant(variant_id: str) -> bool:
    deleted = _draft_service().delete_draft(variant_id)
    if deleted:
        emit_variants_changed()
    return deleted


def import_from_json(path: str, mode: Literal["merge", "replace"] = "merge") -> F8VariantLibrary:
    in_path = Path(str(path or "").strip())
    if not in_path.is_file():
        raise FileNotFoundError(f"Variants file not found: {in_path}")
    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Variant library payload must be an object.")
    schema_version = str(raw.get("schemaVersion") or "").strip()
    if schema_version != "f8variantlib/1":
        raise ValueError(f"Unsupported variant library schemaVersion: {schema_version!r}")
    entries = _variant_entries_from_library_payload(raw)
    draft_service = _draft_service()
    if mode == "replace":
        for draft in draft_service.list_drafts():
            draft_service.delete_draft(draft.draftId)
        existing_records: list[F8VariantRecord] = []
    else:
        existing_records = [draft.record for draft in draft_service.list_drafts()]
    for entry in entries:
        normalized_name = ensure_unique_variant_name(
            str(entry.record.baseNodeType or ""),
            str(entry.record.name or ""),
            exclude_variant_id=str(entry.record.variantId),
            existing_records=existing_records,
        )
        saved_entry = upsert_variant_entry(
            copy_model(
                entry,
                update={
                    "record": copy_model(entry.record, update={"name": normalized_name}),
                },
            )
        )
        existing_records.append(saved_entry.record)
    return load_library()


def export_to_json(path: str) -> Path:
    out_path = Path(str(path or "").strip())
    if not str(out_path):
        raise ValueError("Export path is empty")
    if out_path.suffix.lower() != ".json":
        out_path = out_path.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries = _draft_service().list_catalog_entries()
    payload = _variant_library_payload(entries)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return out_path


def _variant_library_payload(entries: list[F8VariantEntry]) -> dict[str, object]:
    return {
        "schemaVersion": "f8variantlib/1",
        "entries": [
            {
                "record": dump_json(entry.record, mode="json"),
                "isLocalDraft": entry.isLocalDraft,
                "draftOriginKind": None if entry.draftOriginKind is None else entry.draftOriginKind.value,
                "draftOriginAssetId": entry.draftOriginAssetId,
                "draftOriginRevision": entry.draftOriginRevision,
            }
            for entry in entries
        ],
    }


def _variant_entries_from_library_payload(payload: dict[str, object]) -> list[F8VariantEntry]:
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("Variant library missing `entries` array.")
    entries: list[F8VariantEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("Variant library entry must be an object.")
        record = validate_as(F8VariantRecord, raw_entry.get("record"))
        entry = local_entry_from_record(record)
        entries.append(
            F8VariantEntry(
                record=entry.record,
                source=F8VariantSourceKind.local,
                installed=True,
                hasCachedContent=True,
                isLocalDraft=_required_bool(raw_entry, "isLocalDraft"),
                draftOriginKind=_required_draft_origin_kind(raw_entry, "draftOriginKind"),
                draftOriginAssetId=_optional_str(raw_entry.get("draftOriginAssetId")),
                draftOriginRevision=_optional_str(raw_entry.get("draftOriginRevision")),
            )
        )
    return entries


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_bool(payload: dict[str, object], key: str) -> bool:
    if key not in payload:
        raise ValueError(f"Variant library entry missing `{key}`.")
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"Variant library entry `{key}` must be a boolean.")
    return value


def _required_draft_origin_kind(payload: dict[str, object], key: str) -> F8VariantDraftOriginKind | None:
    if key not in payload:
        raise ValueError(f"Variant library entry missing `{key}`.")
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Variant library entry `{key}` must be a non-empty string or null.")
    return F8VariantDraftOriginKind(value)


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
