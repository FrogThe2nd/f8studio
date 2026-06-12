from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import cast
import zlib

from qtpy import QtCore
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection as SqlAlchemyConnection

from f8pystudio.nodegraph.session_schema import extract_layout
from ..db import AssetsDatabase, project_heads_table, project_versions_table
from ..common import (
    JsonObject,
    json_string_list_loads,
    json_object_loads,
    mapping_int,
    mapping_str,
    new_asset_id,
    now_iso,
    stable_json_dumps,
)
from .project_models import F8ProjectRecord, F8ProjectSummary, F8ProjectVersionSummary


class ProjectStorageService:
    _SETTINGS_GROUP: str = "projects/local_storage/v1"
    _CURRENT_PROJECT_ID_KEY: str = "current_project_id"
    _AUTOSAVE_PROJECT_ID_KEY: str = "autosave_project_id"
    _MAX_PROJECT_HISTORY_VERSIONS: int = 50

    def __init__(self, *, db_path: Path | None = None, settings: QtCore.QSettings | None = None) -> None:
        self._db: AssetsDatabase
        self._db = AssetsDatabase(db_path)
        self._db.ensure_initialized()
        self._settings: QtCore.QSettings
        self._settings = QtCore.QSettings() if settings is None else settings

    def current_project_id(self) -> str:
        return self._value_str(self._CURRENT_PROJECT_ID_KEY)

    def autosave_project_id(self) -> str:
        return self._value_str(self._AUTOSAVE_PROJECT_ID_KEY)

    def set_current_project_id(self, project_id: str) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            self._set_value(self._CURRENT_PROJECT_ID_KEY, "")
            return
        if self.project(normalized_project_id) is None:
            raise FileNotFoundError(f"Project not found: {normalized_project_id}")
        self._set_value(self._CURRENT_PROJECT_ID_KEY, normalized_project_id)

    def list_projects(self) -> list[F8ProjectSummary]:
        statement = (
            select(
                project_heads_table.c.project_id,
                project_heads_table.c.name,
                project_heads_table.c.description,
                project_heads_table.c.tags_json,
                project_heads_table.c.latest_version_number,
                project_heads_table.c.created_at,
                project_heads_table.c.updated_at,
            )
            .order_by(func.lower(project_heads_table.c.name), project_heads_table.c.project_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ProjectSummary] = []
        for row in rows:
            row_mapping = _row_mapping(row)
            tags = json_string_list_loads(row_mapping.get("tags_json"))
            out.append(
                F8ProjectSummary(
                    projectId=mapping_str(row_mapping, "project_id"),
                    name=mapping_str(row_mapping, "name"),
                    description=mapping_str(row_mapping, "description"),
                    tags=tags,
                    latestVersionNumber=mapping_int(row_mapping, "latest_version_number"),
                    createdAt=mapping_str(row_mapping, "created_at"),
                    updatedAt=mapping_str(row_mapping, "updated_at"),
                )
            )
        return out

    def list_projects_by_name(self, name: str) -> list[F8ProjectSummary]:
        normalized_name = _normalize_project_name(name)
        if not normalized_name:
            return []
        statement = (
            select(
                project_heads_table.c.project_id,
                project_heads_table.c.name,
                project_heads_table.c.description,
                project_heads_table.c.tags_json,
                project_heads_table.c.latest_version_number,
                project_heads_table.c.created_at,
                project_heads_table.c.updated_at,
            )
            .where(func.lower(project_heads_table.c.name) == normalized_name.lower())
            .order_by(project_heads_table.c.updated_at.desc(), project_heads_table.c.project_id)
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ProjectSummary] = []
        for row in rows:
            row_mapping = _row_mapping(row)
            out.append(
                F8ProjectSummary(
                    projectId=mapping_str(row_mapping, "project_id"),
                    name=mapping_str(row_mapping, "name"),
                    description=mapping_str(row_mapping, "description"),
                    tags=json_string_list_loads(row_mapping.get("tags_json")),
                    latestVersionNumber=mapping_int(row_mapping, "latest_version_number"),
                    createdAt=mapping_str(row_mapping, "created_at"),
                    updatedAt=mapping_str(row_mapping, "updated_at"),
                )
            )
        return out

    def unique_project_by_name(self, name: str) -> F8ProjectSummary | None:
        projects = self.list_projects_by_name(name)
        if len(projects) == 1:
            return projects[0]
        return None

    def project(self, project_id: str) -> F8ProjectRecord | None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return None
        return self.project_version(normalized_project_id, version_number=None)

    def list_project_versions(self, project_id: str) -> list[F8ProjectVersionSummary]:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return []
        self._prune_project_versions(
            normalized_project_id,
            keep_latest=self._MAX_PROJECT_HISTORY_VERSIONS,
        )
        statement = (
            select(
                project_versions_table.c.project_id,
                project_versions_table.c.version_number,
                project_versions_table.c.created_at,
            )
            .where(project_versions_table.c.project_id == normalized_project_id)
            .order_by(project_versions_table.c.version_number.desc())
        )
        with self._db.connect_sqla() as conn:
            rows = conn.execute(statement).mappings().all()
        out: list[F8ProjectVersionSummary] = []
        for row in rows:
            row_mapping = _row_mapping(row)
            out.append(
                F8ProjectVersionSummary(
                    projectId=mapping_str(row_mapping, "project_id"),
                    versionNumber=mapping_int(row_mapping, "version_number"),
                    createdAt=mapping_str(row_mapping, "created_at"),
                )
            )
        return out

    def project_version(self, project_id: str, version_number: int | None) -> F8ProjectRecord | None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return None
        version_join_condition = project_heads_table.c.latest_version_number if version_number is None else int(version_number)
        statement = (
            select(
                project_heads_table.c.project_id,
                project_heads_table.c.name,
                project_heads_table.c.description,
                project_heads_table.c.tags_json,
                project_heads_table.c.created_at,
                project_heads_table.c.updated_at,
                project_versions_table.c.content,
                project_versions_table.c.created_at.label("version_created_at"),
            )
            .select_from(
                project_heads_table.join(
                    project_versions_table,
                    and_(
                        project_versions_table.c.project_id == project_heads_table.c.project_id,
                        project_versions_table.c.version_number == version_join_condition,
                    ),
                )
            )
            .where(project_heads_table.c.project_id == normalized_project_id)
        )
        with self._db.connect_sqla() as conn:
            row = conn.execute(statement).mappings().first()
        if row is None:
            return None
        row_mapping = _row_mapping(row)
        payload = json_object_loads(_decompress_content(row_mapping.get("content")))
        return F8ProjectRecord(
            projectId=mapping_str(row_mapping, "project_id"),
            name=mapping_str(row_mapping, "name"),
            description=mapping_str(row_mapping, "description"),
            tags=json_string_list_loads(row_mapping.get("tags_json")),
            content=payload,
            createdAt=mapping_str(row_mapping, "created_at"),
            updatedAt=(mapping_str(row_mapping, "version_created_at") if row_mapping.get("version_created_at") is not None else mapping_str(row_mapping, "updated_at")),
        )

    def save_project(
        self,
        *,
        content: JsonObject,
        project_id: str = "",
        name: str,
        description: str,
        tags: list[str],
        set_current: bool = True,
    ) -> F8ProjectRecord:
        normalized_project_id = str(project_id or "").strip() or new_asset_id()
        normalized_name = str(name or "").strip() or "Untitled Project"
        normalized_description = str(description or "")
        normalized_tags = [str(tag).strip() for tag in list(tags or []) if str(tag).strip()]
        serialized_content = stable_json_dumps(content)
        compressed_content = _compress_content(serialized_content)
        timestamp = now_iso()
        existing_statement = select(project_heads_table.c.latest_version_number).where(
            project_heads_table.c.project_id == normalized_project_id
        )
        with self._db.begin_sqla() as conn:
            existing = conn.execute(existing_statement).mappings().first()
            if existing is None:
                version_number = 1
                _ = conn.execute(
                    insert(project_heads_table).values(
                        project_id=normalized_project_id,
                        name=normalized_name,
                        description=normalized_description,
                        tags_json=stable_json_dumps(normalized_tags),
                        latest_version_number=version_number,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                _ = conn.execute(
                    insert(project_versions_table).values(
                        project_id=normalized_project_id,
                        version_number=version_number,
                        content=compressed_content,
                        created_at=timestamp,
                    )
                )
            else:
                existing_mapping = _row_mapping(existing)
                version_number = mapping_int(existing_mapping, "latest_version_number")
                latest_content = self._latest_project_content(
                    conn,
                    project_id=normalized_project_id,
                    version_number=version_number,
                )
                metadata_changed = (
                    self._project_metadata_changed(
                        conn,
                        project_id=normalized_project_id,
                        name=normalized_name,
                        description=normalized_description,
                        tags=normalized_tags,
                    )
                )
                if latest_content == serialized_content:
                    if metadata_changed:
                        _ = conn.execute(
                            update(project_heads_table)
                            .where(project_heads_table.c.project_id == normalized_project_id)
                            .values(
                                name=normalized_name,
                                description=normalized_description,
                                tags_json=stable_json_dumps(normalized_tags),
                                updated_at=timestamp,
                            )
                        )
                else:
                    version_number += 1
                    _ = conn.execute(
                        update(project_heads_table)
                        .where(project_heads_table.c.project_id == normalized_project_id)
                        .values(
                            name=normalized_name,
                            description=normalized_description,
                            tags_json=stable_json_dumps(normalized_tags),
                            latest_version_number=version_number,
                            updated_at=timestamp,
                        )
                    )
                    _ = conn.execute(
                        insert(project_versions_table).values(
                            project_id=normalized_project_id,
                            version_number=version_number,
                            content=compressed_content,
                            created_at=timestamp,
                        )
                    )
            self._prune_project_versions_in_connection(
                conn,
                project_id=normalized_project_id,
                keep_latest=self._MAX_PROJECT_HISTORY_VERSIONS,
            )
        if set_current:
            self._set_value(self._CURRENT_PROJECT_ID_KEY, normalized_project_id)
        saved_project = self.project(normalized_project_id)
        if saved_project is None:
            raise FileNotFoundError(f"Project not found after save: {normalized_project_id}")
        return saved_project

    def save_last_project(self, *, content: JsonObject, default_name: str = "Auto Saved Session") -> F8ProjectRecord:
        current_project_id = self.current_project_id()
        project = self.project(current_project_id)
        if project is not None:
            return self.save_project(
                content=content,
                project_id=project.projectId,
                name=project.name,
                description=project.description,
                tags=list(project.tags),
                set_current=True,
            )
        autosave_project_id = self.autosave_project_id()
        autosave_project = self.project(autosave_project_id)
        if autosave_project is not None:
            saved = self.save_project(
                content=content,
                project_id=autosave_project.projectId,
                name=autosave_project.name,
                description=autosave_project.description,
                tags=list(autosave_project.tags),
                set_current=False,
            )
            self._set_value(self._AUTOSAVE_PROJECT_ID_KEY, saved.projectId)
            return saved
        saved = self.save_project(
            content=content,
            name=default_name,
            description="",
            tags=[],
            set_current=False,
        )
        self._set_value(self._AUTOSAVE_PROJECT_ID_KEY, saved.projectId)
        return saved

    def load_last_project(self) -> F8ProjectRecord | None:
        current_project = self._project_after_prune(self.current_project_id())
        if current_project is not None:
            return current_project
        autosave_project = self._project_after_prune(self.autosave_project_id())
        if autosave_project is not None:
            return autosave_project
        return None

    def restore_project_version(self, *, project_id: str, version_number: int) -> F8ProjectRecord:
        current_project = self.project(project_id)
        if current_project is None:
            raise FileNotFoundError(f"Project not found: {project_id}")
        historical_project = self.project_version(project_id, version_number)
        if historical_project is None:
            raise FileNotFoundError(f"Project version not found: {project_id} v{version_number}")
        return self.save_project(
            content=historical_project.content,
            project_id=current_project.projectId,
            name=current_project.name,
            description=current_project.description,
            tags=list(current_project.tags),
            set_current=True,
        )

    def delete_project_version(self, *, project_id: str, version_number: int) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise FileNotFoundError("Project not found: ")
        target_version_number = int(version_number)
        timestamp = now_iso()
        with self._db.begin_sqla() as conn:
            head_row = conn.execute(
                select(project_heads_table.c.latest_version_number).where(
                    project_heads_table.c.project_id == normalized_project_id
                )
            ).mappings().first()
            if head_row is None:
                raise FileNotFoundError(f"Project not found: {normalized_project_id}")
            head_mapping = _row_mapping(head_row)
            latest_version_number = mapping_int(head_mapping, "latest_version_number")
            if target_version_number == latest_version_number:
                raise ValueError("Cannot delete the latest project version.")
            version_row = conn.execute(
                select(project_versions_table.c.version_number).where(
                    and_(
                        project_versions_table.c.project_id == normalized_project_id,
                        project_versions_table.c.version_number == target_version_number,
                    )
                )
            ).mappings().first()
            if version_row is None:
                raise FileNotFoundError(f"Project version not found: {normalized_project_id} v{target_version_number}")
            _ = conn.execute(
                delete(project_versions_table).where(
                    and_(
                        project_versions_table.c.project_id == normalized_project_id,
                        project_versions_table.c.version_number == target_version_number,
                    )
                )
            )
            _ = conn.execute(
                update(project_heads_table)
                .where(project_heads_table.c.project_id == normalized_project_id)
                .values(updated_at=timestamp)
            )

    def delete_project(self, *, project_id: str) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise FileNotFoundError("Project not found: ")
        with self._db.begin_sqla() as conn:
            head_row = conn.execute(
                select(project_heads_table.c.project_id).where(
                    project_heads_table.c.project_id == normalized_project_id
                )
            ).mappings().first()
            if head_row is None:
                raise FileNotFoundError(f"Project not found: {normalized_project_id}")
            _ = conn.execute(
                delete(project_versions_table).where(
                    project_versions_table.c.project_id == normalized_project_id
                )
            )
            _ = conn.execute(
                delete(project_heads_table).where(
                    project_heads_table.c.project_id == normalized_project_id
                )
            )
        if self.current_project_id() == normalized_project_id:
            self._set_value(self._CURRENT_PROJECT_ID_KEY, "")
        if self.autosave_project_id() == normalized_project_id:
            self._set_value(self._AUTOSAVE_PROJECT_ID_KEY, "")

    def export_project_to_json(self, *, project_id: str, path: str) -> Path:
        record = self.project(project_id)
        if record is None:
            raise FileNotFoundError(f"Project not found: {project_id}")
        out_path = Path(str(path or "").strip())
        if not str(out_path):
            raise ValueError("Export path is empty")
        if out_path.suffix.lower() != ".json":
            out_path = out_path.with_suffix(".json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _ = out_path.write_text(
            json.dumps(record.content, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return out_path

    def import_project_from_json(
        self,
        *,
        path: str,
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        set_current: bool = True,
    ) -> F8ProjectRecord:
        in_path = Path(str(path or "").strip())
        if not in_path.is_file():
            raise FileNotFoundError(f"Project JSON not found: {in_path}")
        raw = json_object_loads(in_path.read_text(encoding="utf-8"))
        _ = extract_layout(raw)
        normalized_name = str(name or in_path.stem or "Imported Project").strip()
        return self.save_project(
            content=raw,
            name=normalized_name,
            description=str(description or ""),
            tags=[] if tags is None else list(tags),
            set_current=set_current,
        )

    def _value_str(self, key: str) -> str:
        self._settings.beginGroup(self._SETTINGS_GROUP)
        try:
            value = cast(object, self._settings.value(key, ""))
        finally:
            self._settings.endGroup()
        return str("" if value is None else value).strip()

    def _set_value(self, key: str, value: str) -> None:
        self._settings.beginGroup(self._SETTINGS_GROUP)
        try:
            self._settings.setValue(key, value)
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def _project_after_prune(self, project_id: str) -> F8ProjectRecord | None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return None
        self._prune_project_versions(
            normalized_project_id,
            keep_latest=self._MAX_PROJECT_HISTORY_VERSIONS,
        )
        return self.project(normalized_project_id)

    def _prune_project_versions(self, project_id: str, *, keep_latest: int) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return
        with self._db.begin_sqla() as conn:
            self._prune_project_versions_in_connection(
                conn,
                project_id=normalized_project_id,
                keep_latest=keep_latest,
            )

    def _prune_project_versions_in_connection(
        self,
        conn: SqlAlchemyConnection,
        *,
        project_id: str,
        keep_latest: int,
    ) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return
        keep_count = max(1, int(keep_latest))
        statement = (
            select(project_versions_table.c.version_number)
            .where(project_versions_table.c.project_id == normalized_project_id)
            .order_by(project_versions_table.c.version_number.desc())
            .offset(keep_count)
        )
        rows = conn.execute(statement).mappings().all()
        stale_version_numbers = [mapping_int(_row_mapping(row), "version_number") for row in rows]
        if not stale_version_numbers:
            return
        _ = conn.execute(
            delete(project_versions_table).where(
                and_(
                    project_versions_table.c.project_id == normalized_project_id,
                    project_versions_table.c.version_number.in_(stale_version_numbers),
                )
            )
        )

    def _latest_project_content(
        self,
        conn: SqlAlchemyConnection,
        *,
        project_id: str,
        version_number: int,
    ) -> str:
        statement = select(project_versions_table.c.content).where(
            and_(
                project_versions_table.c.project_id == project_id,
                project_versions_table.c.version_number == int(version_number),
            )
        )
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise FileNotFoundError(f"Project version not found: {project_id} v{version_number}")
        row_mapping = _row_mapping(row)
        return _decompress_content(cast(bytes | None, row_mapping.get("content")))

    def _project_metadata_changed(
        self,
        conn: SqlAlchemyConnection,
        *,
        project_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> bool:
        statement = select(
            project_heads_table.c.name,
            project_heads_table.c.description,
            project_heads_table.c.tags_json,
        ).where(project_heads_table.c.project_id == project_id)
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise FileNotFoundError(f"Project not found: {project_id}")
        row_mapping = _row_mapping(row)
        existing_tags = json_string_list_loads(row_mapping.get("tags_json"))
        return (
            mapping_str(row_mapping, "name") != name
            or mapping_str(row_mapping, "description") != description
            or existing_tags != list(tags)
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


def _row_mapping(row: object) -> Mapping[object, object]:
    if not isinstance(row, Mapping):
        raise TypeError("Expected SQLAlchemy row mapping.")
    return cast(Mapping[object, object], row)


def _normalize_project_name(name: str) -> str:
    return str(name or "").strip()
