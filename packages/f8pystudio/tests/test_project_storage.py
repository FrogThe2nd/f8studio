from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any
import zlib

import pytest
from qtpy import QtCore

from f8pysdk.msgspec_codec import copy_model
from f8pystudio.assets.common import stable_json_dumps
from f8pystudio.assets.db import AssetsDatabase
from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    component_now_iso,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentSyncState,
    F8ComponentVisibility,
)
from f8pystudio.assets.projects.project_storage import ProjectStorageService


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


def _insert_legacy_project_history(
    *,
    db_path: Path,
    project_id: str,
    version_count: int,
    name: str = "Legacy Project",
) -> None:
    created_at = "2026-01-01T00:00:00Z"
    updated_at = "2026-01-31T00:00:00Z"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO project_heads (
                project_id,
                name,
                description,
                tags_json,
                latest_version_number,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                "Legacy history fixture",
                json.dumps(["legacy"]),
                int(version_count),
                created_at,
                updated_at,
            ),
        )
        for version_number in range(1, int(version_count) + 1):
            payload = _session_payload(f"legacy-{version_number}")
            content = zlib.compress(stable_json_dumps(payload).encode("utf-8"), level=6, wbits=31)
            conn.execute(
                """
                INSERT INTO project_versions (
                    project_id,
                    version_number,
                    content,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    int(version_number),
                    content,
                    f"2026-01-{(version_number % 28) + 1:02d}T00:00:00Z",
                ),
            )
        conn.commit()


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
    loaded = service.load_last_project()
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


def test_project_storage_does_not_create_new_version_for_identical_content(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-dedupe.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("same"),
        name="Dedupe Demo",
        description="",
        tags=["demo"],
        set_current=True,
    )
    saved_again = service.save_project(
        content=_session_payload("same"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )

    assert saved_again.projectId == saved.projectId
    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [1]


def test_project_storage_save_last_project_dedupes_current_and_autosave_projects(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-save-last.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    autosaved = service.save_last_project(content=_session_payload("autosave"))
    autosaved_again = service.save_last_project(content=_session_payload("autosave"))

    assert autosaved_again.projectId == autosaved.projectId
    assert [version.versionNumber for version in service.list_project_versions(autosaved.projectId)] == [1]

    named = service.save_project(
        content=_session_payload("current"),
        name="Current Project",
        description="",
        tags=["current"],
        set_current=True,
    )
    named_again = service.save_last_project(content=_session_payload("current"))

    assert named_again.projectId == named.projectId
    assert [version.versionNumber for version in service.list_project_versions(named.projectId)] == [1]


def test_project_storage_updates_metadata_without_creating_new_version(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-metadata.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("meta"),
        name="Original Name",
        description="Original description",
        tags=["before"],
        set_current=True,
    )
    updated = service.save_project(
        content=_session_payload("meta"),
        project_id=saved.projectId,
        name="Renamed Project",
        description="Updated description",
        tags=["after"],
        set_current=True,
    )

    assert updated.projectId == saved.projectId
    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [1]

    loaded = service.project(saved.projectId)
    assert loaded is not None
    assert loaded.name == "Renamed Project"
    assert loaded.description == "Updated description"
    assert loaded.tags == ["after"]

    summaries = service.list_projects()
    assert len(summaries) == 1
    assert summaries[0].name == "Renamed Project"
    assert summaries[0].latestVersionNumber == 1


def test_project_storage_prunes_old_versions_after_reaching_history_limit(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-prune.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("version-1"),
        name="Prune Demo",
        description="",
        tags=["prune"],
        set_current=True,
    )
    for version_number in range(2, 56):
        _ = service.save_project(
            content=_session_payload(f"version-{version_number}"),
            project_id=saved.projectId,
            name=saved.name,
            description=saved.description,
            tags=list(saved.tags),
            set_current=True,
        )

    versions = service.list_project_versions(saved.projectId)
    assert len(versions) == 50
    assert versions[0].versionNumber == 55
    assert versions[-1].versionNumber == 6

    latest = service.project(saved.projectId)
    assert latest is not None
    assert "version-55" in latest.content["layout"]["nodes"]

    kept_version = service.project_version(saved.projectId, 6)
    assert kept_version is not None
    assert "version-6" in kept_version.content["layout"]["nodes"]

    assert service.project_version(saved.projectId, 5) is None
    with pytest.raises(FileNotFoundError):
        _ = service.restore_project_version(project_id=saved.projectId, version_number=5)


def test_project_storage_list_versions_prunes_existing_oversized_history(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-list-prune.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = ProjectStorageService(db_path=db_path, settings=settings)
    _insert_legacy_project_history(db_path=db_path, project_id="legacy-list", version_count=55)

    versions = service.list_project_versions("legacy-list")

    assert len(versions) == 50
    assert versions[0].versionNumber == 55
    assert versions[-1].versionNumber == 6
    assert service.project_version("legacy-list", 5) is None


def test_project_storage_load_last_project_prunes_existing_oversized_history(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-load-prune.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = ProjectStorageService(db_path=db_path, settings=settings)
    _insert_legacy_project_history(db_path=db_path, project_id="legacy-load", version_count=55)
    service.set_current_project_id("legacy-load")

    loaded = service.load_last_project()

    assert loaded is not None
    assert loaded.projectId == "legacy-load"
    assert "legacy-55" in loaded.content["layout"]["nodes"]
    assert len(service.list_project_versions("legacy-load")) == 50
    assert service.project_version("legacy-load", 5) is None


def test_project_storage_delete_project_version_removes_historical_snapshot(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-delete.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("delete-1"),
        name="Delete Demo",
        description="",
        tags=["delete"],
        set_current=True,
    )
    _ = service.save_project(
        content=_session_payload("delete-2"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )
    latest = service.save_project(
        content=_session_payload("delete-3"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )

    service.delete_project_version(project_id=saved.projectId, version_number=2)

    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [3, 1]
    assert service.project_version(saved.projectId, 2) is None
    current = service.project(saved.projectId)
    assert current is not None
    assert current.projectId == latest.projectId
    assert "delete-3" in current.content["layout"]["nodes"]


def test_project_storage_delete_project_version_rejects_latest(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "project-delete-latest.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)

    saved = service.save_project(
        content=_session_payload("keep-1"),
        name="Delete Latest Demo",
        description="",
        tags=["delete"],
        set_current=True,
    )
    _ = service.save_project(
        content=_session_payload("keep-2"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )

    with pytest.raises(ValueError, match="latest project version"):
        service.delete_project_version(project_id=saved.projectId, version_number=2)

    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [2, 1]


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
