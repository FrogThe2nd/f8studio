from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol, cast
import logging
import zlib

from sqlalchemy import delete, insert, select
from f8pysdk.codec import copy_model
from f8pysdk.specs import Entry as F8VariantLibraryEntry
from f8pysdk.specs import F8JsonValue, F8VariantKind, F8VariantLibrary, F8VariantRecord

from ..common import JsonObject, json_object_loads, json_string_list_loads, mapping_optional_str, mapping_str, stable_json_dumps
from ..common.remote_cache_common import RemoteCacheMetadata, remote_cache_metadata_from_fields
from ..db import AssetsDatabase, variant_remote_cache_table
from .variant_drafts import VariantDraftService, draft_as_catalog_entry
from .variant_events import emit_variants_changed
from .variant_models import (
    F8VariantDraftEntry,
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantLocalVersionSummary,
    F8VariantSourceKind,
    F8VariantVisibility,
    variant_now_iso,
)

logger = logging.getLogger(__name__)


class VariantSourceProvider(Protocol):
    def load_entries(self) -> list[F8VariantEntry]: ...


class LocalVariantProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._draft_service = VariantDraftService(db_path=db_path)

    def load_entries(self) -> list[F8VariantEntry]:
        return self._draft_service.list_catalog_entries()

    def save_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        saved = _save_entry_as_draft(self._draft_service, entry)
        return draft_as_catalog_entry(saved)

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        existing_drafts = self._draft_service.list_drafts()
        for draft in existing_drafts:
            self._draft_service.delete_draft(draft.draftId)
        for entry in entries:
            if entry.source != F8VariantSourceKind.local:
                continue
            _save_entry_as_draft(self._draft_service, entry)

    def delete_entry(self, variant_id: str) -> bool:
        return self._draft_service.delete_draft(variant_id)

    def list_versions(self, variant_id: str) -> list[F8VariantLocalVersionSummary]:
        del variant_id
        return []

    def version_record(self, variant_id: str, version_number: int) -> F8VariantRecord | None:
        del variant_id
        del version_number
        return None


class RemoteCacheProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def load_entries(self) -> list[F8VariantEntry]:
        statement = (
            select(
                variant_remote_cache_table.c.variant_id,
                variant_remote_cache_table.c.name,
                variant_remote_cache_table.c.description,
                variant_remote_cache_table.c.tags_json,
                variant_remote_cache_table.c.kind,
                variant_remote_cache_table.c.base_node_type,
                variant_remote_cache_table.c.service_class,
                variant_remote_cache_table.c.operator_class,
                variant_remote_cache_table.c.created_at,
                variant_remote_cache_table.c.updated_at,
                variant_remote_cache_table.c.content,
                variant_remote_cache_table.c.source,
                variant_remote_cache_table.c.visibility,
                variant_remote_cache_table.c.owner_user_id,
                variant_remote_cache_table.c.owner_display_name,
                variant_remote_cache_table.c.remote_version_number,
                variant_remote_cache_table.c.downloaded_at,
                variant_remote_cache_table.c.installed,
                variant_remote_cache_table.c.has_cached_content,
                variant_remote_cache_table.c.subscribed,
            )
            .order_by(variant_remote_cache_table.c.variant_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8VariantEntry] = []
        invalid_found = False
        for row in rows:
            try:
                entry = _variant_entry_from_remote_row(row)
            except Exception:
                logger.exception("Ignoring invalid cached remote variant entry")
                invalid_found = True
                continue
            if not str(entry.record.variantId or "").strip():
                logger.warning("Ignoring cached remote variant entry with empty variantId")
                invalid_found = True
                continue
            out.append(entry)
        if invalid_found:
            self.save_entries(out)
        return out

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        with self._db.begin_sqla() as conn:
            conn.execute(delete(variant_remote_cache_table))
            for entry in entries:
                if not str(entry.record.variantId or "").strip():
                    continue
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
        if db_path is not None:
            self._db_path = Path(db_path)
        elif local_provider is not None and hasattr(local_provider, "_draft_service"):
            self._db_path = local_provider._draft_service._db.path  # pyright: ignore[reportAttributeAccessIssue]
        elif remote_provider is not None:
            self._db_path = remote_provider._db.path  # pyright: ignore[reportAttributeAccessIssue]
        else:
            self._db_path = AssetsDatabase().path
        self._remote_provider = RemoteCacheProvider(self._db_path) if remote_provider is None else remote_provider
        self._local_provider = LocalVariantProvider(self._db_path) if local_provider is None else local_provider

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load_all_entries(self) -> list[F8VariantEntry]:
        merged: dict[str, F8VariantEntry] = {}
        for source_entries in [self._remote_provider.load_entries(), self._local_provider.load_entries()]:
            for entry in source_entries:
                variant_id = str(entry.record.variantId or "").strip()
                if variant_id:
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
        draft = VariantDraftService(db_path=self._db_path).draft(normalized_variant_id)
        if draft is not None:
            return draft_as_catalog_entry(draft)
        entry = self.remote_entry(normalized_variant_id)
        if entry is None:
            return None
        if include_uninstalled or is_entry_usable(entry):
            return entry
        return None

    def record(self, variant_id: str, *, include_uninstalled: bool = False) -> F8VariantRecord | None:
        entry = self.entry(variant_id, include_uninstalled=include_uninstalled)
        return None if entry is None else entry.record

    def variant_exists(self, variant_id: str) -> bool:
        return self.record(variant_id, include_uninstalled=True) is not None

    def upsert_local_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        draft_service = VariantDraftService(db_path=self._db_path)
        current_entries = draft_service.list_catalog_entries()
        _validate_unique_name(current_entries, entry.record, exclude_variant_id=str(entry.record.variantId))
        saved = _save_entry_as_draft(draft_service, entry)
        emit_variants_changed()
        return draft_as_catalog_entry(saved)

    def delete_local_entry(self, variant_id: str) -> bool:
        deleted = VariantDraftService(db_path=self._db_path).delete_draft(variant_id)
        if deleted:
            emit_variants_changed()
        return deleted

    def list_local_versions(self, variant_id: str) -> list[F8VariantLocalVersionSummary]:
        del variant_id
        return []

    def local_version_record(self, variant_id: str, version_number: int) -> F8VariantRecord | None:
        del variant_id
        del version_number
        return None

    def replace_remote_entries(self, entries: list[F8VariantEntry], *, emit_changed: bool = True) -> None:
        normalized_entries = [_normalize_remote_variant_entry_for_storage(entry) for entry in entries]
        current_entries = self._remote_provider.load_entries()
        if current_entries == normalized_entries:
            return
        self._remote_provider.save_entries(normalized_entries)
        if emit_changed:
            emit_variants_changed()

    def load_remote_entries(self) -> list[F8VariantEntry]:
        return self._remote_provider.load_entries()

    def remote_entry(self, variant_id: str) -> F8VariantEntry | None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        for entry in self._remote_provider.load_entries():
            if str(entry.record.variantId or "").strip() == normalized_variant_id:
                return entry
        return None

    def install_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        downloaded_at = entry.downloadedAt or variant_now_iso()
        installed_entry = copy_model(
            entry,
            update={
                "installed": True,
                "hasCachedContent": True,
                "downloadedAt": downloaded_at,
            },
        )
        return self._save_remote_entry(installed_entry)

    def cache_remote_entry(self, entry: F8VariantEntry, *, emit_changed: bool = True) -> F8VariantEntry:
        downloaded_at = entry.downloadedAt or variant_now_iso()
        cached_entry = copy_model(
            entry,
            update={
                "installed": False,
                "hasCachedContent": True,
                "downloadedAt": downloaded_at,
            },
        )
        return self._save_remote_entry(cached_entry, emit_changed=emit_changed)

    def _save_remote_entry(self, entry: F8VariantEntry, *, emit_changed: bool = True) -> F8VariantEntry:
        normalized_entry = _normalize_remote_variant_entry_for_storage(entry)
        current = self._remote_provider.load_entries()
        existing_entry: F8VariantEntry | None = None
        out: list[F8VariantEntry] = []
        found = False
        for current_entry in current:
            if str(current_entry.record.variantId) == str(entry.record.variantId):
                existing_entry = current_entry
                out.append(normalized_entry)
                found = True
            else:
                out.append(current_entry)
        if existing_entry is not None and existing_entry == normalized_entry:
            return normalized_entry
        if not found:
            out.append(normalized_entry)
        self._remote_provider.save_entries(out)
        if emit_changed:
            emit_variants_changed()
        return normalized_entry

    def uninstall_remote_entry(self, variant_id: str) -> F8VariantEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        target: F8VariantEntry | None = None
        normalized_variant_id = str(variant_id or "").strip()
        for entry in current:
            if str(entry.record.variantId or "").strip() != normalized_variant_id:
                out.append(entry)
                continue
            target = copy_model(
                entry,
                update={
                    "record": copy_model(entry.record, update={"spec": {}}),
                    "installed": False,
                    "hasCachedContent": False,
                    "downloadedAt": None,
                },
            )
            out.append(target)
        if target is None:
            return None
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return target

    def delete_remote_entry(self, variant_id: str) -> bool:
        current = self._remote_provider.load_entries()
        normalized_variant_id = str(variant_id or "").strip()
        out = [entry for entry in current if str(entry.record.variantId or "").strip() != normalized_variant_id]
        if len(out) == len(current):
            return False
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return True

    def mark_conflict(self, variant_id: str, *, remote_version_number: int | None) -> F8VariantEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8VariantEntry] = []
        target: F8VariantEntry | None = None
        for entry in current:
            if str(entry.record.variantId) == str(variant_id):
                target = copy_model(entry, update={"remoteVersionNumber": remote_version_number})
                out.append(target)
            else:
                out.append(entry)
        if target is None:
            return None
        self._remote_provider.save_entries(out)
        emit_variants_changed()
        return target

    def export_local_library(self) -> F8VariantLibrary:
        return entries_to_library(VariantDraftService(db_path=self._db_path).list_catalog_entries())

    def import_local_library(self, library: F8VariantLibrary, *, mode: str) -> F8VariantLibrary:
        entries = [
            local_entry_from_record(entry.record)
            for entry in list(library.entries or [])
        ]
        return self.import_local_entries(entries, mode=mode)

    def import_local_entries(self, entries: list[F8VariantEntry], *, mode: str) -> F8VariantLibrary:
        draft_service = VariantDraftService(db_path=self._db_path)
        current_entries = [] if mode == "replace" else draft_service.list_catalog_entries()
        current_records = [entry.record for entry in current_entries]
        imported_entries = list(current_entries)
        for entry in entries:
            variant = entry.record
            variant_id = str(variant.variantId or "").strip()
            imported_entries = [item for item in imported_entries if str(item.record.variantId or "").strip() != variant_id]
            unique_name = ensure_unique_variant_name(
                variant.baseNodeType,
                variant.name,
                existing_records=current_records,
            )
            if unique_name != variant.name:
                variant = copy_model(variant, update={"name": unique_name})
                entry = copy_model(entry, update={"record": variant})
            imported_entries.append(entry)
            current_records = [item.record for item in imported_entries]
        if mode == "replace":
            for existing_draft in draft_service.list_drafts():
                draft_service.delete_draft(existing_draft.draftId)
        else:
            for entry in current_entries:
                draft_service.delete_draft(str(entry.record.variantId))
        for entry in imported_entries:
            _ = self.upsert_local_entry(entry)
        emit_variants_changed()
        return entries_to_library(imported_entries)


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
    return F8VariantEntry(
        record=record,
        source=F8VariantSourceKind.local,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
        draftOriginKind=F8VariantDraftOriginKind.new,
    )


def entries_to_library(entries: list[F8VariantEntry]) -> F8VariantLibrary:
    return F8VariantLibrary(
        entries=[
            F8VariantLibraryEntry(record=entry.record)
            for entry in entries
            if entry.source == F8VariantSourceKind.local
        ]
    )


def is_entry_usable(entry: F8VariantEntry) -> bool:
    if entry.source == F8VariantSourceKind.local:
        return True
    return variant_entry_is_installed(entry)


def _entry_sort_key(entry: F8VariantEntry) -> tuple[str, str, str]:
    record = entry.record
    return (
        str(record.baseNodeType or "").lower(),
        str(record.name or "").lower(),
        str(record.variantId or ""),
    )


def _local_variant_records() -> list[F8VariantRecord]:
    provider = LocalVariantProvider()
    return [entry.record for entry in provider.load_entries()]


def _save_entry_as_draft(draft_service: VariantDraftService, entry: F8VariantEntry) -> F8VariantDraftEntry:
    existing_draft = draft_service.draft(str(entry.record.variantId))
    origin_kind = entry.draftOriginKind or (existing_draft.originKind if existing_draft is not None else F8VariantDraftOriginKind.new)
    if existing_draft is None:
        return draft_service.create_draft_from_record(
            entry.record,
            origin_kind=origin_kind,
            publish_target_asset_id=entry.draftOriginAssetId,
            publish_base_remote_version_number=entry.draftOriginVersionNumber,
            draft_id=str(entry.record.variantId),
        )
    return draft_service.save_draft(
        F8VariantDraftEntry(
            draftId=existing_draft.draftId,
            record=entry.record,
            originKind=origin_kind,
            publishTargetAssetId=entry.draftOriginAssetId or existing_draft.publishTargetAssetId,
            publishBaseRemoteVersionNumber=entry.draftOriginVersionNumber or existing_draft.publishBaseRemoteVersionNumber,
            createdAt=existing_draft.createdAt,
            updatedAt=existing_draft.updatedAt,
        )
    )


def _variant_entry_from_remote_row(row: object) -> F8VariantEntry:
    row_mapping = _row_mapping(row)
    metadata = RemoteCacheMetadata.from_row(row_mapping)
    spec_payload = _json_value_dict_from_object(json_object_loads(_decompress_content(row_mapping.get("content"))))
    visibility = None if metadata.visibility is None else F8VariantVisibility(metadata.visibility)
    return F8VariantEntry(
        record=_variant_record_from_row(row_mapping, spec_payload=spec_payload),
        source=F8VariantSourceKind(metadata.source),
        visibility=visibility,
        ownerUserId=metadata.owner_user_id,
        ownerDisplayName=metadata.owner_display_name,
        remoteVersionNumber=metadata.remote_version_number,
        downloadedAt=metadata.downloaded_at,
        installed=bool(metadata.installed),
        hasCachedContent=_sqlite_row_bool(row_mapping, "has_cached_content"),
        subscribed=metadata.subscribed,
    )


def _insert_remote_variant_entry(conn: object, entry: F8VariantEntry) -> None:
    metadata = remote_cache_metadata_from_fields(
        source=str(entry.source.value),
        visibility=None if entry.visibility is None else str(entry.visibility.value),
        owner_user_id=entry.ownerUserId,
        owner_display_name=entry.ownerDisplayName,
        remote_version_number=entry.remoteVersionNumber,
        downloaded_at=entry.downloadedAt,
        installed=entry.installed,
        subscribed=entry.subscribed,
    )
    conn.execute(
        insert(variant_remote_cache_table).values(
            variant_id=str(entry.record.variantId),
            name=str(entry.record.name),
            description=str(entry.record.description),
            tags_json=stable_json_dumps(list(entry.record.tags or [])),
            kind=str(entry.record.kind.value),
            base_node_type=str(entry.record.baseNodeType),
            service_class=str(entry.record.serviceClass),
            operator_class=_operator_class_db_value(entry.record),
            created_at=str(entry.record.createdAt),
            updated_at=str(entry.record.updatedAt),
            source=metadata.source,
            visibility=metadata.visibility,
            owner_user_id=metadata.owner_user_id,
            owner_display_name=metadata.owner_display_name,
            remote_version_number=metadata.remote_version_number,
            downloaded_at=metadata.downloaded_at,
            installed=1 if metadata.installed else 0,
            has_cached_content=1 if variant_entry_has_cached_content(entry) else 0,
            subscribed=1 if metadata.subscribed else 0,
            content=_compress_content(stable_json_dumps(cast(JsonObject, entry.record.spec if variant_entry_has_cached_content(entry) else {}))),
        )
    )


def variant_entry_has_cached_content(entry: F8VariantEntry) -> bool:
    if entry.source == F8VariantSourceKind.local:
        return True
    if entry.hasCachedContent is not None:
        return bool(entry.hasCachedContent)
    return bool(entry.installed)


def variant_entry_is_installed(entry: F8VariantEntry) -> bool:
    if entry.source == F8VariantSourceKind.local:
        return True
    return bool(entry.installed and variant_entry_has_cached_content(entry))


def variant_entry_can_hydrate(entry: F8VariantEntry) -> bool:
    return entry.source in {
        F8VariantSourceKind.remote_official,
        F8VariantSourceKind.remote_public,
        F8VariantSourceKind.remote_private,
    }


def _normalize_remote_variant_entry_for_storage(entry: F8VariantEntry) -> F8VariantEntry:
    has_cached_content = variant_entry_has_cached_content(entry)
    normalized_spec = entry.record.spec if has_cached_content else {}
    normalized_record = copy_model(
        entry.record,
        update={"spec": normalized_spec},
    )
    return copy_model(
        entry,
        update={
            "record": normalized_record,
            "hasCachedContent": has_cached_content,
        },
    )


def _compress_content(json_str: str) -> bytes:
    return zlib.compress(json_str.encode("utf-8"), level=6, wbits=31)


def _decompress_content(data: object) -> str:
    if data is None:
        return "{}"
    raw = bytes(data)
    try:
        return zlib.decompress(raw, wbits=31).decode("utf-8")
    except zlib.error as exc:
        raise ValueError("Invalid compressed variant content.") from exc


def _row_mapping(row: object) -> Mapping[object, object]:
    if not isinstance(row, Mapping):
        raise TypeError("Expected mapping row for variant entry.")
    return cast(Mapping[object, object], row)


def _variant_record_from_row(row: Mapping[object, object], *, spec_payload: dict[str, F8JsonValue]) -> F8VariantRecord:
    kind = F8VariantKind(mapping_str(row, "kind"))
    operator_class = mapping_optional_str(row, "operator_class")
    return F8VariantRecord(
        variantId=mapping_str(row, "variant_id"),
        kind=kind,
        baseNodeType=mapping_str(row, "base_node_type"),
        serviceClass=mapping_str(row, "service_class"),
        operatorClass=operator_class,
        name=mapping_str(row, "name"),
        description=mapping_str(row, "description"),
        tags=json_string_list_loads(row.get("tags_json")),
        spec=spec_payload,
        createdAt=mapping_str(row, "created_at"),
        updatedAt=mapping_str(row, "updated_at"),
    )


def _json_value_dict_from_object(value: object) -> dict[str, F8JsonValue]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, F8JsonValue], value)


def _normalized_operator_class(record: F8VariantRecord) -> str | None:
    operator_class = record.operatorClass
    if operator_class is None:
        return None
    text = str(operator_class).strip()
    return text or None


def _operator_class_db_value(record: F8VariantRecord) -> str | None:
    return _normalized_operator_class(record)


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


def _sqlite_row_bool(row: Mapping[object, object], key: str) -> bool:
    return bool(int(str(row[key])))
