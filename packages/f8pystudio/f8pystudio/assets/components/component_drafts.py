from __future__ import annotations

import json
import zlib
from pathlib import Path

from sqlalchemy import delete, insert, select, update
from f8pysdk.codec import dump_json, validate_as

from ..common import (
    json_object_loads,
    json_string_list_loads,
    mapping_optional_str,
    mapping_str,
    new_asset_id,
    stable_json_dumps,
)
from ..db import (
    AssetsDatabase,
    component_drafts_local_table,
)
from .component_models import (
    F8ComponentDraftEntry,
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentSyncState,
    component_now_iso,
)

class ComponentDraftService:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def list_drafts(self) -> list[F8ComponentDraftEntry]:
        statement = (
            select(
                component_drafts_local_table.c.draft_id,
                component_drafts_local_table.c.name,
                component_drafts_local_table.c.description,
                component_drafts_local_table.c.tags_json,
                component_drafts_local_table.c.schema_version,
                component_drafts_local_table.c.content,
                component_drafts_local_table.c.origin_kind,
                component_drafts_local_table.c.publish_target_asset_id,
                component_drafts_local_table.c.publish_base_remote_revision,
                component_drafts_local_table.c.created_at,
                component_drafts_local_table.c.updated_at,
            )
            .order_by(component_drafts_local_table.c.updated_at.desc(), component_drafts_local_table.c.draft_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_draft_from_row(row) for row in rows]

    def list_catalog_entries(self) -> list[F8ComponentEntry]:
        return [draft_as_catalog_entry(draft) for draft in self.list_drafts()]

    def draft(self, draft_id: str) -> F8ComponentDraftEntry | None:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            return None
        statement = select(
            component_drafts_local_table.c.draft_id,
            component_drafts_local_table.c.name,
            component_drafts_local_table.c.description,
            component_drafts_local_table.c.tags_json,
            component_drafts_local_table.c.schema_version,
            component_drafts_local_table.c.content,
            component_drafts_local_table.c.origin_kind,
            component_drafts_local_table.c.publish_target_asset_id,
            component_drafts_local_table.c.publish_base_remote_revision,
            component_drafts_local_table.c.created_at,
            component_drafts_local_table.c.updated_at,
        ).where(component_drafts_local_table.c.draft_id == normalized_draft_id)
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        return _draft_from_row(row)

    def draft_for_publish_target(self, asset_id: str) -> F8ComponentDraftEntry | None:
        normalized_asset_id = str(asset_id or "").strip()
        if not normalized_asset_id:
            return None
        statement = (
            select(
                component_drafts_local_table.c.draft_id,
                component_drafts_local_table.c.name,
                component_drafts_local_table.c.description,
                component_drafts_local_table.c.tags_json,
                component_drafts_local_table.c.schema_version,
                component_drafts_local_table.c.content,
                component_drafts_local_table.c.origin_kind,
                component_drafts_local_table.c.publish_target_asset_id,
                component_drafts_local_table.c.publish_base_remote_revision,
                component_drafts_local_table.c.created_at,
                component_drafts_local_table.c.updated_at,
            )
            .where(component_drafts_local_table.c.publish_target_asset_id == normalized_asset_id)
            .order_by(component_drafts_local_table.c.updated_at.desc())
        )
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        return _draft_from_row(row)

    def save_draft(self, draft: F8ComponentDraftEntry) -> F8ComponentDraftEntry:
        normalized = _normalized_component_draft(draft)
        values = _draft_db_values(normalized)
        statement = select(component_drafts_local_table.c.draft_id).where(
            component_drafts_local_table.c.draft_id == normalized.draftId
        )
        with self._db.begin_sqla() as conn:
            existing = conn.execute(statement).mappings().first()
            if existing is None:
                conn.execute(insert(component_drafts_local_table).values(**values))
            else:
                conn.execute(
                    update(component_drafts_local_table)
                    .where(component_drafts_local_table.c.draft_id == normalized.draftId)
                    .values(**values)
                )
        return normalized

    def create_draft_from_record(
        self,
        record: F8ComponentRecord,
        *,
        origin_kind: F8ComponentDraftOriginKind | None,
        publish_target_asset_id: str | None,
        publish_base_remote_revision: str | None,
        draft_id: str | None = None,
    ) -> F8ComponentDraftEntry:
        draft_identifier = str(draft_id or "").strip() or new_asset_id()
        timestamp = component_now_iso()
        payload = {
            **dump_json(record, mode="json"),
            "componentId": draft_identifier,
            "createdAt": str(record.createdAt or timestamp),
            "updatedAt": timestamp,
        }
        normalized_record = validate_as(F8ComponentRecord, payload)
        return self.save_draft(
            F8ComponentDraftEntry(
                draftId=draft_identifier,
                record=normalized_record,
                originKind=origin_kind,
                publishTargetAssetId=None if publish_target_asset_id is None else str(publish_target_asset_id),
                publishBaseRemoteRevision=(
                    None if publish_base_remote_revision is None else str(publish_base_remote_revision)
                ),
                createdAt=str(normalized_record.createdAt),
                updatedAt=str(normalized_record.updatedAt),
            )
        )

    def delete_draft(self, draft_id: str) -> bool:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            return False
        with self._db.begin_sqla() as conn:
            result = conn.execute(
                delete(component_drafts_local_table).where(component_drafts_local_table.c.draft_id == normalized_draft_id)
            )
        return bool(result.rowcount)

def draft_as_catalog_entry(draft: F8ComponentDraftEntry) -> F8ComponentEntry:
    return F8ComponentEntry(
        record=draft.record,
        source=F8ComponentSourceKind.local,
        syncState=F8ComponentSyncState.local_only,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
        draftOriginKind=draft.originKind,
        draftOriginAssetId=draft.publishTargetAssetId,
        draftOriginRevision=draft.publishBaseRemoteRevision,
    )


def _normalized_component_draft(draft: F8ComponentDraftEntry) -> F8ComponentDraftEntry:
    draft_id = str(draft.draftId or "").strip() or new_asset_id()
    timestamp = component_now_iso()
    created_at = str(draft.createdAt or draft.record.createdAt or timestamp)
    updated_at = timestamp
    record_payload = {
        **dump_json(draft.record, mode="json"),
        "componentId": draft_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
    }
    normalized_record = validate_as(F8ComponentRecord, record_payload)
    return F8ComponentDraftEntry(
        draftId=draft_id,
        record=normalized_record,
        originKind=draft.originKind,
        publishTargetAssetId=(
            None if draft.publishTargetAssetId is None else str(draft.publishTargetAssetId).strip() or None
        ),
        publishBaseRemoteRevision=(
            None
            if draft.publishBaseRemoteRevision is None
            else str(draft.publishBaseRemoteRevision).strip() or None
        ),
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _draft_db_values(draft: F8ComponentDraftEntry) -> dict[str, object]:
    return {
        "draft_id": draft.draftId,
        "name": str(draft.record.name),
        "description": str(draft.record.description),
        "tags_json": stable_json_dumps(list(draft.record.tags or [])),
        "schema_version": str(draft.record.schemaVersion),
        "content": _compress_content(stable_json_dumps(draft.record.content)),
        "origin_kind": None if draft.originKind is None else draft.originKind.value,
        "publish_target_asset_id": draft.publishTargetAssetId,
        "publish_base_remote_revision": draft.publishBaseRemoteRevision,
        "created_at": str(draft.createdAt),
        "updated_at": str(draft.updatedAt),
    }


def _draft_from_row(row: object) -> F8ComponentDraftEntry:
    if not isinstance(row, dict):
        row = dict(row)
    row_mapping: dict[object, object] = row
    draft_id = mapping_str(row_mapping, "draft_id")
    created_at = mapping_str(row_mapping, "created_at")
    updated_at = mapping_str(row_mapping, "updated_at")
    record = F8ComponentRecord(
        componentId=draft_id,
        name=mapping_str(row_mapping, "name"),
        description=mapping_str(row_mapping, "description"),
        tags=json_string_list_loads(row_mapping.get("tags_json")),
        schemaVersion=mapping_str(row_mapping, "schema_version"),
        content=json_object_loads(_decompress_content(row_mapping.get("content"))),
        createdAt=created_at,
        updatedAt=updated_at,
    )
    origin_kind = _origin_kind_from_db_value(mapping_optional_str(row_mapping, "origin_kind"))
    return F8ComponentDraftEntry(
        draftId=draft_id,
        record=record,
        originKind=origin_kind,
        publishTargetAssetId=mapping_optional_str(row_mapping, "publish_target_asset_id"),
        publishBaseRemoteRevision=mapping_optional_str(row_mapping, "publish_base_remote_revision"),
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _origin_kind_from_db_value(value: str | None) -> F8ComponentDraftOriginKind | None:
    if value is None:
        return None
    return F8ComponentDraftOriginKind(value)


def _compress_content(value: str) -> bytes:
    return zlib.compress(str(value).encode("utf-8"), level=9)


def _decompress_content(value: object) -> str:
    if value is None:
        return "{}"
    raw_value = bytes(value)
    try:
        return zlib.decompress(raw_value).decode("utf-8")
    except zlib.error:
        try:
            return zlib.decompress(raw_value, wbits=31).decode("utf-8")
        except zlib.error:
            return raw_value.decode("utf-8")


__all__ = [
    "ComponentDraftService",
    "draft_as_catalog_entry",
]
