from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
from typing import cast

import zlib
from sqlalchemy import and_, delete, func, insert, select, update
from f8pysdk.msgspec_codec import copy_model

from ..db import (
    AssetsDatabase,
    component_heads_local_table,
    component_remote_cache_table,
)
from ..common import (
    JsonObject,
    json_object_loads,
    json_string_list_loads,
    mapping_int,
    mapping_optional_str,
    mapping_str,
    stable_json_dumps,
)
from .component_events import emit_components_changed
from .component_models import (
    F8ComponentEntry,
    F8ComponentLocalVersionSummary,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentSyncState,
    F8ComponentVisibility,
    component_now_iso,
)
from ..common.remote_cache_common import (
    RemoteCacheMetadata,
    remote_cache_metadata_from_fields,
)

logger = logging.getLogger(__name__)


class LocalComponentProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db: AssetsDatabase
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def load_entries(self) -> list[F8ComponentEntry]:
        statement = (
            select(
                component_heads_local_table.c.component_id,
                component_heads_local_table.c.name,
                component_heads_local_table.c.description,
                component_heads_local_table.c.usage_notes,
                component_heads_local_table.c.tags_json,
                component_heads_local_table.c.schema_version,
                component_heads_local_table.c.latest_version_number,
                component_heads_local_table.c.content,
                component_heads_local_table.c.created_at,
                component_heads_local_table.c.updated_at,
            )
            .order_by(func.lower(component_heads_local_table.c.name), component_heads_local_table.c.component_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ComponentEntry] = []
        for row in rows:
            row_mapping = _row_mapping(row)
            record = _component_record_from_row(row_mapping, updated_at_key="updated_at")
            out.append(F8ComponentEntry(record=record, source=F8ComponentSourceKind.local))
        return out

    def save_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        record = entry.record
        version_timestamp = component_now_iso()
        existing_statement = select(
            component_heads_local_table.c.created_at,
            component_heads_local_table.c.latest_version_number,
        ).where(component_heads_local_table.c.component_id == str(record.componentId))
        
        with self._db.begin_sqla() as conn:
            existing = conn.execute(existing_statement).mappings().first()
            if existing is None:
                created_at = str(record.createdAt or version_timestamp)
                version_number = 1
                _ = conn.execute(
                    insert(component_heads_local_table).values(
                        component_id=str(record.componentId),
                        name=str(record.name),
                        description=str(record.description),
                        usage_notes=str(record.usageNotes),
                        tags_json=stable_json_dumps(list(record.tags or [])),
                        schema_version=str(record.schemaVersion),
                        latest_version_number=version_number,
                        content=_compress_content(stable_json_dumps(record.content)),
                        created_at=created_at,
                        updated_at=version_timestamp,
                    )
                )
            else:
                existing_mapping = _row_mapping(existing)
                created_at = mapping_str(existing_mapping, "created_at")
                version_number = mapping_int(existing_mapping, "latest_version_number") + 1
                _ = conn.execute(
                    update(component_heads_local_table)
                    .where(component_heads_local_table.c.component_id == str(record.componentId))
                    .values(
                        name=str(record.name),
                        description=str(record.description),
                        usage_notes=str(record.usageNotes),
                        tags_json=stable_json_dumps(list(record.tags or [])),
                        schema_version=str(record.schemaVersion),
                        latest_version_number=version_number,
                        content=_compress_content(stable_json_dumps(record.content)),
                        updated_at=version_timestamp,
                    )
                )
        
        saved_record = F8ComponentRecord(
            componentId=str(record.componentId),
            name=str(record.name),
            description=str(record.description),
            usageNotes=str(record.usageNotes),
            tags=[str(tag) for tag in list(record.tags or []) if str(tag).strip()],
            schemaVersion=str(record.schemaVersion),
            content=record.content,
            createdAt=created_at,
            updatedAt=version_timestamp,
        )
        return F8ComponentEntry(
            record=saved_record,
            source=entry.source,
            visibility=entry.visibility,
            ownerUserId=entry.ownerUserId,
            ownerDisplayName=entry.ownerDisplayName,
            librarySlug=entry.librarySlug,
            remoteRevision=entry.remoteRevision,
            syncState=entry.syncState,
            downloadedAt=entry.downloadedAt,
            installed=entry.installed,
            subscribed=entry.subscribed,
        )

    def delete_entry(self, component_id: str) -> bool:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return False
        with self._db.begin_sqla() as conn:
            head_cursor = conn.execute(
                delete(component_heads_local_table).where(component_heads_local_table.c.component_id == normalized_component_id)
            )
        return bool(head_cursor.rowcount)


class RemoteComponentCacheProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db: AssetsDatabase
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def load_entries(self) -> list[F8ComponentEntry]:
        statement = (
            select(
                component_remote_cache_table.c.component_id,
                component_remote_cache_table.c.content,
                component_remote_cache_table.c.source,
                component_remote_cache_table.c.visibility,
                component_remote_cache_table.c.owner_user_id,
                component_remote_cache_table.c.owner_display_name,
                component_remote_cache_table.c.library_slug,
                component_remote_cache_table.c.remote_revision,
                component_remote_cache_table.c.sync_state,
                component_remote_cache_table.c.downloaded_at,
                component_remote_cache_table.c.installed,
                component_remote_cache_table.c.subscribed,
            )
            .order_by(component_remote_cache_table.c.component_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ComponentEntry] = []
        invalid_found = False
        for row in rows:
            row_mapping = _row_mapping(row)
            try:
                record_payload = json_object_loads(_decompress_content(row_mapping.get("content")))
                metadata = RemoteCacheMetadata.from_row(row_mapping)
                entry = _component_entry_from_remote(record_payload, metadata)
            except Exception:
                logger.exception("Ignoring invalid cached remote component entry")
                invalid_found = True
                continue
            if not str(entry.record.componentId or "").strip():
                logger.warning("Ignoring cached remote component entry with empty componentId")
                invalid_found = True
                continue
            out.append(entry)
        if invalid_found:
            logger.info("Cleaning invalid component remote cache rows")
            self.save_entries(out)
        return out

    def save_entries(self, entries: list[F8ComponentEntry]) -> None:
        with self._db.begin_sqla() as conn:
            _ = conn.execute(delete(component_remote_cache_table))
            for entry in entries:
                component_id = str(entry.record.componentId or "").strip()
                if not component_id:
                    continue
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
                    insert(component_remote_cache_table).values(
                        component_id=component_id,
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
                        content=_compress_content(stable_json_dumps(_component_record_payload(entry.record))),
                        updated_at=component_now_iso(),
                    )
                )


class ComponentCatalogService:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        local_provider: LocalComponentProvider | None = None,
        remote_provider: RemoteComponentCacheProvider | None = None,
    ) -> None:
        self._local_provider: LocalComponentProvider
        self._local_provider = LocalComponentProvider(db_path) if local_provider is None else local_provider
        self._remote_provider: RemoteComponentCacheProvider
        self._remote_provider = RemoteComponentCacheProvider(db_path) if remote_provider is None else remote_provider

    def load_all_entries(self) -> list[F8ComponentEntry]:
        merged: dict[str, F8ComponentEntry] = {}
        for source_entries in [self._remote_provider.load_entries(), self._local_provider.load_entries()]:
            for entry in source_entries:
                component_id = str(entry.record.componentId or "").strip()
                if not component_id:
                    continue
                merged[component_id] = entry
        return sorted(merged.values(), key=_entry_sort_key)

    def entry(self, component_id: str, *, include_uninstalled: bool = True) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self.load_all_entries():
            if str(entry.record.componentId) != normalized_component_id:
                continue
            if include_uninstalled or entry.installed or entry.source == F8ComponentSourceKind.local:
                return entry
        return None

    def list_entries(self, *, include_uninstalled: bool = False) -> list[F8ComponentEntry]:
        entries = self.load_all_entries()
        if include_uninstalled:
            return entries
        return [entry for entry in entries if entry.installed or entry.source == F8ComponentSourceKind.local]

    def upsert_local_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        local_entry = entry if entry.source == F8ComponentSourceKind.local else copy_model(entry, update={"source": F8ComponentSourceKind.local})
        saved = self._local_provider.save_entry(local_entry)
        emit_components_changed()
        return saved

    def delete_local_entry(self, component_id: str) -> bool:
        deleted = self._local_provider.delete_entry(component_id)
        if deleted:
            emit_components_changed()
        return deleted

    def list_local_versions(self, component_id: str) -> list[F8ComponentLocalVersionSummary]:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return []
        statement = select(
            component_heads_local_table.c.component_id,
            component_heads_local_table.c.latest_version_number,
            component_heads_local_table.c.created_at,
        ).where(component_heads_local_table.c.component_id == normalized_component_id)
        with self._db_for_local_provider().connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return []
        row_mapping = _row_mapping(row)
        return [
            F8ComponentLocalVersionSummary(
                componentId=mapping_str(row_mapping, "component_id"),
                versionNumber=mapping_int(row_mapping, "latest_version_number"),
                createdAt=mapping_str(row_mapping, "created_at"),
            )
        ]

    def local_version_record(self, component_id: str, version_number: int) -> F8ComponentRecord | None:
        entry = self.entry(component_id)
        return None if entry is None else entry.record

    def _db_for_local_provider(self) -> AssetsDatabase:
        return self._local_provider._db

    def replace_remote_entries(self, entries: list[F8ComponentEntry]) -> None:
        self._remote_provider.save_entries(entries)
        emit_components_changed()

    def load_remote_entries(self) -> list[F8ComponentEntry]:
        return self._remote_provider.load_entries()

    def install_remote_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        installed_entry = copy_model(entry, update={"installed": True, "downloadedAt": component_now_iso()})
        current = self._remote_provider.load_entries()
        out: list[F8ComponentEntry] = []
        found = False
        for current_entry in current:
            if str(current_entry.record.componentId) == str(installed_entry.record.componentId):
                out.append(installed_entry)
                found = True
            else:
                out.append(current_entry)
        if not found:
            out.append(installed_entry)
        self._remote_provider.save_entries(out)
        emit_components_changed()
        return installed_entry

    def mark_conflict(self, component_id: str, *, remote_revision: str | None) -> F8ComponentEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8ComponentEntry] = []
        target: F8ComponentEntry | None = None
        for entry in current:
            if str(entry.record.componentId) == str(component_id):
                target = copy_model(entry, update={"syncState": F8ComponentSyncState.conflict, "remoteRevision": remote_revision})
                out.append(target)
            else:
                out.append(entry)
        if target is None:
            return None
        self._remote_provider.save_entries(out)
        emit_components_changed()
        return target


def _entry_sort_key(entry: F8ComponentEntry) -> tuple[str, str]:
    return (str(entry.record.name or "").lower(), str(entry.record.componentId or ""))


def _row_mapping(row: object) -> Mapping[object, object]:
    if not isinstance(row, Mapping):
        raise TypeError("Expected SQLAlchemy row mapping.")
    return cast(Mapping[object, object], row)


def _component_record_from_row(row: Mapping[object, object], *, updated_at_key: str = "updated_at") -> F8ComponentRecord:
    updated_at = mapping_optional_str(row, updated_at_key)
    content_raw = row.get("content")
    return F8ComponentRecord(
        componentId=mapping_str(row, "component_id"),
        name=mapping_str(row, "name"),
        description=mapping_str(row, "description"),
        usageNotes=mapping_str(row, "usage_notes"),
        tags=json_string_list_loads(row.get("tags_json")),
        schemaVersion=mapping_str(row, "schema_version"),
        content=json_object_loads(_decompress_content(content_raw)),
        createdAt=mapping_str(row, "created_at"),
        updatedAt=updated_at if updated_at is not None else mapping_str(row, "updated_at"),
    )


def _compress_content(json_str: str) -> bytes:
    return zlib.compress(json_str.encode("utf-8"), level=6, wbits=31)


def _decompress_content(data: bytes | None) -> str:
    if data is None:
        return "{}"
    try:
        return zlib.decompress(data, wbits=31).decode("utf-8")
    except Exception:
        if isinstance(data, str):
            return data
        return (data or b"").decode("utf-8", errors="replace")


def _component_record_from_payload(payload: JsonObject) -> F8ComponentRecord:
    return F8ComponentRecord(
        componentId=_payload_str(payload, "componentId"),
        name=_payload_str(payload, "name"),
        description=_payload_optional_str(payload, "description") or "",
        usageNotes=_payload_optional_str(payload, "usageNotes") or "",
        tags=_payload_string_list(payload, "tags"),
        schemaVersion=_payload_str(payload, "schemaVersion"),
        content=_payload_json_object(payload, "content"),
        createdAt=_payload_str(payload, "createdAt"),
        updatedAt=_payload_str(payload, "updatedAt"),
    )


def _component_record_payload(record: F8ComponentRecord) -> JsonObject:
    return {
        "componentId": str(record.componentId),
        "name": str(record.name),
        "description": str(record.description),
        "usageNotes": str(record.usageNotes),
        "tags": [str(tag) for tag in list(record.tags or []) if str(tag).strip()],
        "schemaVersion": str(record.schemaVersion),
        "content": record.content,
        "createdAt": str(record.createdAt),
        "updatedAt": str(record.updatedAt),
    }


def _component_entry_from_remote(record_payload: JsonObject, metadata: RemoteCacheMetadata) -> F8ComponentEntry:
    visibility = None if metadata.visibility is None else F8ComponentVisibility(metadata.visibility)
    return F8ComponentEntry(
        record=_component_record_from_payload(record_payload),
        source=F8ComponentSourceKind(metadata.source),
        visibility=visibility,
        ownerUserId=metadata.owner_user_id,
        ownerDisplayName=metadata.owner_display_name,
        librarySlug=metadata.library_slug,
        remoteRevision=metadata.remote_revision,
        syncState=F8ComponentSyncState(metadata.sync_state),
        downloadedAt=metadata.downloaded_at,
        installed=metadata.installed,
        subscribed=metadata.subscribed,
    )


def _payload_str(payload: JsonObject, key: str) -> str:
    return str(payload[key])


def _payload_optional_str(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _payload_json_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object field: {key}")
    return cast(JsonObject, value)


def _payload_string_list(payload: JsonObject, key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    return json_string_list_loads(value)
