from __future__ import annotations

from collections.abc import Iterable
import json
import logging
from pathlib import Path
from typing import Protocol

from f8pysdk.msgspec_codec import copy_model, dump_json, validate_as

from f8pysdk import F8VariantLibrary, F8VariantRecord

from .variant_events import emit_variants_changed
from .variant_models import (
    F8VariantCatalogSnapshot,
    F8VariantEntry,
    F8VariantSourceKind,
    F8VariantSyncState,
)

logger = logging.getLogger(__name__)


class VariantSourceProvider(Protocol):
    def load_entries(self) -> list[F8VariantEntry]: ...


class LocalVariantProvider:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return local_variants_file_path() if self._path is None else self._path

    def load_entries(self) -> list[F8VariantEntry]:
        snapshot = load_catalog_snapshot(self.path, migrate_legacy=True)
        out: list[F8VariantEntry] = []
        for entry in snapshot.entries:
            if entry.source == F8VariantSourceKind.local:
                out.append(entry)
        return out

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        save_catalog_snapshot(
            self.path,
            F8VariantCatalogSnapshot(entries=[entry for entry in entries if entry.source == F8VariantSourceKind.local]),
        )


class RemoteCacheProvider:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return remote_cache_file_path() if self._path is None else self._path

    def load_entries(self) -> list[F8VariantEntry]:
        snapshot = load_catalog_snapshot(self.path)
        out: list[F8VariantEntry] = []
        for entry in snapshot.entries:
            if entry.source in {
                F8VariantSourceKind.remote_official,
                F8VariantSourceKind.remote_public,
                F8VariantSourceKind.remote_private,
            }:
                out.append(entry)
        return out

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        save_catalog_snapshot(
            self.path,
            F8VariantCatalogSnapshot(
                entries=[
                    entry
                    for entry in entries
                    if entry.source
                    in {
                        F8VariantSourceKind.remote_official,
                        F8VariantSourceKind.remote_public,
                        F8VariantSourceKind.remote_private,
                    }
                ]
            ),
        )


class VariantCatalogService:
    def __init__(
        self,
        *,
        local_provider: LocalVariantProvider | None = None,
        remote_provider: RemoteCacheProvider | None = None,
    ) -> None:
        self._local_provider = LocalVariantProvider() if local_provider is None else local_provider
        self._remote_provider = RemoteCacheProvider() if remote_provider is None else remote_provider

    def load_all_entries(self) -> list[F8VariantEntry]:
        merged: dict[str, F8VariantEntry] = {}
        order = [
            self._remote_provider.load_entries(),
            self._local_provider.load_entries(),
        ]
        for source_entries in order:
            for entry in source_entries:
                variant_id = str(entry.record.variantId or "").strip()
                if not variant_id:
                    continue
                merged[variant_id] = entry
        return sorted(merged.values(), key=_entry_sort_key)

    def list_entries_for_base(self, base_node_type: str, *, include_uninstalled: bool = False) -> list[F8VariantEntry]:
        base = str(base_node_type or "").strip()
        if not base:
            return []
        out: list[F8VariantEntry] = []
        for entry in self.load_all_entries():
            if str(entry.record.baseNodeType or "").strip() != base:
                continue
            if include_uninstalled or is_entry_usable(entry):
                out.append(entry)
        return out

    def list_records_for_base(self, base_node_type: str, *, include_uninstalled: bool = False) -> list[F8VariantRecord]:
        return [entry.record for entry in self.list_entries_for_base(base_node_type, include_uninstalled=include_uninstalled)]

    def entry(self, variant_id: str, *, include_uninstalled: bool = True) -> F8VariantEntry | None:
        variant_id = str(variant_id or "").strip()
        if not variant_id:
            return None
        for entry in self.load_all_entries():
            if str(entry.record.variantId or "").strip() != variant_id:
                continue
            if include_uninstalled or is_entry_usable(entry):
                return entry
        return None

    def record(self, variant_id: str, *, include_uninstalled: bool = False) -> F8VariantRecord | None:
        entry = self.entry(variant_id, include_uninstalled=include_uninstalled)
        return None if entry is None else entry.record

    def variant_exists(self, variant_id: str) -> bool:
        return self.record(variant_id) is not None

    def upsert_local_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        if entry.source != F8VariantSourceKind.local:
            entry = copy_model(entry, update={"source": F8VariantSourceKind.local})
        local_entries = self._local_provider.load_entries()
        _validate_unique_name(local_entries, entry.record, exclude_variant_id=str(entry.record.variantId))
        replaced = False
        out: list[F8VariantEntry] = []
        target_id = str(entry.record.variantId)
        for current in local_entries:
            if str(current.record.variantId) == target_id:
                out.append(entry)
                replaced = True
            else:
                out.append(current)
        if not replaced:
            out.append(entry)
        self._local_provider.save_entries(out)
        emit_variants_changed()
        return entry

    def delete_local_entry(self, variant_id: str) -> bool:
        variant_id = str(variant_id or "").strip()
        if not variant_id:
            return False
        local_entries = self._local_provider.load_entries()
        out = [entry for entry in local_entries if str(entry.record.variantId) != variant_id]
        if len(out) == len(local_entries):
            return False
        self._local_provider.save_entries(out)
        emit_variants_changed()
        return True

    def replace_remote_entries(self, entries: list[F8VariantEntry]) -> None:
        self._remote_provider.save_entries(entries)
        emit_variants_changed()

    def install_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        installed_entry = copy_model(entry, update={"installed": True, "downloadedAt": F8VariantRecord.now_iso()})
        remote_entries = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        found = False
        for current in remote_entries:
            if str(current.record.variantId) == str(installed_entry.record.variantId):
                out.append(installed_entry)
                found = True
            else:
                out.append(current)
        if not found:
            out.append(installed_entry)
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return installed_entry

    def mark_conflict(self, variant_id: str, *, remote_revision: str | None) -> F8VariantEntry | None:
        remote_entries = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        target: F8VariantEntry | None = None
        for current in remote_entries:
            if str(current.record.variantId) == str(variant_id):
                target = copy_model(
                    current,
                    update={"syncState": F8VariantSyncState.conflict, "remoteRevision": remote_revision},
                )
                out.append(target)
            else:
                out.append(current)
        if target is None:
            return None
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return target

    def export_local_library(self) -> F8VariantLibrary:
        return entries_to_library(self._local_provider.load_entries())

    def import_local_library(self, library: F8VariantLibrary, *, mode: str) -> F8VariantLibrary:
        current_entries = [] if mode == "replace" else self._local_provider.load_entries()
        current_records = [entry.record for entry in current_entries]
        imported_entries = list(current_entries)
        for variant in list(library.variants or []):
            variant_id = str(variant.variantId or "").strip()
            imported_entries = [entry for entry in imported_entries if str(entry.record.variantId or "").strip() != variant_id]
            unique_name = ensure_unique_variant_name(
                variant.baseNodeType,
                variant.name,
                existing_records=current_records,
            )
            if unique_name != variant.name:
                variant = copy_model(variant, update={"name": unique_name})
            entry = local_entry_from_record(variant)
            imported_entries.append(entry)
            current_records = [existing.record for existing in imported_entries]
        self._local_provider.save_entries(imported_entries)
        emit_variants_changed()
        return entries_to_library(imported_entries)


def catalog_dir() -> Path:
    return Path.home() / ".f8" / "studio"


def variants_file_path() -> Path:
    return local_variants_file_path()


def legacy_variants_file_path() -> Path:
    return catalog_dir() / "nodeVariants.json"


def local_variants_file_path() -> Path:
    return catalog_dir() / "nodeVariants.local.json"


def remote_cache_file_path() -> Path:
    return catalog_dir() / "nodeVariants.remote-cache.json"


def normalize_variant_name(name: str) -> str:
    return str(name or "").strip()


def _records_name_conflict(
    records: Iterable[F8VariantRecord],
    *,
    base_node_type: str,
    name: str,
    exclude_variant_id: str | None = None,
) -> bool:
    base = str(base_node_type or "").strip()
    target = normalize_variant_name(name)
    exclude_id = str(exclude_variant_id or "").strip()
    if not base or not target:
        return False
    for variant in records:
        if str(variant.baseNodeType or "").strip() != base:
            continue
        if exclude_id and str(variant.variantId or "").strip() == exclude_id:
            continue
        if normalize_variant_name(variant.name) == target:
            return True
    return False


def is_variant_name_conflict(base_node_type: str, name: str, *, exclude_variant_id: str | None = None) -> bool:
    service = VariantCatalogService()
    local_records = [entry.record for entry in service._local_provider.load_entries()]
    return _records_name_conflict(
        local_records,
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
    base_name = normalize_variant_name(desired_name) or "Variant"
    records = list(existing_records) if existing_records is not None else []
    if not records:
        records = [entry.record for entry in VariantCatalogService()._local_provider.load_entries()]
    if not _records_name_conflict(
        records,
        base_node_type=base_node_type,
        name=base_name,
        exclude_variant_id=exclude_variant_id,
    ):
        return base_name
    suffix = 2
    while True:
        candidate = f"{base_name} ({suffix})"
        if not _records_name_conflict(
            records,
            base_node_type=base_node_type,
            name=candidate,
            exclude_variant_id=exclude_variant_id,
        ):
            return candidate
        suffix += 1


def local_entry_from_record(record: F8VariantRecord) -> F8VariantEntry:
    return F8VariantEntry(record=record, source=F8VariantSourceKind.local, syncState=F8VariantSyncState.local_only)


def entries_to_library(entries: list[F8VariantEntry]) -> F8VariantLibrary:
    return F8VariantLibrary(variants=[entry.record for entry in entries if entry.source == F8VariantSourceKind.local])


def load_catalog_snapshot(path: Path, *, migrate_legacy: bool = False) -> F8VariantCatalogSnapshot:
    if migrate_legacy and not path.is_file():
        migrate_legacy_variants_if_needed(target_path=path)
    return _read_catalog_snapshot(path)


def _read_catalog_snapshot(path: Path) -> F8VariantCatalogSnapshot:
    if not path.is_file():
        return F8VariantCatalogSnapshot()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and str(raw.get("schemaVersion") or "").strip() == "f8variantlib/1":
            library = validate_as(F8VariantLibrary, raw)
            return F8VariantCatalogSnapshot(entries=[local_entry_from_record(record) for record in list(library.variants or [])])
        return validate_as(F8VariantCatalogSnapshot, raw)
    except Exception:
        logger.exception("Failed to load variant catalog from %s", path)
        return F8VariantCatalogSnapshot()


def save_catalog_snapshot(path: Path, snapshot: F8VariantCatalogSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = dump_json(snapshot, mode="json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def migrate_legacy_variants_if_needed(*, target_path: Path | None = None) -> None:
    legacy_path = legacy_variants_file_path()
    destination = local_variants_file_path() if target_path is None else target_path
    if destination.is_file() or not legacy_path.is_file():
        return
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        library = validate_as(F8VariantLibrary, raw)
        snapshot = F8VariantCatalogSnapshot(entries=[local_entry_from_record(record) for record in list(library.variants or [])])
        save_catalog_snapshot(destination, snapshot)
        backup_path = legacy_path.with_suffix(legacy_path.suffix + ".bak")
        if not backup_path.exists():
            legacy_path.replace(backup_path)
        else:
            legacy_path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to migrate legacy variants file from %s", legacy_path)


def is_entry_usable(entry: F8VariantEntry) -> bool:
    if entry.source in {F8VariantSourceKind.local, F8VariantSourceKind.remote_private}:
        return True
    return bool(entry.installed)


def _entry_sort_key(entry: F8VariantEntry) -> tuple[str, str, str]:
    record = entry.record
    return (
        str(record.baseNodeType or "").lower(),
        str(record.name or "").lower(),
        str(record.variantId or ""),
    )


def _validate_unique_name(entries: list[F8VariantEntry], record: F8VariantRecord, *, exclude_variant_id: str | None) -> None:
    records = [entry.record for entry in entries]
    if _records_name_conflict(
        records,
        base_node_type=record.baseNodeType,
        name=record.name,
        exclude_variant_id=exclude_variant_id,
    ):
        normalized = normalize_variant_name(record.name)
        raise ValueError(
            f'Variant name "{normalized}" already exists for base node type "{record.baseNodeType}".'
        )
