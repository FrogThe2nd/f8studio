from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
from typing import cast
import zlib

from sqlalchemy import delete, insert, select
from f8pysdk.codec import copy_model

from ..common import (
    canonicalize_iso_utc,
    json_object_loads,
    json_string_list_loads,
    mapping_optional_str,
    mapping_str,
    stable_json_dumps,
)
from ..common.remote_cache_common import RemoteCacheMetadata, remote_cache_metadata_from_fields
from ..db import AssetsDatabase, component_remote_cache_table
from .component_drafts import ComponentDraftService, draft_as_catalog_entry
from .component_events import emit_components_changed
from .component_models import (
    F8ComponentDraftEntry,
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentLocalVersionSummary,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from .official_components import bundled_official_component_entries, component_entry_is_bundled_official

logger = logging.getLogger(__name__)


class LocalComponentProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._draft_service = ComponentDraftService(db_path=db_path)

    @property
    def db_path(self) -> Path:
        return self._draft_service.db_path

    def load_entries(self) -> list[F8ComponentEntry]:
        return self._draft_service.list_catalog_entries()

    def save_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        origin_kind = entry.draftOriginKind or F8ComponentDraftOriginKind.new
        existing_draft = self._draft_service.draft(str(entry.record.componentId))
        if existing_draft is None:
            saved = self._draft_service.create_draft_from_record(
                entry.record,
                origin_kind=origin_kind,
                publish_target_asset_id=entry.draftOriginAssetId,
                publish_base_remote_version_number=entry.draftOriginVersionNumber,
                draft_id=str(entry.record.componentId),
            )
        else:
            saved = self._draft_service.save_draft(
                F8ComponentDraftEntry(
                    draftId=existing_draft.draftId,
                    record=entry.record,
                    originKind=origin_kind,
                    publishTargetAssetId=entry.draftOriginAssetId or existing_draft.publishTargetAssetId,
                    publishBaseRemoteVersionNumber=entry.draftOriginVersionNumber
                    or existing_draft.publishBaseRemoteVersionNumber,
                    createdAt=existing_draft.createdAt,
                    updatedAt=existing_draft.updatedAt,
                )
            )
        return draft_as_catalog_entry(saved)

    def delete_entry(self, component_id: str) -> bool:
        return self._draft_service.delete_draft(component_id)


class RemoteComponentCacheProvider:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    @property
    def db_path(self) -> Path:
        return self._db.path

    def load_entries(self) -> list[F8ComponentEntry]:
        statement = select(
            component_remote_cache_table.c.component_id,
            component_remote_cache_table.c.name,
            component_remote_cache_table.c.description,
            component_remote_cache_table.c.tags_json,
            component_remote_cache_table.c.created_at,
            component_remote_cache_table.c.updated_at,
            component_remote_cache_table.c.source,
            component_remote_cache_table.c.visibility,
            component_remote_cache_table.c.owner_user_id,
            component_remote_cache_table.c.owner_display_name,
            component_remote_cache_table.c.remote_version_number,
            component_remote_cache_table.c.downloaded_at,
            component_remote_cache_table.c.installed,
            component_remote_cache_table.c.has_cached_content,
            component_remote_cache_table.c.subscribed,
            component_remote_cache_table.c.content,
        ).order_by(component_remote_cache_table.c.component_id)
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ComponentEntry] = []
        invalid_found = False
        for row in rows:
            row_mapping = _row_mapping(row)
            try:
                metadata = RemoteCacheMetadata.from_row(row_mapping)
                entry = _component_entry_from_remote_cache_row(row_mapping, metadata)
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
            self.save_entries(out)
        return out

    def save_entries(self, entries: list[F8ComponentEntry]) -> None:
        unique_entries = _unique_remote_component_entries_by_id(entries)
        with self._db.begin_sqla() as conn:
            conn.execute(delete(component_remote_cache_table))
            for entry in unique_entries:
                component_id = str(entry.record.componentId or "").strip()
                if not component_id:
                    continue
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
                    insert(component_remote_cache_table).values(
                        component_id=component_id,
                        name=str(entry.record.name),
                        description=str(entry.record.description),
                        tags_json=stable_json_dumps(list(entry.record.tags or [])),
                        created_at=str(entry.record.createdAt),
                        updated_at=str(entry.record.updatedAt),
                        source=metadata.source,
                        visibility=metadata.visibility,
                        owner_user_id=metadata.owner_user_id,
                        owner_display_name=metadata.owner_display_name,
                        remote_version_number=metadata.remote_version_number,
                        downloaded_at=metadata.downloaded_at,
                        installed=1 if metadata.installed else 0,
                        has_cached_content=1 if component_entry_has_cached_content(entry) else 0,
                        subscribed=1 if metadata.subscribed else 0,
                        content=_compress_content(
                            stable_json_dumps(entry.record.content if component_entry_has_cached_content(entry) else {})
                        ),
                    )
                )


class ComponentCatalogService:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        remote_provider: RemoteComponentCacheProvider | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else AssetsDatabase().path
        self._remote_provider = (
            RemoteComponentCacheProvider(self._db_path) if remote_provider is None else remote_provider
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load_all_entries(self) -> list[F8ComponentEntry]:
        merged: dict[str, F8ComponentEntry] = {}
        for source_entries in [
            bundled_official_component_entries(),
            self._remote_provider.load_entries(),
            ComponentDraftService(db_path=self._db_path).list_catalog_entries(),
        ]:
            for entry in source_entries:
                component_id = str(entry.record.componentId or "").strip()
                if component_id:
                    merged[component_id] = entry
        return sorted(merged.values(), key=_entry_sort_key)

    def entry(self, component_id: str, *, include_uninstalled: bool = True) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        draft = ComponentDraftService(db_path=self._db_path).draft(normalized_component_id)
        if draft is not None:
            return draft_as_catalog_entry(draft)
        entry = self.remote_entry(normalized_component_id)
        if entry is None:
            return None
        if include_uninstalled or component_entry_is_installed(entry):
            return entry
        return None

    def list_entries(self, *, include_uninstalled: bool = False) -> list[F8ComponentEntry]:
        entries = self.load_all_entries()
        if include_uninstalled:
            return entries
        return [entry for entry in entries if component_entry_is_installed(entry)]

    def upsert_local_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        draft_service = ComponentDraftService(db_path=self._db_path)
        existing_draft = draft_service.draft(str(entry.record.componentId))
        origin_kind = entry.draftOriginKind or (
            existing_draft.originKind if existing_draft is not None else F8ComponentDraftOriginKind.new
        )
        if existing_draft is None:
            saved = draft_service.create_draft_from_record(
                entry.record,
                origin_kind=origin_kind,
                publish_target_asset_id=entry.draftOriginAssetId,
                publish_base_remote_version_number=entry.draftOriginVersionNumber,
                draft_id=str(entry.record.componentId),
            )
        else:
            saved = draft_service.save_draft(
                F8ComponentDraftEntry(
                    draftId=existing_draft.draftId,
                    record=entry.record,
                    originKind=origin_kind,
                    publishTargetAssetId=entry.draftOriginAssetId or existing_draft.publishTargetAssetId,
                    publishBaseRemoteVersionNumber=entry.draftOriginVersionNumber
                    or existing_draft.publishBaseRemoteVersionNumber,
                    createdAt=existing_draft.createdAt,
                    updatedAt=existing_draft.updatedAt,
                )
            )
        emit_components_changed()
        return draft_as_catalog_entry(saved)

    def delete_local_entry(self, component_id: str) -> bool:
        deleted = ComponentDraftService(db_path=self._db_path).delete_draft(component_id)
        if deleted:
            emit_components_changed()
        return deleted

    def list_local_versions(self, component_id: str) -> list[F8ComponentLocalVersionSummary]:
        del component_id
        return []

    def local_version_record(self, component_id: str, version_number: int) -> F8ComponentRecord | None:
        del component_id
        del version_number
        return None

    def replace_remote_entries(self, entries: list[F8ComponentEntry], *, emit_changed: bool = True) -> None:
        normalized_entries = _unique_remote_component_entries_by_id(
            [
                _normalize_remote_component_entry_for_storage(entry)
                for entry in entries
                if not component_entry_is_bundled_official(entry)
            ]
        )
        current_entries = self._remote_provider.load_entries()
        if current_entries == normalized_entries:
            return
        self._remote_provider.save_entries(normalized_entries)
        if emit_changed:
            emit_components_changed()

    def load_remote_entries(self) -> list[F8ComponentEntry]:
        merged = {
            str(entry.record.componentId): entry
            for entry in bundled_official_component_entries()
            if str(entry.record.componentId).strip()
        }
        for entry in self._remote_provider.load_entries():
            component_id = str(entry.record.componentId or "").strip()
            if component_id:
                merged[component_id] = entry
        return sorted(merged.values(), key=_entry_sort_key)

    def load_persisted_remote_entries(self) -> list[F8ComponentEntry]:
        """Return only mutable remote-cache rows, excluding package resources."""

        return self._remote_provider.load_entries()

    def remote_entry(self, component_id: str) -> F8ComponentEntry | None:
        normalized_component_id = str(component_id or "").strip()
        if not normalized_component_id:
            return None
        for entry in self.load_remote_entries():
            if str(entry.record.componentId or "").strip() == normalized_component_id:
                return entry
        return None

    def install_remote_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        downloaded_at = entry.downloadedAt
        if component_entry_has_cached_content(entry) and not downloaded_at:
            downloaded_at = component_now_iso()
        installed_entry = copy_model(
            entry,
            update={
                "installed": component_entry_has_cached_content(entry),
                "hasCachedContent": component_entry_has_cached_content(entry),
                "downloadedAt": downloaded_at,
            },
        )
        return self._save_remote_entry(installed_entry)

    def cache_remote_entry(self, entry: F8ComponentEntry, *, emit_changed: bool = True) -> F8ComponentEntry:
        downloaded_at = entry.downloadedAt
        if component_entry_has_cached_content(entry) and not downloaded_at:
            downloaded_at = component_now_iso()
        cached_entry = copy_model(
            entry,
            update={
                "installed": False,
                "hasCachedContent": component_entry_has_cached_content(entry),
                "downloadedAt": downloaded_at,
            },
        )
        return self._save_remote_entry(cached_entry, emit_changed=emit_changed)

    def _save_remote_entry(self, entry: F8ComponentEntry, *, emit_changed: bool = True) -> F8ComponentEntry:
        normalized_entry = _normalize_remote_component_entry_for_storage(entry)
        current = self._remote_provider.load_entries()
        existing_entry: F8ComponentEntry | None = None
        out: list[F8ComponentEntry] = []
        found = False
        for current_entry in current:
            if str(current_entry.record.componentId) == str(entry.record.componentId):
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
            emit_components_changed()
        return normalized_entry

    def uninstall_remote_entry(self, component_id: str) -> F8ComponentEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8ComponentEntry] = []
        target: F8ComponentEntry | None = None
        normalized_component_id = str(component_id or "").strip()
        for entry in current:
            if str(entry.record.componentId or "").strip() != normalized_component_id:
                out.append(entry)
                continue
            target = copy_model(
                entry,
                update={
                    "record": copy_model(entry.record, update={"content": {}}),
                    "installed": False,
                    "hasCachedContent": False,
                    "downloadedAt": None,
                },
            )
            out.append(target)
        if target is None:
            return None
        self._remote_provider.save_entries(out)
        emit_components_changed()
        return target

    def delete_remote_entry(self, component_id: str) -> bool:
        current = self._remote_provider.load_entries()
        normalized_component_id = str(component_id or "").strip()
        out = [entry for entry in current if str(entry.record.componentId or "").strip() != normalized_component_id]
        if len(out) == len(current):
            return False
        self._remote_provider.save_entries(out)
        emit_components_changed()
        return True

    def mark_conflict(self, component_id: str, *, remote_version_number: int | None) -> F8ComponentEntry | None:
        current = self._remote_provider.load_entries()
        out: list[F8ComponentEntry] = []
        target: F8ComponentEntry | None = None
        for entry in current:
            if str(entry.record.componentId) == str(component_id):
                target = copy_model(entry, update={"remoteVersionNumber": remote_version_number})
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


def _component_record_from_row(row: Mapping[object, object]) -> F8ComponentRecord:
    return F8ComponentRecord(
        componentId=mapping_str(row, "component_id"),
        name=mapping_str(row, "name"),
        description=mapping_str(row, "description"),
        tags=json_string_list_loads(row.get("tags_json")),
        content=json_object_loads(_decompress_content(row.get("content"))),
        createdAt=canonicalize_iso_utc(mapping_str(row, "created_at")),
        updatedAt=canonicalize_iso_utc(mapping_str(row, "updated_at")),
    )


def _component_entry_from_remote_cache_row(
    row: Mapping[object, object], metadata: RemoteCacheMetadata
) -> F8ComponentEntry:
    visibility = None if metadata.visibility is None else F8ComponentVisibility(metadata.visibility)
    return F8ComponentEntry(
        record=_component_record_from_row(row),
        source=F8ComponentSourceKind(metadata.source),
        visibility=visibility,
        ownerUserId=metadata.owner_user_id,
        ownerDisplayName=metadata.owner_display_name,
        remoteVersionNumber=metadata.remote_version_number,
        downloadedAt=None if metadata.downloaded_at is None else canonicalize_iso_utc(metadata.downloaded_at),
        installed=metadata.installed,
        hasCachedContent=_sqlite_row_bool(row, "has_cached_content"),
        subscribed=metadata.subscribed,
    )


def _compress_content(json_str: str) -> bytes:
    return zlib.compress(json_str.encode("utf-8"), level=6, wbits=31)


def _decompress_content(data: object) -> str:
    if data is None:
        return "{}"
    raw = bytes(data)
    try:
        return zlib.decompress(raw, wbits=31).decode("utf-8")
    except zlib.error:
        return raw.decode("utf-8", errors="replace")


def _sqlite_row_bool(row: Mapping[object, object], key: str) -> bool:
    return bool(int(str(row[key])))


def component_entry_has_cached_content(entry: F8ComponentEntry) -> bool:
    if entry.source == F8ComponentSourceKind.local:
        return True
    if entry.hasCachedContent is not None:
        return bool(entry.hasCachedContent)
    return _component_content_is_hydrated(entry.record.content)


def _component_content_is_hydrated(content: Mapping[str, object]) -> bool:
    layout_value = content.get("layout")
    schema_version_value = content.get("schemaVersion")
    return (
        isinstance(layout_value, dict) and isinstance(schema_version_value, str) and bool(schema_version_value.strip())
    )


def component_entry_is_installed(entry: F8ComponentEntry) -> bool:
    if entry.source == F8ComponentSourceKind.local:
        return True
    return bool(entry.installed and component_entry_has_cached_content(entry))


def component_entry_can_hydrate(entry: F8ComponentEntry) -> bool:
    return entry.source in {
        F8ComponentSourceKind.remote_official,
        F8ComponentSourceKind.remote_public,
        F8ComponentSourceKind.remote_private,
    }


def _normalize_remote_component_entry_for_storage(entry: F8ComponentEntry) -> F8ComponentEntry:
    has_cached_content = component_entry_has_cached_content(entry)
    normalized_content = entry.record.content if has_cached_content else {}
    normalized_record = copy_model(
        entry.record,
        update={
            "content": normalized_content,
            "createdAt": canonicalize_iso_utc(entry.record.createdAt),
            "updatedAt": canonicalize_iso_utc(entry.record.updatedAt),
        },
    )
    return copy_model(
        entry,
        update={
            "record": normalized_record,
            "downloadedAt": None if entry.downloadedAt is None else canonicalize_iso_utc(entry.downloadedAt),
            "hasCachedContent": has_cached_content,
        },
    )


def _unique_remote_component_entries_by_id(entries: list[F8ComponentEntry]) -> list[F8ComponentEntry]:
    entries_by_component_id: dict[str, F8ComponentEntry] = {}
    component_ids: list[str] = []
    for entry in entries:
        component_id = str(entry.record.componentId or "").strip()
        if not component_id:
            continue
        if component_id not in entries_by_component_id:
            component_ids.append(component_id)
        entries_by_component_id[component_id] = entry
    return [entries_by_component_id[component_id] for component_id in component_ids]
