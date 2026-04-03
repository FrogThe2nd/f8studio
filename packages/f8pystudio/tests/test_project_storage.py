from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from qtpy import QtCore

from f8pysdk.msgspec_codec import copy_model
from f8pystudio.graph_assets.asset_db import AssetsDatabase
from f8pystudio.graph_assets.component_catalog import ComponentCatalogService
from f8pystudio.graph_assets.component_models import (
    F8ComponentEntry,
    component_now_iso,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentSyncState,
    F8ComponentVisibility,
)
from f8pystudio.graph_assets.project_storage import ProjectStorageService


def test_assets_database_initializes_component_project_and_variant_tables(tmp_path: Path) -> None:
    db = AssetsDatabase(path=tmp_path / "assets.db")
    db.ensure_initialized()

    with sqlite3.connect(db.path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {str(row[0]) for row in rows}

    assert {
        "project_heads",
        "project_versions",
        "component_heads_local",
        "component_versions_local",
        "component_remote_cache",
        "variant_heads_local",
        "variant_remote_cache",
    }.issubset(table_names)


def test_assets_database_migrates_remote_cache_library_slug_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE component_remote_cache (
                component_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                visibility TEXT,
                owner_user_id TEXT,
                owner_display_name TEXT,
                remote_revision TEXT,
                sync_state TEXT NOT NULL,
                downloaded_at TEXT,
                installed INTEGER NOT NULL,
                subscribed INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE variant_remote_cache (
                variant_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                visibility TEXT,
                owner_user_id TEXT,
                owner_display_name TEXT,
                remote_revision TEXT,
                sync_state TEXT NOT NULL,
                downloaded_at TEXT,
                installed INTEGER NOT NULL,
                subscribed INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    db = AssetsDatabase(path=db_path)
    db.ensure_initialized()

    with sqlite3.connect(db.path) as conn:
        component_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(component_remote_cache)").fetchall()}
        variant_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(variant_remote_cache)").fetchall()}

    assert "library_slug" in component_columns
    assert "library_slug" in variant_columns


def _session_payload(node_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                node_id: {
                    "id": node_id,
                    "name": node_id,
                    "pos": [10, 20],
                }
            },
            "connections": [],
        },
    }


def _component_entry(
    *,
    component_id: str,
    source: F8ComponentSourceKind,
    installed: bool,
) -> F8ComponentEntry:
    now = component_now_iso()
    record = F8ComponentRecord(
        componentId=component_id,
        name=f"Component {component_id}",
        description="",
        usageNotes="",
        tags=["demo"],
        content=_session_payload(component_id),
        createdAt=now,
        updatedAt=now,
    )
    return F8ComponentEntry(
        record=record,
        source=source,
        visibility=F8ComponentVisibility.public if source == F8ComponentSourceKind.remote_public else None,
        remoteRevision="r1" if source != F8ComponentSourceKind.local else None,
        syncState=F8ComponentSyncState.synced if source != F8ComponentSourceKind.local else F8ComponentSyncState.local_only,
        installed=installed,
    )


def test_project_storage_save_load_export_and_import_round_trip(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-storage.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("alpha"),
        name="Alpha",
        description="Primary project",
        tags=["vision", "demo"],
        set_current=True,
    )

    assert service.current_project_id() == saved.projectId
    loaded = service.load_last_session()
    assert loaded is not None
    assert loaded.projectId == saved.projectId
    assert loaded.content["layout"]["nodes"]["alpha"]["name"] == "alpha"

    exported_path = service.export_project_to_json(project_id=saved.projectId, path=str(tmp_path / "alpha-export"))
    exported_payload = json.loads(exported_path.read_text(encoding="utf-8"))
    assert exported_payload["schemaVersion"] == "f8studio-session/1"

    imported = service.import_project_from_json(
        path=str(exported_path),
        name="Imported Alpha",
        description="Imported from JSON",
        tags=["imported"],
        set_current=True,
    )

    assert imported.projectId != saved.projectId
    assert service.current_project_id() == imported.projectId
    assert len(service.list_projects()) == 2
    assert service.project(imported.projectId) is not None


def test_project_storage_lists_versions_and_restores_older_snapshot_as_latest(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-history.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("first"),
        name="History Demo",
        description="Versioned project",
        tags=["history"],
        set_current=True,
    )
    updated = service.save_project(
        content=_session_payload("second"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )

    versions = service.list_project_versions(saved.projectId)
    assert [version.versionNumber for version in versions] == [2, 1]

    old_version = service.project_version(saved.projectId, 1)
    assert old_version is not None
    assert "first" in old_version.content["layout"]["nodes"]

    restored = service.restore_project_version(project_id=saved.projectId, version_number=1)
    assert restored.projectId == saved.projectId
    assert "first" in restored.content["layout"]["nodes"]
    assert "second" not in restored.content["layout"]["nodes"]

    latest = service.project(saved.projectId)
    assert latest is not None
    assert "first" in latest.content["layout"]["nodes"]
    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [3, 2, 1]


def test_component_catalog_hides_uninstalled_remote_entries_until_installed(tmp_path: Path) -> None:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    local_entry = _component_entry(component_id="local-1", source=F8ComponentSourceKind.local, installed=True)
    remote_entry = _component_entry(component_id="remote-1", source=F8ComponentSourceKind.remote_public, installed=False)

    service.upsert_local_entry(local_entry)
    service.replace_remote_entries([remote_entry])

    visible_before_install = {entry.record.componentId for entry in service.list_entries(include_uninstalled=False)}
    assert visible_before_install == {"local-1"}
    assert service.entry("remote-1", include_uninstalled=False) is None

    installed = service.install_remote_entry(remote_entry)
    assert installed.installed is True

    visible_after_install = {entry.record.componentId for entry in service.list_entries(include_uninstalled=False)}
    assert visible_after_install == {"local-1", "remote-1"}
    installed_entry = service.entry("remote-1", include_uninstalled=False)
    assert installed_entry is not None
    assert installed_entry.downloadedAt is not None


def test_component_catalog_persists_remote_library_slug(tmp_path: Path) -> None:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    remote_entry = copy_model(
        _component_entry(component_id="remote-1", source=F8ComponentSourceKind.remote_public, installed=False),
        update={"librarySlug": "community/featured"},
    )

    service.replace_remote_entries([remote_entry])

    loaded = service.entry("remote-1", include_uninstalled=True)
    assert loaded is not None
    assert loaded.librarySlug == "community/featured"
