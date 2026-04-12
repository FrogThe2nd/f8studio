from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
import logging

import msgspec
import zlib
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection as SqlAlchemyConnection
from f8pysdk.codec import copy_model, dump_json, validate_as

from f8pysdk.specs import Entry as F8VariantLibraryEntry
from f8pysdk.specs import F8JsonValue, F8VariantKind, F8VariantLibrary, F8VariantRecord

from ..db import AssetsDatabase, variant_heads_local_table, variant_remote_cache_table, variant_versions_local_table
from ..common import (
    JsonObject,
    json_object_loads,
    json_string_list_loads,
    mapping_optional_str,
    mapping_str,
    stable_json_dumps,
)
from ..common.remote_cache_common import (
    RemoteCacheMetadata,
    remote_cache_metadata_from_fields,
)
from .variant_events import emit_variants_changed
from .variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantLocalVersionSummary,
    F8VariantSourceKind,
    F8VariantSyncState,
    F8VariantVisibility,
    variant_now_iso,
)

logger = logging.getLogger(__name__)


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
                variant_heads_local_table.c.name,
                variant_heads_local_table.c.description,
                variant_heads_local_table.c.tags_json,
                variant_heads_local_table.c.kind,
                variant_heads_local_table.c.base_node_type,
                variant_heads_local_table.c.service_class,
                variant_heads_local_table.c.operator_class,
                variant_heads_local_table.c.latest_version_number,
                variant_heads_local_table.c.created_at,
                variant_heads_local_table.c.updated_at,
                variant_heads_local_table.c.sync_base_remote_revision,
                variant_heads_local_table.c.sync_base_remote_version_number,
                variant_heads_local_table.c.sync_base_local_version_number,
                variant_heads_local_table.c.is_local_draft,
                variant_heads_local_table.c.draft_origin_kind,
                variant_heads_local_table.c.draft_origin_asset_id,
                variant_heads_local_table.c.draft_origin_revision,
                variant_heads_local_table.c.content,
            )
            .order_by(func.lower(variant_heads_local_table.c.name), variant_heads_local_table.c.variant_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_variant_entry_from_local_row(row) for row in rows]

    def save_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        record = entry.record
        version_timestamp = variant_now_iso()
        existing_statement = select(
            variant_heads_local_table.c.variant_id,
            variant_heads_local_table.c.name,
            variant_heads_local_table.c.description,
            variant_heads_local_table.c.tags_json,
            variant_heads_local_table.c.kind,
            variant_heads_local_table.c.base_node_type,
            variant_heads_local_table.c.service_class,
            variant_heads_local_table.c.operator_class,
            variant_heads_local_table.c.latest_version_number,
            variant_heads_local_table.c.created_at,
            variant_heads_local_table.c.updated_at,
            variant_heads_local_table.c.sync_base_remote_revision,
            variant_heads_local_table.c.sync_base_remote_version_number,
            variant_heads_local_table.c.sync_base_local_version_number,
            variant_heads_local_table.c.is_local_draft,
            variant_heads_local_table.c.draft_origin_kind,
            variant_heads_local_table.c.draft_origin_asset_id,
            variant_heads_local_table.c.draft_origin_revision,
            variant_heads_local_table.c.content,
        ).where(variant_heads_local_table.c.variant_id == str(record.variantId))

        with self._db.begin_sqla() as conn:
            existing = conn.execute(existing_statement).mappings().first()
            if existing is None:
                created_at = str(record.createdAt or version_timestamp)
                version_number = _initial_local_version_number(entry)
                _ = conn.execute(
                    insert(variant_heads_local_table).values(
                        variant_id=str(record.variantId),
                        name=str(record.name),
                        description=str(record.description),
                        tags_json=_variant_tags_json(record),
                        kind=str(record.kind.value),
                        base_node_type=str(record.baseNodeType),
                        service_class=str(record.serviceClass),
                        operator_class=_operator_class_db_value(record),
                        latest_version_number=version_number,
                        content=_compress_content(stable_json_dumps(cast(JsonObject, record.spec))),
                        created_at=created_at,
                        updated_at=version_timestamp,
                        sync_base_remote_revision=entry.syncBaseRemoteRevision,
                        sync_base_remote_version_number=entry.syncBaseRemoteVersionNumber,
                        sync_base_local_version_number=entry.syncBaseLocalVersionNumber,
                        is_local_draft=1 if entry.isLocalDraft else 0,
                        draft_origin_kind=None if entry.draftOriginKind is None else entry.draftOriginKind.value,
                        draft_origin_asset_id=entry.draftOriginAssetId,
                        draft_origin_revision=entry.draftOriginRevision,
                    )
                )
                self._insert_local_version_snapshot(
                    conn,
                    record=copy_model(record, update={"createdAt": created_at, "updatedAt": version_timestamp}),
                    version_number=version_number,
                    created_at=version_timestamp,
                )
            else:
                existing_mapping = _row_mapping(existing)
                created_at = mapping_str(existing_mapping, "created_at")
                existing_record = _variant_record_from_row(
                    existing_mapping,
                    spec_payload=_json_value_dict_from_object(json_object_loads(_decompress_content(existing_mapping.get("content")))),
                )
                current_version_number = int(str(existing_mapping.get("latest_version_number") or 1))
                version_changed = _variant_content_changed(existing_record, record)
                version_number = current_version_number + 1 if version_changed else current_version_number
                _ = conn.execute(
                    update(variant_heads_local_table)
                    .where(variant_heads_local_table.c.variant_id == str(record.variantId))
                    .values(
                        name=str(record.name),
                        description=str(record.description),
                        tags_json=_variant_tags_json(record),
                        kind=str(record.kind.value),
                        base_node_type=str(record.baseNodeType),
                        service_class=str(record.serviceClass),
                        operator_class=_operator_class_db_value(record),
                        latest_version_number=version_number,
                        content=_compress_content(stable_json_dumps(cast(JsonObject, record.spec))),
                        updated_at=version_timestamp,
                        sync_base_remote_revision=entry.syncBaseRemoteRevision,
                        sync_base_remote_version_number=entry.syncBaseRemoteVersionNumber,
                        sync_base_local_version_number=entry.syncBaseLocalVersionNumber,
                        is_local_draft=1 if entry.isLocalDraft else 0,
                        draft_origin_kind=None if entry.draftOriginKind is None else entry.draftOriginKind.value,
                        draft_origin_asset_id=entry.draftOriginAssetId,
                        draft_origin_revision=entry.draftOriginRevision,
                    )
                )
                if version_changed:
                    self._insert_local_version_snapshot(
                        conn,
                        record=copy_model(record, update={"createdAt": created_at, "updatedAt": version_timestamp}),
                        version_number=version_number,
                        created_at=version_timestamp,
                    )

        saved_record = copy_model(record, update={"createdAt": created_at, "updatedAt": version_timestamp})
        return copy_model(
            entry,
            update={
                "record": saved_record,
                "source": F8VariantSourceKind.local,
                "syncState": F8VariantSyncState.local_only,
                "installed": True,
                "hasCachedContent": True,
                "localVersionNumber": version_number,
                "syncBaseLocalVersionNumber": (
                    entry.syncBaseLocalVersionNumber
                    if entry.syncBaseLocalVersionNumber is not None
                    else (version_number if entry.syncBaseRemoteRevision is not None else None)
                ),
                "isLocalDraft": entry.isLocalDraft,
                "draftOriginKind": entry.draftOriginKind,
                "draftOriginAssetId": entry.draftOriginAssetId,
                "draftOriginRevision": entry.draftOriginRevision,
            },
        )

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        with self._db.begin_sqla() as conn:
            _ = conn.execute(delete(variant_versions_local_table))
            _ = conn.execute(delete(variant_heads_local_table))
            for entry in entries:
                if entry.source != F8VariantSourceKind.local:
                    continue
                _insert_local_variant_entry(conn, entry)
                record = copy_model(
                    entry.record,
                    update={
                        "createdAt": str(entry.record.createdAt),
                        "updatedAt": str(entry.record.updatedAt),
                    },
                )
                self._insert_local_version_snapshot(
                    conn,
                    record=record,
                    version_number=int(entry.localVersionNumber or 1),
                    created_at=str(entry.record.updatedAt),
                )

    def delete_entry(self, variant_id: str) -> bool:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return False
        with self._db.begin_sqla() as conn:
            _ = conn.execute(
                delete(variant_versions_local_table).where(variant_versions_local_table.c.variant_id == normalized_variant_id)
            )
            head_cursor = conn.execute(
                delete(variant_heads_local_table).where(variant_heads_local_table.c.variant_id == normalized_variant_id)
            )
        return bool(head_cursor.rowcount)

    def list_versions(self, variant_id: str) -> list[F8VariantLocalVersionSummary]:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return []
        statement = (
            select(
                variant_versions_local_table.c.variant_id,
                variant_versions_local_table.c.version_number,
                variant_versions_local_table.c.created_at,
            )
            .where(variant_versions_local_table.c.variant_id == normalized_variant_id)
            .order_by(variant_versions_local_table.c.version_number.asc())
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
            if not rows:
                head_row = conn.execute(
                    select(
                        variant_heads_local_table.c.variant_id,
                        variant_heads_local_table.c.latest_version_number,
                        variant_heads_local_table.c.updated_at,
                    ).where(variant_heads_local_table.c.variant_id == normalized_variant_id)
                ).mappings().first()
                if head_row is None:
                    return []
                head_mapping = _row_mapping(head_row)
                return [
                    F8VariantLocalVersionSummary(
                        variantId=mapping_str(head_mapping, "variant_id"),
                        versionNumber=int(str(head_mapping.get("latest_version_number") or 1)),
                        createdAt=mapping_str(head_mapping, "updated_at"),
                    )
                ]
        return [
            F8VariantLocalVersionSummary(
                variantId=mapping_str(_row_mapping(row), "variant_id"),
                versionNumber=int(str(_row_mapping(row).get("version_number") or 1)),
                createdAt=mapping_str(_row_mapping(row), "created_at"),
            )
            for row in rows
        ]

    def version_record(self, variant_id: str, version_number: int) -> F8VariantRecord | None:
        normalized_variant_id = str(variant_id or "").strip()
        if not normalized_variant_id:
            return None
        target_version_number = int(version_number)
        statement = select(
            variant_versions_local_table.c.record_json
        ).where(
            and_(
                variant_versions_local_table.c.variant_id == normalized_variant_id,
                variant_versions_local_table.c.version_number == target_version_number,
            )
        )
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
            if row is not None:
                record_json = _decompress_content(_row_mapping(row).get("record_json"))
                return validate_as(F8VariantRecord, json_object_loads(record_json))
            head_row = conn.execute(
                select(
                    variant_heads_local_table.c.variant_id,
                    variant_heads_local_table.c.name,
                    variant_heads_local_table.c.description,
                    variant_heads_local_table.c.tags_json,
                    variant_heads_local_table.c.kind,
                    variant_heads_local_table.c.base_node_type,
                    variant_heads_local_table.c.service_class,
                    variant_heads_local_table.c.operator_class,
                    variant_heads_local_table.c.latest_version_number,
                    variant_heads_local_table.c.created_at,
                    variant_heads_local_table.c.updated_at,
                    variant_heads_local_table.c.sync_base_remote_revision,
                    variant_heads_local_table.c.sync_base_remote_version_number,
                    variant_heads_local_table.c.sync_base_local_version_number,
                    variant_heads_local_table.c.is_local_draft,
                    variant_heads_local_table.c.draft_origin_kind,
                    variant_heads_local_table.c.draft_origin_asset_id,
                    variant_heads_local_table.c.draft_origin_revision,
                    variant_heads_local_table.c.content,
                ).where(variant_heads_local_table.c.variant_id == normalized_variant_id)
            ).mappings().first()
        if head_row is None:
            return None
        head_mapping = _row_mapping(head_row)
        if int(str(head_mapping.get("latest_version_number") or 1)) != target_version_number:
            return None
        return _variant_record_from_row(
            head_mapping,
            spec_payload=_json_value_dict_from_object(json_object_loads(_decompress_content(head_mapping.get("content")))),
        )

    def _insert_local_version_snapshot(
        self,
        conn: SqlAlchemyConnection,
        *,
        record: F8VariantRecord,
        version_number: int,
        created_at: str,
    ) -> None:
        _ = conn.execute(
            insert(variant_versions_local_table).values(
                variant_id=str(record.variantId),
                version_number=int(version_number),
                record_json=_compress_content(stable_json_dumps(cast(JsonObject, dump_json(record, mode="json")))),
                created_at=str(created_at),
            )
        )


class RemoteCacheProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db: AssetsDatabase
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
                variant_remote_cache_table.c.remote_version_number,
                variant_remote_cache_table.c.created_at,
                variant_remote_cache_table.c.updated_at,
                variant_remote_cache_table.c.content,
                variant_remote_cache_table.c.source,
                variant_remote_cache_table.c.visibility,
                variant_remote_cache_table.c.owner_user_id,
                variant_remote_cache_table.c.owner_display_name,
                variant_remote_cache_table.c.library_slug,
                variant_remote_cache_table.c.remote_revision,
                variant_remote_cache_table.c.sync_base_remote_revision,
                variant_remote_cache_table.c.sync_state,
                variant_remote_cache_table.c.downloaded_at,
                variant_remote_cache_table.c.installed,
                variant_remote_cache_table.c.has_cached_content,
                variant_remote_cache_table.c.subscribed,
                variant_remote_cache_table.c.sync_base_remote_version_number,
                variant_remote_cache_table.c.sync_base_local_version_number,
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
            logger.debug("Cleaning invalid variant remote cache rows")
            self.save_entries(out)
        return out

    def save_entries(self, entries: list[F8VariantEntry]) -> None:
        with self._db.begin_sqla() as conn:
            _ = conn.execute(delete(variant_remote_cache_table))
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
        saved = self._local_provider.save_entry(local_entry)
        emit_variants_changed()
        return saved

    def delete_local_entry(self, variant_id: str) -> bool:
        deleted = self._local_provider.delete_entry(variant_id)
        if deleted:
            emit_variants_changed()
        return deleted

    def list_local_versions(self, variant_id: str) -> list[F8VariantLocalVersionSummary]:
        return self._local_provider.list_versions(variant_id)

    def local_version_record(self, variant_id: str, version_number: int) -> F8VariantRecord | None:
        return self._local_provider.version_record(variant_id, version_number)

    def replace_remote_entries(self, entries: list[F8VariantEntry]) -> None:
        self._remote_provider.save_entries(entries)
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
        installed_entry = copy_model(
            entry,
            update={
                "installed": True,
                "hasCachedContent": True,
                "downloadedAt": entry.downloadedAt or variant_now_iso(),
            },
        )
        return self._save_remote_entry(installed_entry)

    def cache_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        cached_entry = copy_model(
            entry,
            update={
                "installed": False,
                "hasCachedContent": True,
                "downloadedAt": entry.downloadedAt or variant_now_iso(),
            },
        )
        return self._save_remote_entry(cached_entry)

    def _save_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        installed_entry = copy_model(
            entry,
            update={"downloadedAt": entry.downloadedAt or variant_now_iso()},
        )
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
        raw_entries = library.entries
        return self.import_local_entries(
            [
                F8VariantEntry(
                    record=entry.record,
                    source=F8VariantSourceKind.local,
                    localVersionNumber=None if isinstance(entry.localVersionNumber, msgspec.UnsetType) else int(entry.localVersionNumber),
                    syncBaseRemoteRevision=(
                        None if isinstance(entry.syncBaseRemoteRevision, msgspec.UnsetType) else str(entry.syncBaseRemoteRevision)
                    ),
                    syncBaseRemoteVersionNumber=(
                        None
                        if isinstance(entry.syncBaseRemoteVersionNumber, msgspec.UnsetType)
                        else int(entry.syncBaseRemoteVersionNumber)
                    ),
                    syncBaseLocalVersionNumber=(
                        None if isinstance(entry.syncBaseLocalVersionNumber, msgspec.UnsetType) else int(entry.syncBaseLocalVersionNumber)
                    ),
                    isLocalDraft=True,
                    draftOriginKind=F8VariantDraftOriginKind.new,
                )
                for entry in ([] if isinstance(raw_entries, msgspec.UnsetType) else list(raw_entries or []))
            ],
            mode=mode,
        )

    def import_local_entries(self, entries: list[F8VariantEntry], *, mode: str) -> F8VariantLibrary:
        current_entries = [] if mode == "replace" else self._local_provider.load_entries()
        current_records = [entry.record for entry in current_entries]
        imported_entries = list(current_entries)
        for entry in entries:
            variant = entry.record
            variant_id = str(variant.variantId or "").strip()
            imported_entries = [entry for entry in imported_entries if str(entry.record.variantId or "").strip() != variant_id]
            unique_name = ensure_unique_variant_name(
                variant.baseNodeType,
                variant.name,
                existing_records=current_records,
            )
            if unique_name != variant.name:
                variant = copy_model(variant, update={"name": unique_name})
                entry = copy_model(entry, update={"record": variant})
            imported_entries.append(entry)
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
    return F8VariantEntry(
        record=record,
        source=F8VariantSourceKind.local,
        syncState=F8VariantSyncState.local_only,
        isLocalDraft=True,
        draftOriginKind=F8VariantDraftOriginKind.new,
    )


def entries_to_library(entries: list[F8VariantEntry]) -> F8VariantLibrary:
    return F8VariantLibrary(
        entries=[
            F8VariantLibraryEntry(
                record=entry.record,
                localVersionNumber=entry.localVersionNumber if entry.localVersionNumber is not None else msgspec.UNSET,
                syncBaseRemoteRevision=entry.syncBaseRemoteRevision if entry.syncBaseRemoteRevision is not None else msgspec.UNSET,
                syncBaseRemoteVersionNumber=(
                    entry.syncBaseRemoteVersionNumber if entry.syncBaseRemoteVersionNumber is not None else msgspec.UNSET
                ),
                syncBaseLocalVersionNumber=entry.syncBaseLocalVersionNumber if entry.syncBaseLocalVersionNumber is not None else msgspec.UNSET,
            )
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


def _initial_local_version_number(entry: F8VariantEntry) -> int:
    if entry.localVersionNumber is not None:
        return max(1, int(entry.localVersionNumber))
    if entry.remoteVersionNumber is not None:
        return max(1, int(entry.remoteVersionNumber))
    return 1


def _variant_content_changed(existing_record: F8VariantRecord, incoming_record: F8VariantRecord) -> bool:
    if existing_record.kind != incoming_record.kind:
        return True
    if str(existing_record.baseNodeType) != str(incoming_record.baseNodeType):
        return True
    if str(existing_record.serviceClass) != str(incoming_record.serviceClass):
        return True
    if _normalized_operator_class(existing_record) != _normalized_operator_class(incoming_record):
        return True
    return stable_json_dumps(cast(JsonObject, existing_record.spec)) != stable_json_dumps(cast(JsonObject, incoming_record.spec))


def _normalized_operator_class(record: F8VariantRecord) -> str | None:
    operator_class = record.operatorClass
    if operator_class is None or isinstance(operator_class, msgspec.UnsetType):
        return None
    text = str(operator_class).strip()
    return text or None


def _variant_tags_json(record: F8VariantRecord) -> str:
    tags = [] if isinstance(record.tags, msgspec.UnsetType) else list(record.tags or [])
    return stable_json_dumps(tags)


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


def _variant_entry_from_local_row(row: object) -> F8VariantEntry:
    row_mapping = _row_mapping(row)
    spec_payload = _json_value_dict_from_object(json_object_loads(_decompress_content(row_mapping.get("content"))))
    return F8VariantEntry(
        record=_variant_record_from_row(row_mapping, spec_payload=spec_payload),
        source=F8VariantSourceKind.local,
        syncState=F8VariantSyncState.local_only,
        installed=True,
        hasCachedContent=True,
        localVersionNumber=int(str(row_mapping.get("latest_version_number") or 1)),
        syncBaseRemoteRevision=mapping_optional_str(row_mapping, "sync_base_remote_revision"),
        syncBaseRemoteVersionNumber=int(str(row_mapping.get("sync_base_remote_version_number")))
        if row_mapping.get("sync_base_remote_version_number") is not None
        else None,
        syncBaseLocalVersionNumber=int(str(row_mapping.get("sync_base_local_version_number")))
        if row_mapping.get("sync_base_local_version_number") is not None
        else None,
        isLocalDraft=_sqlite_row_bool(row_mapping, "is_local_draft"),
        draftOriginKind=_variant_draft_origin_kind_from_row(row_mapping, "draft_origin_kind"),
        draftOriginAssetId=mapping_optional_str(row_mapping, "draft_origin_asset_id"),
        draftOriginRevision=mapping_optional_str(row_mapping, "draft_origin_revision"),
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
        librarySlug=metadata.library_slug,
        remoteRevision=metadata.remote_revision,
        syncBaseRemoteRevision=mapping_optional_str(row_mapping, "sync_base_remote_revision"),
        syncState=F8VariantSyncState(metadata.sync_state),
        downloadedAt=metadata.downloaded_at,
        installed=bool(metadata.installed),
        hasCachedContent=_sqlite_row_bool(row_mapping, "has_cached_content"),
        subscribed=metadata.subscribed,
        remoteVersionNumber=int(str(row_mapping.get("remote_version_number"))) if row_mapping.get("remote_version_number") is not None else None,
        syncBaseRemoteVersionNumber=int(str(row_mapping.get("sync_base_remote_version_number")))
        if row_mapping.get("sync_base_remote_version_number") is not None
        else None,
        syncBaseLocalVersionNumber=int(str(row_mapping.get("sync_base_local_version_number")))
        if row_mapping.get("sync_base_local_version_number") is not None
        else None,
    )


def _insert_local_variant_entry(conn: SqlAlchemyConnection, entry: F8VariantEntry) -> None:
    record = entry.record
    _ = conn.execute(
        insert(variant_heads_local_table).values(
            variant_id=str(record.variantId),
            name=str(record.name),
            description=str(record.description),
            tags_json=_variant_tags_json(record),
            kind=str(record.kind.value),
            base_node_type=str(record.baseNodeType),
            service_class=str(record.serviceClass),
            operator_class=_operator_class_db_value(record),
            latest_version_number=int(entry.localVersionNumber or 1),
            content=_compress_content(stable_json_dumps(cast(JsonObject, record.spec))),
            created_at=str(record.createdAt),
            updated_at=str(record.updatedAt),
            sync_base_remote_revision=entry.syncBaseRemoteRevision,
            sync_base_remote_version_number=entry.syncBaseRemoteVersionNumber,
            sync_base_local_version_number=entry.syncBaseLocalVersionNumber,
            is_local_draft=1 if entry.isLocalDraft else 0,
            draft_origin_kind=None if entry.draftOriginKind is None else entry.draftOriginKind.value,
            draft_origin_asset_id=entry.draftOriginAssetId,
            draft_origin_revision=entry.draftOriginRevision,
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
            name=str(entry.record.name),
            description=str(entry.record.description),
            tags_json=stable_json_dumps([] if isinstance(entry.record.tags, msgspec.UnsetType) else list(entry.record.tags or [])),
            kind=str(entry.record.kind.value),
            base_node_type=str(entry.record.baseNodeType),
            service_class=str(entry.record.serviceClass),
            operator_class=(
                None
                if entry.record.operatorClass is None or isinstance(entry.record.operatorClass, msgspec.UnsetType)
                else str(entry.record.operatorClass)
            ),
            remote_version_number=entry.remoteVersionNumber,
            created_at=str(entry.record.createdAt),
            updated_at=str(entry.record.updatedAt),
            source=metadata.source,
            visibility=metadata.visibility,
            owner_user_id=metadata.owner_user_id,
            owner_display_name=metadata.owner_display_name,
            library_slug=metadata.library_slug,
            remote_revision=metadata.remote_revision,
            sync_base_remote_revision=entry.syncBaseRemoteRevision,
            sync_state=metadata.sync_state,
            downloaded_at=metadata.downloaded_at,
            installed=1 if metadata.installed else 0,
            has_cached_content=1 if variant_entry_has_cached_content(entry) else 0,
            subscribed=1 if metadata.subscribed else 0,
            sync_base_remote_version_number=entry.syncBaseRemoteVersionNumber,
            sync_base_local_version_number=entry.syncBaseLocalVersionNumber,
            content=_compress_content(
                stable_json_dumps(cast(JsonObject, entry.record.spec if variant_entry_has_cached_content(entry) else {}))
            ),
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


def _compress_content(json_str: str) -> bytes:
    return zlib.compress(json_str.encode("utf-8"), level=6, wbits=31)


def _decompress_content(data: bytes | None) -> str:
    if data is None:
        return "{}"
    try:
        return zlib.decompress(data, wbits=31).decode("utf-8")
    except zlib.error as exc:
        raise ValueError("Invalid compressed variant content.") from exc


def _row_mapping(row: object) -> Mapping[object, object]:
    if not isinstance(row, Mapping):
        raise TypeError("Expected mapping row for variant entry.")
    return cast(Mapping[object, object], row)


def _variant_record_from_row(row: Mapping[object, object], *, spec_payload: dict[str, F8JsonValue]) -> F8VariantRecord:
    kind = F8VariantKind(mapping_str(row, "kind"))
    operator_class_value = row.get("operator_class")
    operator_class: str | None | msgspec.UnsetType
    if operator_class_value is None and kind == F8VariantKind.service:
        operator_class = msgspec.UNSET
    else:
        operator_class = None if operator_class_value is None else str(operator_class_value)
    return F8VariantRecord(
        variantId=mapping_str(row, "variant_id"),
        kind=kind,
        baseNodeType=mapping_str(row, "base_node_type"),
        serviceClass=mapping_str(row, "service_class"),
        name=mapping_str(row, "name"),
        spec=spec_payload,
        createdAt=mapping_str(row, "created_at"),
        updatedAt=mapping_str(row, "updated_at"),
        operatorClass=operator_class,
        description=mapping_optional_str(row, "description") or "",
        tags=json_string_list_loads(row.get("tags_json")),
    )


def _json_value_dict_from_object(value: object) -> dict[str, F8JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object payload.")
    return cast(dict[str, F8JsonValue], value)


def _sqlite_row_bool(row: Mapping[object, object], key: str) -> bool:
    return bool(int(str(row[key])))


def _variant_draft_origin_kind_from_row(
    row: Mapping[object, object],
    key: str,
) -> F8VariantDraftOriginKind | None:
    raw_value = mapping_optional_str(row, key)
    if raw_value is None:
        return None
    return F8VariantDraftOriginKind(raw_value)


def _local_variant_records() -> list[F8VariantRecord]:
    provider = LocalVariantProvider()
    return [entry.record for entry in provider.load_entries()]
