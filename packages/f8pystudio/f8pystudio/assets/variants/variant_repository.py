from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from f8pysdk.codec import copy_model
from f8pysdk.codec import dump_json, validate_as

from f8pysdk.specs import F8VariantLibrary, F8VariantRecord

from .variant_catalog import (
    local_entry_from_record,
    VariantCatalogService,
    _records_name_conflict,
    ensure_unique_variant_name as _catalog_ensure_unique_variant_name,
    is_entry_usable,
    local_variants_file_path,
    normalize_variant_name,
    remote_cache_file_path,
    variants_file_path,
)
from .variant_events import emit_variants_changed
from .variant_models import F8VariantDraftOriginKind, F8VariantEntry, F8VariantSourceKind, F8VariantSyncState


def _service() -> VariantCatalogService:
    return VariantCatalogService()


def load_library() -> F8VariantLibrary:
    return _service().export_local_library()


def save_library(file_model: F8VariantLibrary) -> None:
    _service().import_local_library(file_model, mode="replace")


def list_entries_for_base(base_node_type: str, *, include_uninstalled: bool = False) -> list[F8VariantEntry]:
    return _service().list_entries_for_base(base_node_type, include_uninstalled=include_uninstalled)


def list_variants_for_base(base_node_type: str) -> list[F8VariantRecord]:
    return _service().list_records_for_base(base_node_type)


def list_variants_grouped_by_base(*, include_uninstalled: bool = False) -> dict[str, list[F8VariantRecord]]:
    grouped_records: dict[str, list[F8VariantRecord]] = {}
    for entry in _service().load_all_entries():
        if not include_uninstalled and not is_entry_usable(entry):
            continue
        base_node_type = str(entry.record.baseNodeType or "").strip()
        if not base_node_type:
            continue
        existing_records = grouped_records.get(base_node_type)
        if existing_records is None:
            grouped_records[base_node_type] = [entry.record]
        else:
            existing_records.append(entry.record)
    return grouped_records


def _local_records() -> list[F8VariantRecord]:
    return [entry.record for entry in _service()._local_provider.load_entries()]


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
    return _service().variant_exists(variant_id)


def variant_record(variant_id: str) -> F8VariantRecord | None:
    return _service().record(variant_id)


def variant_entry(variant_id: str, *, include_uninstalled: bool = True) -> F8VariantEntry | None:
    return _service().entry(variant_id, include_uninstalled=include_uninstalled)


def local_variant_entry_by_name(base_node_type: str, name: str) -> F8VariantEntry | None:
    normalized_base_node_type = str(base_node_type or "").strip()
    normalized_name = normalize_variant_name(name)
    if not normalized_base_node_type or not normalized_name:
        return None
    for entry in _service()._local_provider.load_entries():
        if str(entry.record.baseNodeType or "").strip() != normalized_base_node_type:
            continue
        if normalize_variant_name(entry.record.name) != normalized_name:
            continue
        return entry
    return None


def upsert_variant_entry(entry: F8VariantEntry) -> F8VariantEntry:
    return _service().upsert_local_entry(entry)


def upsert_variant(record: F8VariantRecord) -> F8VariantRecord:
    existing_local_entry = _service().entry(str(record.variantId), include_uninstalled=True)
    if existing_local_entry is not None and existing_local_entry.source == F8VariantSourceKind.local:
        saved = _service().upsert_local_entry(copy_model(existing_local_entry, update={"record": record}))
        return saved.record
    saved = _service().upsert_local_entry(
        F8VariantEntry(
            record=record,
            source=F8VariantSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8VariantDraftOriginKind.new,
        )
    )
    return saved.record


def delete_variant(variant_id: str) -> bool:
    return _service().delete_local_entry(variant_id)


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
    return _service().import_local_entries(entries, mode=mode)


def export_to_json(path: str) -> Path:
    out_path = Path(str(path or "").strip())
    if not str(out_path):
        raise ValueError("Export path is empty")
    if out_path.suffix.lower() != ".json":
        out_path = out_path.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in _service()._local_provider.load_entries() if entry.source == F8VariantSourceKind.local]
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
                "localVersionNumber": entry.localVersionNumber,
                "syncBaseRemoteRevision": entry.syncBaseRemoteRevision,
                "syncBaseRemoteVersionNumber": entry.syncBaseRemoteVersionNumber,
                "syncBaseLocalVersionNumber": entry.syncBaseLocalVersionNumber,
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
                syncState=F8VariantSyncState.local_only,
                installed=True,
                hasCachedContent=True,
                localVersionNumber=_optional_int(raw_entry.get("localVersionNumber")),
                syncBaseRemoteRevision=_optional_str(raw_entry.get("syncBaseRemoteRevision")),
                syncBaseRemoteVersionNumber=_optional_int(raw_entry.get("syncBaseRemoteVersionNumber")),
                syncBaseLocalVersionNumber=_optional_int(raw_entry.get("syncBaseLocalVersionNumber")),
                isLocalDraft=_optional_bool(raw_entry.get("isLocalDraft"), default=True),
                draftOriginKind=_optional_draft_origin_kind(raw_entry.get("draftOriginKind")),
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


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _optional_draft_origin_kind(value: object) -> F8VariantDraftOriginKind | None:
    raw_value = _optional_str(value)
    if raw_value is None:
        return None
    return F8VariantDraftOriginKind(raw_value)


__all__ = [
    "variants_file_path",
    "local_variants_file_path",
    "remote_cache_file_path",
    "load_library",
    "save_library",
    "list_entries_for_base",
    "list_variants_for_base",
    "list_variants_grouped_by_base",
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
