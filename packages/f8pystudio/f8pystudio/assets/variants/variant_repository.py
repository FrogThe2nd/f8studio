from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from f8pysdk.codec import dump_json, validate_as

from f8pysdk.specs import F8VariantLibrary, F8VariantRecord

from .variant_catalog import (
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
from .variant_models import F8VariantEntry


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
    from .variant_models import F8VariantSourceKind

    saved = _service().upsert_local_entry(
        F8VariantEntry(
            record=record,
            source=F8VariantSourceKind.local,
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
    imported = validate_as(F8VariantLibrary, raw)
    return _service().import_local_library(imported, mode=mode)


def export_to_json(path: str) -> Path:
    out_path = Path(str(path or "").strip())
    if not str(out_path):
        raise ValueError("Export path is empty")
    if out_path.suffix.lower() != ".json":
        out_path = out_path.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lib = load_library()
    out_path.write_text(
        json.dumps(dump_json(lib, mode="json"), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return out_path


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
