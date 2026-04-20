from __future__ import annotations

import zlib
from pathlib import Path

import msgspec
from sqlalchemy import delete, insert, select, update
from f8pysdk.codec import dump_json, validate_as
from f8pysdk.specs import F8VariantRecord

from ..common import (
    json_object_loads,
    json_string_list_loads,
    mapping_optional_str,
    mapping_str,
    new_asset_id,
    stable_json_dumps,
)
from ..db import AssetsDatabase, variant_drafts_local_table
from .variant_models import (
    F8VariantDraftEntry,
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantSourceKind,
    variant_now_iso,
)


class VariantDraftService:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    def list_drafts(self) -> list[F8VariantDraftEntry]:
        statement = (
            select(
                variant_drafts_local_table.c.draft_id,
                variant_drafts_local_table.c.name,
                variant_drafts_local_table.c.description,
                variant_drafts_local_table.c.tags_json,
                variant_drafts_local_table.c.kind,
                variant_drafts_local_table.c.base_node_type,
                variant_drafts_local_table.c.service_class,
                variant_drafts_local_table.c.operator_class,
                variant_drafts_local_table.c.content,
                variant_drafts_local_table.c.origin_kind,
                variant_drafts_local_table.c.publish_target_asset_id,
                variant_drafts_local_table.c.publish_base_remote_revision,
                variant_drafts_local_table.c.created_at,
                variant_drafts_local_table.c.updated_at,
            )
            .order_by(variant_drafts_local_table.c.updated_at.desc(), variant_drafts_local_table.c.draft_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_draft_from_row(row) for row in rows]

    def list_catalog_entries(self) -> list[F8VariantEntry]:
        return [draft_as_catalog_entry(draft) for draft in self.list_drafts()]

    def draft(self, draft_id: str) -> F8VariantDraftEntry | None:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            return None
        statement = select(
            variant_drafts_local_table.c.draft_id,
            variant_drafts_local_table.c.name,
            variant_drafts_local_table.c.description,
            variant_drafts_local_table.c.tags_json,
            variant_drafts_local_table.c.kind,
            variant_drafts_local_table.c.base_node_type,
            variant_drafts_local_table.c.service_class,
            variant_drafts_local_table.c.operator_class,
            variant_drafts_local_table.c.content,
            variant_drafts_local_table.c.origin_kind,
            variant_drafts_local_table.c.publish_target_asset_id,
            variant_drafts_local_table.c.publish_base_remote_revision,
            variant_drafts_local_table.c.created_at,
            variant_drafts_local_table.c.updated_at,
        ).where(variant_drafts_local_table.c.draft_id == normalized_draft_id)
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        return _draft_from_row(row)

    def draft_for_publish_target(self, asset_id: str) -> F8VariantDraftEntry | None:
        normalized_asset_id = str(asset_id or "").strip()
        if not normalized_asset_id:
            return None
        statement = (
            select(
                variant_drafts_local_table.c.draft_id,
                variant_drafts_local_table.c.name,
                variant_drafts_local_table.c.description,
                variant_drafts_local_table.c.tags_json,
                variant_drafts_local_table.c.kind,
                variant_drafts_local_table.c.base_node_type,
                variant_drafts_local_table.c.service_class,
                variant_drafts_local_table.c.operator_class,
                variant_drafts_local_table.c.content,
                variant_drafts_local_table.c.origin_kind,
                variant_drafts_local_table.c.publish_target_asset_id,
                variant_drafts_local_table.c.publish_base_remote_revision,
                variant_drafts_local_table.c.created_at,
                variant_drafts_local_table.c.updated_at,
            )
            .where(variant_drafts_local_table.c.publish_target_asset_id == normalized_asset_id)
            .order_by(variant_drafts_local_table.c.updated_at.desc())
        )
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        return _draft_from_row(row)

    def save_draft(self, draft: F8VariantDraftEntry) -> F8VariantDraftEntry:
        normalized = _normalized_variant_draft(draft)
        values = _draft_db_values(normalized)
        statement = select(variant_drafts_local_table.c.draft_id).where(
            variant_drafts_local_table.c.draft_id == normalized.draftId
        )
        with self._db.begin_sqla() as conn:
            existing = conn.execute(statement).mappings().first()
            if existing is None:
                conn.execute(insert(variant_drafts_local_table).values(**values))
            else:
                conn.execute(
                    update(variant_drafts_local_table)
                    .where(variant_drafts_local_table.c.draft_id == normalized.draftId)
                    .values(**values)
                )
        return normalized

    def create_draft_from_record(
        self,
        record: F8VariantRecord,
        *,
        origin_kind: F8VariantDraftOriginKind | None,
        publish_target_asset_id: str | None,
        publish_base_remote_revision: str | None,
        draft_id: str | None = None,
    ) -> F8VariantDraftEntry:
        draft_identifier = str(draft_id or "").strip() or new_asset_id()
        timestamp = variant_now_iso()
        payload = {
            **dump_json(record, mode="json"),
            "variantId": draft_identifier,
            "createdAt": str(record.createdAt or timestamp),
            "updatedAt": timestamp,
        }
        normalized_record = validate_as(F8VariantRecord, payload)
        return self.save_draft(
            F8VariantDraftEntry(
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
                delete(variant_drafts_local_table).where(variant_drafts_local_table.c.draft_id == normalized_draft_id)
            )
        return bool(result.rowcount)


def draft_as_catalog_entry(draft: F8VariantDraftEntry) -> F8VariantEntry:
    return F8VariantEntry(
        record=draft.record,
        source=F8VariantSourceKind.local,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
        draftOriginKind=draft.originKind,
        draftOriginAssetId=draft.publishTargetAssetId,
        draftOriginRevision=draft.publishBaseRemoteRevision,
    )


def _normalized_variant_draft(draft: F8VariantDraftEntry) -> F8VariantDraftEntry:
    draft_id = str(draft.draftId or "").strip() or new_asset_id()
    timestamp = variant_now_iso()
    created_at = str(draft.createdAt or draft.record.createdAt or timestamp)
    updated_at = timestamp
    record_payload = {
        **dump_json(draft.record, mode="json"),
        "variantId": draft_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
    }
    normalized_record = validate_as(F8VariantRecord, record_payload)
    return F8VariantDraftEntry(
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


def _draft_db_values(draft: F8VariantDraftEntry) -> dict[str, object]:
    return {
        "draft_id": draft.draftId,
        "name": str(draft.record.name),
        "description": str(draft.record.description),
        "tags_json": stable_json_dumps(list(draft.record.tags or [])),
        "kind": str(draft.record.kind.value),
        "base_node_type": str(draft.record.baseNodeType),
        "service_class": str(draft.record.serviceClass),
        "operator_class": (
            None
            if draft.record.operatorClass is None or isinstance(draft.record.operatorClass, msgspec.UnsetType)
            else str(draft.record.operatorClass)
        ),
        "content": _compress_content(stable_json_dumps(draft.record.spec)),
        "origin_kind": None if draft.originKind is None else draft.originKind.value,
        "publish_target_asset_id": draft.publishTargetAssetId,
        "publish_base_remote_revision": draft.publishBaseRemoteRevision,
        "created_at": str(draft.createdAt),
        "updated_at": str(draft.updatedAt),
    }


def _draft_from_row(row: object) -> F8VariantDraftEntry:
    if not isinstance(row, dict):
        row = dict(row)
    row_mapping: dict[object, object] = row
    draft_id = mapping_str(row_mapping, "draft_id")
    created_at = mapping_str(row_mapping, "created_at")
    updated_at = mapping_str(row_mapping, "updated_at")
    record_payload = {
        "variantId": draft_id,
        "kind": mapping_str(row_mapping, "kind"),
        "baseNodeType": mapping_str(row_mapping, "base_node_type"),
        "serviceClass": mapping_str(row_mapping, "service_class"),
        "name": mapping_str(row_mapping, "name"),
        "description": mapping_str(row_mapping, "description"),
        "tags": json_string_list_loads(row_mapping.get("tags_json")),
        "spec": json_object_loads(_decompress_content(row_mapping.get("content"))),
        "createdAt": created_at,
        "updatedAt": updated_at,
    }
    operator_class = _operator_class_from_row(row_mapping)
    if not isinstance(operator_class, msgspec.UnsetType):
        record_payload["operatorClass"] = operator_class
    record = validate_as(F8VariantRecord, record_payload)
    origin_kind = _origin_kind_from_db_value(mapping_optional_str(row_mapping, "origin_kind"))
    return F8VariantDraftEntry(
        draftId=draft_id,
        record=record,
        originKind=origin_kind,
        publishTargetAssetId=mapping_optional_str(row_mapping, "publish_target_asset_id"),
        publishBaseRemoteRevision=mapping_optional_str(row_mapping, "publish_base_remote_revision"),
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _origin_kind_from_db_value(value: str | None) -> F8VariantDraftOriginKind | None:
    if value is None:
        return None
    return F8VariantDraftOriginKind(value)


def _operator_class_from_row(row_mapping: dict[object, object]) -> str | None | msgspec.UnsetType:
    operator_class = mapping_optional_str(row_mapping, "operator_class")
    if operator_class is None and mapping_str(row_mapping, "kind") == "service":
        return msgspec.UNSET
    return operator_class


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
    "VariantDraftService",
    "draft_as_catalog_entry",
]
