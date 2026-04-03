from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import msgspec
import zlib
from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection as SqlAlchemyConnection
from f8pysdk.msgspec_codec import copy_model

from f8pysdk import F8JsonValue, F8VariantKind, F8VariantLibrary, F8VariantRecord

from ..db import AssetsDatabase, variant_heads_local_table, variant_remote_cache_table
from ..common import (
    JsonObject,
    json_object_loads,
    json_string_list_loads,
    stable_json_dumps,
)
from ..common.remote_cache_common import (
    RemoteCacheMetadata,
    remote_cache_metadata_from_fields,
)
from .variant_events import emit_variants_changed
from .variant_models import (
    F8VariantEntry,
    F8VariantSourceKind,
    F8VariantSyncState,
    F8VariantVisibility,
    variant_now_iso,
)


class VariantSourceProvider(Protocol):
    def load_entries(self) -> list[F8VariantEntry]: ...


class LocalVariantProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db: AssetsDatabase
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def load_entries(self) -> list[F8VariantEntry]:
        statement = (
            select(
                variant_heads_local_table.c.variant_id,
                variant_heads_local_table.c.content,
            )
            .order_by(func.lower(variant_heads_local_table.c.name), variant_heads_local_table.c.variant_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_variant_entry_from_local_row(row) for row in rows]

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        with self._db.begin_sqla() as conn:
            _ = conn.execute(delete(variant_heads_local_table))
            for entry in entries:
                if entry.source != F8VariantSourceKind.local:
                    continue
                _insert_local_variant_entry(conn, entry)


class RemoteCacheProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db: AssetsDatabase
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def load_entries(self) -> list[F8VariantEntry]:
        statement = (
            select(
                variant_remote_cache_table.c.variant_id,
                variant_remote_cache_table.c.content,
                variant_remote_cache_table.c.source,
                variant_remote_cache_table.c.visibility,
                variant_remote_cache_table.c.owner_user_id,
                variant_remote_cache_table.c.owner_display_name,
                variant_remote_cache_table.c.library_slug,
                variant_remote_cache_table.c.remote_revision,
                variant_remote_cache_table.c.sync_state,
                variant_remote_cache_table.c.downloaded_at,
                variant_remote_cache_table.c.installed,
                variant_remote_cache_table.c.subscribed,
            )
            .order_by(variant_remote_cache_table.c.variant_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_variant_entry_from_remote_row(row) for row in rows]

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        with self._db.begin_sqla() as conn:
            _ = conn.execute(delete(variant_remote_cache_table))
            for entry in entries:
                if entry.source not in {
                    F8VariantSourceKind.remote_official,
                    F8VariantSourceKind.remote_public,
                    F8VariantSourceKind.remote_private,
                }:
                    continue
                _insert_remote_variant_entry(conn, entry)


class VariantCatalogService:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        local_provider: LocalVariantProvider | None = None,
        remote_provider: RemoteCacheProvider | None = None,
    ) -> None:
        self._local_provider: LocalVariantProvider
        self._local_provider = LocalVariantProvider(db_path) if local_provider is None else local_provider
        self._remote_provider: RemoteCacheProvider
        self._remote_provider = RemoteCacheProvider(db_path) if remote_provider is None else remote_provider

    def load_all_entries(self) -> list[F8VariantEntry]:
        merged: dict[str, F8VariantEntry] = {}
        for source_entries in [self._remote_provider.load_entries(), self._local_provider.load_entries()]:
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
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        for entry in self.load_all_entries():
            if str(entry.record.variantId or "").strip() != normalized_variant_id:
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
        local_entry = entry if entry.source == F8VariantSourceKind.local else copy_model(entry, update={"source": F8VariantSourceKind.local})
        local_entries = self._local_provider.load_entries()
        _validate_unique_name(local_entries, local_entry.record, exclude_variant_id=str(local_entry.record.variantId))
        out: list[F8VariantEntry] = []
        replaced = False
        target_variant_id = str(local_entry.record.variantId)
        for current in local_entries:
            if str(current.record.variantId) == target_variant_id:
                out.append(local_entry)
                replaced = True
            else:
                out.append(current)
        if not replaced:
            out.append(local_entry)
        self._local_provider.save_entries(out)
        emit_variants_changed()
        return local_entry

    def delete_local_entry(self, variant_id: str) -> bool:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return False
        local_entries = self._local_provider.load_entries()
        out = [entry for entry in local_entries if str(entry.record.variantId) != normalized_variant_id]
        if len(out) == len(local_entries):
            return False
        self._local_provider.save_entries(out)
        emit_variants_changed()
        return True

    def replace_remote_entries(self, entries: list[F8VariantEntry]) -> None:
        self._remote_provider.save_entries(entries)
        emit_variants_changed()

    def load_remote_entries(self) -> list[F8VariantEntry]:
        return self._remote_provider.load_entries()

    def install_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        installed_entry = copy_model(entry, update={"installed": True, "downloadedAt": variant_now_iso()})
        current = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        found = False
        for current_entry in current:
            if str(current_entry.record.variantId) == str(installed_entry.record.variantId):
                out.append(installed_entry)
                found = True
            else:
                out.append(current_entry)
        if not found:
            out.append(installed_entry)
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return installed_entry

    def mark_conflict(self, variant_id: str, *, remote_revision: str | None) -> F8VariantEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        target: F8VariantEntry | None = None
        for entry in current:
            if str(entry.record.variantId) == str(variant_id):
                target = copy_model(entry, update={"syncState": F8VariantSyncState.conflict, "remoteRevision": remote_revision})
                out.append(target)
            else:
                out.append(entry)
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
        library_variants = [] if isinstance(library.variants, msgspec.UnsetType) else list(library.variants or [])
        for variant in library_variants:
            variant_id = str(variant.variantId or "").strip()
            imported_entries = [entry for entry in imported_entries if str(entry.record.variantId or "").strip() != variant_id]
            unique_name = ensure_unique_variant_name(
                variant.baseNodeType,
                variant.name,
                existing_records=current_records,
            )
            if unique_name != variant.name:
                variant = copy_model(variant, update={"name": unique_name})
            imported_entries.append(local_entry_from_record(variant))
            current_records = [entry.record for entry in imported_entries]
        self._local_provider.save_entries(imported_entries)
        emit_variants_changed()
        return entries_to_library(imported_entries)


# Kept for JSON import/export convenience in the manager. These are no longer
# the runtime storage locations for variants.
def catalog_dir() -> Path:
    return Path.home() / ".f8" / "studio"


def variants_file_path() -> Path:
    return catalog_dir() / "nodeVariants.json"


def local_variants_file_path() -> Path:
    return variants_file_path()


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
    normalized_exclude_variant_id = str(exclude_variant_id or "").strip()
    if not base or not target:
        return False
    for variant in records:
        if str(variant.baseNodeType or "").strip() != base:
            continue
        if normalized_exclude_variant_id and str(variant.variantId or "").strip() == normalized_exclude_variant_id:
            continue
        if normalize_variant_name(variant.name) == target:
            return True
    return False


def is_variant_name_conflict(base_node_type: str, name: str, *, exclude_variant_id: str | None = None) -> bool:
    local_records = _local_variant_records()
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
    records = list(existing_records) if existing_records is not None else _local_variant_records()
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
        raise ValueError(f'Variant name "{normalized}" already exists for base node type "{record.baseNodeType}".')


def _variant_entry_from_local_row(row: object) -> F8VariantEntry:
    row_mapping = _row_mapping(row)
    record_payload = json_object_loads(_decompress_content(row_mapping.get("content")))
    return F8VariantEntry(
        record=_variant_record_from_payload(record_payload),
        source=F8VariantSourceKind.local,
        syncState=F8VariantSyncState.local_only,
        installed=True,
    )


def _variant_entry_from_remote_row(row: object) -> F8VariantEntry:
    row_mapping = _row_mapping(row)
    record_payload = json_object_loads(_decompress_content(row_mapping.get("content")))
    metadata = RemoteCacheMetadata.from_row(row_mapping)
    visibility = None if metadata.visibility is None else F8VariantVisibility(metadata.visibility)
    return F8VariantEntry(
        record=_variant_record_from_payload(record_payload),
        source=F8VariantSourceKind(metadata.source),
        visibility=visibility,
        ownerUserId=metadata.owner_user_id,
        ownerDisplayName=metadata.owner_display_name,
        librarySlug=metadata.library_slug,
        remoteRevision=metadata.remote_revision,
        syncState=F8VariantSyncState(metadata.sync_state),
        downloadedAt=metadata.downloaded_at,
        installed=metadata.installed,
        subscribed=metadata.subscribed,
    )


def _insert_local_variant_entry(conn: SqlAlchemyConnection, entry: F8VariantEntry) -> None:
    record = entry.record
    operator_class = None if record.operatorClass is None or isinstance(record.operatorClass, msgspec.UnsetType) else str(record.operatorClass)
    tags = [] if isinstance(record.tags, msgspec.UnsetType) else list(record.tags or [])
    _ = conn.execute(
        insert(variant_heads_local_table).values(
            variant_id=str(record.variantId),
            name=str(record.name),
            description=str(record.description),
            tags_json=stable_json_dumps(tags),
            kind=str(record.kind.value),
            base_node_type=str(record.baseNodeType),
            service_class=str(record.serviceClass),
            operator_class=operator_class,
            content=_compress_content(stable_json_dumps(_variant_record_payload(record))),
            created_at=str(record.createdAt),
            updated_at=str(record.updatedAt),
        )
    )


def _insert_remote_variant_entry(conn: SqlAlchemyConnection, entry: F8VariantEntry) -> None:
    metadata = remote_cache_metadata_from_fields(
        source=str(entry.source.value),
        visibility=None if entry.visibility is None else str(entry.visibility.value),
        owner_user_id=entry.ownerUserId,
        owner_display_name=entry.ownerDisplayName,
        library_slug=entry.librarySlug,
        remote_revision=entry.remoteRevision,
        sync_state=str(entry.syncState.value),
        downloaded_at=entry.downloadedAt,
        installed=entry.installed,
        subscribed=entry.subscribed,
    )
    _ = conn.execute(
        insert(variant_remote_cache_table).values(
            variant_id=str(entry.record.variantId),
            source=metadata.source,
            visibility=metadata.visibility,
            owner_user_id=metadata.owner_user_id,
            owner_display_name=metadata.owner_display_name,
            library_slug=metadata.library_slug,
            remote_revision=metadata.remote_revision,
            sync_state=metadata.sync_state,
            downloaded_at=metadata.downloaded_at,
            installed=1 if metadata.installed else 0,
            subscribed=1 if metadata.subscribed else 0,
            content=_compress_content(stable_json_dumps(_variant_record_payload(entry.record))),
            updated_at=variant_now_iso(),
        )
    )


def _compress_content(json_str: str) -> bytes:
    return zlib.compress(json_str.encode("utf-8"), level=6, wbits=31)


def _decompress_content(data: bytes | None) -> str:
    if data is None:
        return "{}"
    try:
        return zlib.decompress(data, wbits=31).decode("utf-8")
    except Exception:
        # Fallback for unexpected corruption or if somehow a string leaked in
        if isinstance(data, str):
            return data
        return data.decode("utf-8", errors="replace")


def _row_mapping(row: object) -> Mapping[object, object]:
    if not isinstance(row, Mapping):
        raise TypeError("Expected mapping row for variant entry.")
    return cast(Mapping[object, object], row)


def _variant_record_from_payload(payload: JsonObject) -> F8VariantRecord:
    operator_class: str | None | msgspec.UnsetType
    if "operatorClass" in payload:
        operator_class = _payload_optional_str(payload, "operatorClass")
    else:
        operator_class = msgspec.UNSET
    return F8VariantRecord(
        variantId=_payload_str(payload, "variantId"),
        kind=F8VariantKind(_payload_str(payload, "kind")),
        baseNodeType=_payload_str(payload, "baseNodeType"),
        serviceClass=_payload_str(payload, "serviceClass"),
        name=_payload_str(payload, "name"),
        spec=_payload_json_value_dict(payload, "spec"),
        createdAt=_payload_str(payload, "createdAt"),
        updatedAt=_payload_str(payload, "updatedAt"),
        operatorClass=operator_class,
        description=_payload_optional_str(payload, "description") or "",
        tags=_payload_string_list(payload, "tags"),
    )


def _variant_record_payload(record: F8VariantRecord) -> JsonObject:
    tags = [] if isinstance(record.tags, msgspec.UnsetType) else [str(tag) for tag in list(record.tags or []) if str(tag).strip()]
    payload: JsonObject = {
        "variantId": str(record.variantId),
        "kind": str(record.kind.value),
        "baseNodeType": str(record.baseNodeType),
        "serviceClass": str(record.serviceClass),
        "name": str(record.name),
        "spec": cast(JsonObject, record.spec),
        "createdAt": str(record.createdAt),
        "updatedAt": str(record.updatedAt),
        "description": str(record.description),
        "tags": tags,
    }
    if isinstance(record.operatorClass, msgspec.UnsetType):
        return payload
    payload["operatorClass"] = None if record.operatorClass is None else str(record.operatorClass)
    return payload


def _payload_str(payload: JsonObject, key: str) -> str:
    return str(payload[key])


def _payload_optional_str(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _payload_string_list(payload: JsonObject, key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    return json_string_list_loads(value)


def _payload_json_value_dict(payload: JsonObject, key: str) -> dict[str, F8JsonValue]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object field: {key}")
    return cast(dict[str, F8JsonValue], value)


def _local_variant_records() -> list[F8VariantRecord]:
    provider = LocalVariantProvider()
    return [entry.record for entry in provider.load_entries()]
