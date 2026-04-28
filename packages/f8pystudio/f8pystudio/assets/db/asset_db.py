from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import threading

from sqlalchemy import Column, ForeignKey, Index, Integer, LargeBinary, MetaData, Table, Text, create_engine, event, inspect, text
from sqlalchemy.engine import Connection as SqlAlchemyConnection
from sqlalchemy.engine import URL, Engine
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.pool import NullPool

_METADATA = MetaData()
_INITIALIZATION_LOCKS: dict[Path, threading.Lock] = {}
_INITIALIZATION_LOCKS_GUARD = threading.Lock()
logger = logging.getLogger(__name__)

project_heads_table = Table(
    "project_heads",
    _METADATA,
    Column("project_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("latest_version_number", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

project_versions_table = Table(
    "project_versions",
    _METADATA,
    Column("project_id", Text, ForeignKey("project_heads.project_id"), primary_key=True),
    Column("version_number", Integer, primary_key=True),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
)

component_remote_cache_table = Table(
    "component_remote_cache",
    _METADATA,
    Column("component_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("visibility", Text, nullable=True),
    Column("owner_user_id", Text, nullable=True),
    Column("owner_display_name", Text, nullable=True),
    Column("remote_version_number", Integer, nullable=True),
    Column("downloaded_at", Text, nullable=True),
    Column("installed", Integer, nullable=False),
    Column("has_cached_content", Integer, nullable=False),
    Column("subscribed", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
)

component_drafts_local_table = Table(
    "component_drafts_local",
    _METADATA,
    Column("draft_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("origin_kind", Text, nullable=True),
    Column("publish_target_asset_id", Text, nullable=True),
    Column("publish_base_remote_version_number", Integer, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

variant_remote_cache_table = Table(
    "variant_remote_cache",
    _METADATA,
    Column("variant_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("base_node_type", Text, nullable=False),
    Column("service_class", Text, nullable=False),
    Column("operator_class", Text, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("visibility", Text, nullable=True),
    Column("owner_user_id", Text, nullable=True),
    Column("owner_display_name", Text, nullable=True),
    Column("remote_version_number", Integer, nullable=True),
    Column("downloaded_at", Text, nullable=True),
    Column("installed", Integer, nullable=False),
    Column("has_cached_content", Integer, nullable=False),
    Column("subscribed", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
)

variant_drafts_local_table = Table(
    "variant_drafts_local",
    _METADATA,
    Column("draft_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("base_node_type", Text, nullable=False),
    Column("service_class", Text, nullable=False),
    Column("operator_class", Text, nullable=True),
    Column("content", LargeBinary, nullable=False),
    Column("origin_kind", Text, nullable=True),
    Column("publish_target_asset_id", Text, nullable=True),
    Column("publish_base_remote_version_number", Integer, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

Index("idx_project_heads_updated_at", project_heads_table.c.updated_at)
Index("idx_component_drafts_local_updated_at", component_drafts_local_table.c.updated_at)
Index("idx_variant_drafts_local_updated_at", variant_drafts_local_table.c.updated_at)
Index("idx_variant_remote_cache_updated_at", variant_remote_cache_table.c.updated_at)


def assets_db_path() -> Path:
    return Path.home() / ".f8" / "studio" / "assets.db"


def _initialization_lock_for(path: Path) -> threading.Lock:
    normalized_path = path.expanduser().resolve()
    with _INITIALIZATION_LOCKS_GUARD:
        existing_lock = _INITIALIZATION_LOCKS.get(normalized_path)
        if existing_lock is not None:
            return existing_lock
        created_lock = threading.Lock()
        _INITIALIZATION_LOCKS[normalized_path] = created_lock
        return created_lock


class AssetsDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self._path = assets_db_path() if path is None else Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def ensure_initialized(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _initialization_lock_for(self.path):
            self._backup_database_if_remote_cache_schema_mismatch()
            engine = self._engine()
            try:
                _METADATA.create_all(bind=engine)
                self._apply_additive_migrations(engine)
            finally:
                engine.dispose()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def connect_sqla(self) -> Iterator[SqlAlchemyConnection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()

    @contextmanager
    def begin_sqla(self) -> Iterator[SqlAlchemyConnection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = self._engine()
        try:
            with engine.begin() as connection:
                yield connection
        finally:
            engine.dispose()

    def _engine(self) -> Engine:
        database_url = URL.create("sqlite+pysqlite", database=str(self.path.resolve()))
        # This service creates short-lived engines for small local operations.
        # Disable pooling so every SQLite connection is closed deterministically.
        engine = create_engine(database_url, poolclass=NullPool)

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection: object, connection_record: object) -> None:
            del connection_record
            sqlite_connection = dbapi_connection
            if not isinstance(sqlite_connection, sqlite3.Connection):
                raise TypeError("Expected sqlite3.Connection for SQLite engine.")
            sqlite_connection.execute("PRAGMA foreign_keys = ON")

        return engine

    def _apply_additive_migrations(self, engine: Engine) -> None:
        del engine

    def _backup_database_if_remote_cache_schema_mismatch(self) -> None:
        if not self.path.exists():
            return
        engine = self._engine()
        try:
            if not self._remote_cache_schema_mismatch(engine):
                return
        finally:
            engine.dispose()
        backup_path = self._next_schema_backup_path()
        self.path.replace(backup_path)
        logger.warning(
            "Backed up assets database with legacy remote cache schema before rebuild: %s -> %s",
            self.path,
            backup_path,
        )

    def _remote_cache_schema_mismatch(self, engine: Engine) -> bool:
        inspector = inspect(engine)
        return self._table_column_names_mismatch(
            inspector=inspector,
            table=component_remote_cache_table,
        ) or self._table_column_names_mismatch(
            inspector=inspector,
            table=component_drafts_local_table,
        ) or self._table_column_names_mismatch(
            inspector=inspector,
            table=variant_remote_cache_table,
        )

    def _table_column_names_mismatch(self, *, inspector: Inspector, table: Table) -> bool:
        table_name = str(table.name)
        existing_table_names = set(inspector.get_table_names())
        if table_name not in existing_table_names:
            return False
        existing_columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
        expected_columns = {str(column.name) for column in table.columns}
        return existing_columns != expected_columns

    def _next_schema_backup_path(self) -> Path:
        timestamp = self._schema_backup_timestamp()
        candidate = self.path.with_name(f"{self.path.name}.{timestamp}")
        if not candidate.exists():
            return candidate
        suffix = 1
        while True:
            numbered_candidate = self.path.with_name(f"{self.path.name}.{timestamp}.{suffix}")
            if not numbered_candidate.exists():
                return numbered_candidate
            suffix += 1

    def _schema_backup_timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _ensure_nullable_text_column(self, engine: Engine, *, inspector: Inspector, table_name: str, column_name: str) -> None:
        if table_name not in set(inspector.get_table_names()):
            return
        if column_name in {str(column["name"]) for column in inspector.get_columns(table_name)}:
            return
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT"))

    def _ensure_integer_column_with_default(
        self,
        engine: Engine,
        *,
        inspector: Inspector,
        table_name: str,
        column_name: str,
        default_value: int,
    ) -> None:
        if table_name not in set(inspector.get_table_names()):
            return
        if column_name in {str(column["name"]) for column in inspector.get_columns(table_name)}:
            return
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT {int(default_value)}"
                )
            )

    def _ensure_nullable_integer_column(self, engine: Engine, *, inspector: Inspector, table_name: str, column_name: str) -> None:
        if table_name not in set(inspector.get_table_names()):
            return
        if column_name in {str(column["name"]) for column in inspector.get_columns(table_name)}:
            return
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER"))


__all__ = [
    "AssetsDatabase",
    "assets_db_path",
    "project_heads_table",
    "project_versions_table",
    "component_drafts_local_table",
    "component_remote_cache_table",
    "variant_drafts_local_table",
    "variant_remote_cache_table",
]
