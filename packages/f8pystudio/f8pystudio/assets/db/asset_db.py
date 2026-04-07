from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

from sqlalchemy import Column, ForeignKey, Index, Integer, LargeBinary, MetaData, Table, Text, create_engine, event, inspect, text
from sqlalchemy.engine import Connection as SqlAlchemyConnection
from sqlalchemy.engine import URL, Engine
from sqlalchemy.engine.reflection import Inspector

_METADATA = MetaData()
_INITIALIZATION_LOCKS: dict[Path, threading.Lock] = {}
_INITIALIZATION_LOCKS_GUARD = threading.Lock()

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

component_heads_local_table = Table(
    "component_heads_local",
    _METADATA,
    Column("component_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("latest_version_number", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


component_remote_cache_table = Table(
    "component_remote_cache",
    _METADATA,
    Column("component_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("remote_version_number", Integer, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("visibility", Text, nullable=True),
    Column("owner_user_id", Text, nullable=True),
    Column("owner_display_name", Text, nullable=True),
    Column("library_slug", Text, nullable=True),
    Column("remote_revision", Text, nullable=True),
    Column("sync_state", Text, nullable=False),
    Column("downloaded_at", Text, nullable=True),
    Column("installed", Integer, nullable=False),
    Column("has_cached_content", Integer, nullable=False),
    Column("subscribed", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
)

# Variant local/remote tables are defined here so variants can migrate into the
# same assets.db without introducing a second SQLite file.
variant_heads_local_table = Table(
    "variant_heads_local",
    _METADATA,
    Column("variant_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("base_node_type", Text, nullable=False),
    Column("service_class", Text, nullable=False),
    Column("operator_class", Text, nullable=True),
    Column("latest_version_number", Integer, nullable=False, default=1),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

variant_versions_local_table = Table(
    "variant_versions_local",
    _METADATA,
    Column("variant_id", Text, ForeignKey("variant_heads_local.variant_id"), primary_key=True),
    Column("version_number", Integer, primary_key=True),
    Column("record_json", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
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
    Column("remote_version_number", Integer, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("visibility", Text, nullable=True),
    Column("owner_user_id", Text, nullable=True),
    Column("owner_display_name", Text, nullable=True),
    Column("library_slug", Text, nullable=True),
    Column("remote_revision", Text, nullable=True),
    Column("sync_state", Text, nullable=False),
    Column("downloaded_at", Text, nullable=True),
    Column("installed", Integer, nullable=False),
    Column("has_cached_content", Integer, nullable=False),
    Column("subscribed", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
)

Index("idx_project_heads_updated_at", project_heads_table.c.updated_at)
Index("idx_component_heads_local_updated_at", component_heads_local_table.c.updated_at)
Index("idx_variant_heads_local_updated_at", variant_heads_local_table.c.updated_at)
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
        engine = create_engine(database_url)

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection: object, connection_record: object) -> None:
            del connection_record
            sqlite_connection = dbapi_connection
            if not isinstance(sqlite_connection, sqlite3.Connection):
                raise TypeError("Expected sqlite3.Connection for SQLite engine.")
            sqlite_connection.execute("PRAGMA foreign_keys = ON")

        return engine

    def _apply_additive_migrations(self, engine: Engine) -> None:
        inspector = inspect(engine)
        self._ensure_nullable_text_column(engine, inspector=inspector, table_name="component_remote_cache", column_name="library_slug")
        self._ensure_nullable_text_column(engine, inspector=inspector, table_name="variant_remote_cache", column_name="library_slug")
        self._ensure_integer_column_with_default(
            engine,
            inspector=inspector,
            table_name="variant_heads_local",
            column_name="latest_version_number",
            default_value=1,
        )

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


__all__ = [
    "AssetsDatabase",
    "assets_db_path",
    "project_heads_table",
    "project_versions_table",
    "component_heads_local_table",
    "component_remote_cache_table",
    "variant_heads_local_table",
    "variant_versions_local_table",
    "variant_remote_cache_table",
]
