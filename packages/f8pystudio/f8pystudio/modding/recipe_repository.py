from __future__ import annotations

import json
from pathlib import Path
import zlib

from f8pysdk.codec import dump_json, validate_as
from sqlalchemy import delete, insert, select, update

from f8pystudio.assets.common import (
    canonicalize_iso_utc,
    json_object_loads,
    json_string_list_loads,
    mapping_optional_int,
    mapping_optional_str,
    mapping_str,
    new_asset_id,
    now_iso,
    stable_json_dumps,
)
from f8pystudio.assets.db import AssetsDatabase, modding_recipe_drafts_local_table

from .models import (
    F8ModdingRecipeDraftEntry,
    F8ModdingRecipeRecord,
    MODDING_RECIPE_SCHEMA_VERSION,
    SUPPORTED_MODDING_RECIPE_SCHEMA_VERSIONS,
    ModdingRecipeDraftOriginKind,
)
from .redaction import sanitized_recipe_content, validate_no_absolute_local_paths


class ModdingRecipeDraftService:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()

    @property
    def db_path(self) -> Path:
        return self._db.path

    def list_drafts(self) -> list[F8ModdingRecipeDraftEntry]:
        statement = (
            select(
                modding_recipe_drafts_local_table.c.draft_id,
                modding_recipe_drafts_local_table.c.name,
                modding_recipe_drafts_local_table.c.description,
                modding_recipe_drafts_local_table.c.tags_json,
                modding_recipe_drafts_local_table.c.content,
                modding_recipe_drafts_local_table.c.last_target_path,
                modding_recipe_drafts_local_table.c.origin_kind,
                modding_recipe_drafts_local_table.c.publish_target_asset_id,
                modding_recipe_drafts_local_table.c.publish_base_remote_version_number,
                modding_recipe_drafts_local_table.c.created_at,
                modding_recipe_drafts_local_table.c.updated_at,
            )
            .order_by(modding_recipe_drafts_local_table.c.updated_at.desc(), modding_recipe_drafts_local_table.c.draft_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_draft_from_row(row) for row in rows]

    def draft(self, draft_id: str) -> F8ModdingRecipeDraftEntry | None:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            return None
        statement = select(
            modding_recipe_drafts_local_table.c.draft_id,
            modding_recipe_drafts_local_table.c.name,
            modding_recipe_drafts_local_table.c.description,
            modding_recipe_drafts_local_table.c.tags_json,
            modding_recipe_drafts_local_table.c.content,
            modding_recipe_drafts_local_table.c.last_target_path,
            modding_recipe_drafts_local_table.c.origin_kind,
            modding_recipe_drafts_local_table.c.publish_target_asset_id,
            modding_recipe_drafts_local_table.c.publish_base_remote_version_number,
            modding_recipe_drafts_local_table.c.created_at,
            modding_recipe_drafts_local_table.c.updated_at,
        ).where(modding_recipe_drafts_local_table.c.draft_id == normalized_draft_id)
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        return _draft_from_row(row)

    def save_draft(self, draft: F8ModdingRecipeDraftEntry) -> F8ModdingRecipeDraftEntry:
        normalized = _normalized_draft(draft)
        values = _draft_db_values(normalized)
        statement = select(modding_recipe_drafts_local_table.c.draft_id).where(
            modding_recipe_drafts_local_table.c.draft_id == normalized.draftId
        )
        with self._db.begin_sqla() as conn:
            existing = conn.execute(statement).mappings().first()
            if existing is None:
                conn.execute(insert(modding_recipe_drafts_local_table).values(**values))
            else:
                conn.execute(
                    update(modding_recipe_drafts_local_table)
                    .where(modding_recipe_drafts_local_table.c.draft_id == normalized.draftId)
                    .values(**values)
                )
        return normalized

    def create_draft_from_record(
        self,
        record: F8ModdingRecipeRecord,
        *,
        origin_kind: ModdingRecipeDraftOriginKind | None = ModdingRecipeDraftOriginKind.new,
        publish_target_asset_id: str | None = None,
        publish_base_remote_version_number: int | None = None,
        draft_id: str | None = None,
    ) -> F8ModdingRecipeDraftEntry:
        draft_identifier = str(draft_id or record.recipeId or "").strip() or new_asset_id()
        timestamp = now_iso()
        payload = {
            **dump_json(record, mode="json"),
            "recipeId": draft_identifier,
            "updatedAt": timestamp,
            "createdAt": str(record.createdAt or timestamp),
        }
        normalized_record = validate_as(F8ModdingRecipeRecord, payload)
        return self.save_draft(
            F8ModdingRecipeDraftEntry(
                draftId=draft_identifier,
                record=normalized_record,
                originKind=origin_kind,
                publishTargetAssetId=None if publish_target_asset_id is None else str(publish_target_asset_id),
                publishBaseRemoteVersionNumber=publish_base_remote_version_number,
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
                delete(modding_recipe_drafts_local_table).where(
                    modding_recipe_drafts_local_table.c.draft_id == normalized_draft_id
                )
            )
        return bool(result.rowcount)

    def export_draft(self, draft_id: str, path: str) -> Path:
        draft = self.draft(draft_id)
        if draft is None:
            raise FileNotFoundError(f"Modding recipe draft not found: {draft_id}")
        out_path = Path(str(path or "").strip())
        if not str(out_path):
            raise ValueError("Export path is empty")
        if out_path.suffix.lower() != ".json":
            out_path = out_path.with_suffix(".json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sanitized_record = validate_as(
            F8ModdingRecipeRecord,
            {
                **dump_json(draft.record, mode="json"),
                "content": sanitized_recipe_content(draft.record.content),
                "lastTargetPath": "",
            },
        )
        payload = {
            "assetType": "modding_recipe",
            "recipeId": sanitized_record.recipeId,
            "versionNumber": 1,
            "record": dump_json(sanitized_record, mode="json"),
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return out_path


def validate_recipe_content_for_publish(content: dict[str, object]) -> None:
    schema_version = str(content.get("schemaVersion") or "").strip()
    if schema_version not in SUPPORTED_MODDING_RECIPE_SCHEMA_VERSIONS:
        raise ValueError(
            "unsupported modding recipe schemaVersion; expected one of "
            + ", ".join(sorted(SUPPORTED_MODDING_RECIPE_SCHEMA_VERSIONS))
        )
    validate_no_absolute_local_paths(content)


def _normalized_draft(draft: F8ModdingRecipeDraftEntry) -> F8ModdingRecipeDraftEntry:
    draft_id = str(draft.draftId or draft.record.recipeId or "").strip() or new_asset_id()
    timestamp = now_iso()
    created_at = canonicalize_iso_utc(draft.createdAt or draft.record.createdAt or timestamp)
    content = dict(draft.record.content)
    if str(content.get("schemaVersion") or "") not in SUPPORTED_MODDING_RECIPE_SCHEMA_VERSIONS:
        raise ValueError(
            "unsupported modding recipe schemaVersion; expected one of "
            + ", ".join(sorted(SUPPORTED_MODDING_RECIPE_SCHEMA_VERSIONS))
        )
    record_payload = {
        **dump_json(draft.record, mode="json"),
        "recipeId": draft_id,
        "content": content,
        "createdAt": created_at,
        "updatedAt": timestamp,
    }
    normalized_record = validate_as(F8ModdingRecipeRecord, record_payload)
    return F8ModdingRecipeDraftEntry(
        draftId=draft_id,
        record=normalized_record,
        originKind=draft.originKind,
        publishTargetAssetId=(
            None if draft.publishTargetAssetId is None else str(draft.publishTargetAssetId).strip() or None
        ),
        publishBaseRemoteVersionNumber=(
            None if draft.publishBaseRemoteVersionNumber is None else int(draft.publishBaseRemoteVersionNumber)
        ),
        createdAt=created_at,
        updatedAt=timestamp,
    )


def _draft_db_values(draft: F8ModdingRecipeDraftEntry) -> dict[str, object]:
    return {
        "draft_id": draft.draftId,
        "name": str(draft.record.name),
        "description": str(draft.record.description),
        "tags_json": stable_json_dumps(list(draft.record.tags or [])),
        "content": _compress_content(stable_json_dumps(draft.record.content)),
        "last_target_path": str(draft.record.lastTargetPath or ""),
        "origin_kind": None if draft.originKind is None else draft.originKind.value,
        "publish_target_asset_id": draft.publishTargetAssetId,
        "publish_base_remote_version_number": draft.publishBaseRemoteVersionNumber,
        "created_at": str(draft.createdAt),
        "updated_at": str(draft.updatedAt),
    }


def _draft_from_row(row: object) -> F8ModdingRecipeDraftEntry:
    if not isinstance(row, dict):
        row = dict(row)
    row_mapping: dict[object, object] = row
    draft_id = mapping_str(row_mapping, "draft_id")
    created_at = canonicalize_iso_utc(mapping_str(row_mapping, "created_at"))
    updated_at = canonicalize_iso_utc(mapping_str(row_mapping, "updated_at"))
    content = json_object_loads(_decompress_content(row_mapping.get("content")))
    record = F8ModdingRecipeRecord(
        recipeId=draft_id,
        name=mapping_str(row_mapping, "name"),
        description=mapping_str(row_mapping, "description"),
        tags=json_string_list_loads(row_mapping.get("tags_json")),
        content=content,
        lastTargetPath=str(row_mapping.get("last_target_path") or ""),
        createdAt=created_at,
        updatedAt=updated_at,
    )
    return F8ModdingRecipeDraftEntry(
        draftId=draft_id,
        record=record,
        originKind=_origin_kind_from_db_value(mapping_optional_str(row_mapping, "origin_kind")),
        publishTargetAssetId=mapping_optional_str(row_mapping, "publish_target_asset_id"),
        publishBaseRemoteVersionNumber=mapping_optional_int(row_mapping, "publish_base_remote_version_number"),
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _origin_kind_from_db_value(value: str | None) -> ModdingRecipeDraftOriginKind | None:
    if value is None:
        return None
    return ModdingRecipeDraftOriginKind(value)


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
    "ModdingRecipeDraftService",
    "validate_recipe_content_for_publish",
]
