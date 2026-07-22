from __future__ import annotations

import json
from pathlib import Path

import pytest
from f8pysdk.codec import copy_model, validate_as
from f8pysdk.specs import F8ComponentRecord as ProtocolF8ComponentRecord

from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_repository import (
    component_entry,
    export_component_to_json,
    import_component_from_json,
    upsert_component,
)
from f8pystudio.assets.components.component_models import F8ComponentRecord


def _make_component_record(*, component_id: str, name: str) -> F8ComponentRecord:
    return F8ComponentRecord(
        componentId=component_id,
        name=name,
        description="desc",
        tags=["demo"],
        content={
            "schemaVersion": "f8studio-session/1",
            "layout": {
                "nodes": {},
                "connections": [],
            },
        },
    )


def _patch_component_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ComponentCatalogService:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.components.component_repository._service", lambda: service)
    return service


def test_component_import_renames_duplicate_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_component_service(monkeypatch, tmp_path)
    upsert_component(_make_component_record(component_id="c-existing", name="Imported Component"))
    monkeypatch.setattr(
        "f8pystudio.assets.components.component_repository.new_asset_id",
        lambda: "imported-component",
    )

    import_path = tmp_path / "component-import.json"
    payload = {
        "componentId": "remote-component",
        "assetType": "component",
        "versionNumber": 7,
        "record": {
            "componentId": "remote-component",
            "name": "Imported Component",
            "description": "desc",
            "tags": ["demo"],
            "content": {
                "schemaVersion": "f8studio-session/1",
                "layout": {
                    "nodes": {},
                    "connections": [],
                },
            },
            "createdAt": "2026-04-01T00:00:00+00:00",
            "updatedAt": "2026-04-01T00:00:00+00:00",
        },
    }
    import_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    imported = import_component_from_json(str(import_path))

    assert imported.componentId == "imported-component"
    assert imported.name == "Imported Component (2)"


def test_component_export_import_uses_single_asset_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_component_service(monkeypatch, tmp_path)
    saved = upsert_component(_make_component_record(component_id="component-a", name="Exported Component"))

    export_path = tmp_path / "component-export.json"
    export_component_to_json("component-a", str(export_path))

    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["assetType"] == "component"
    assert exported["componentId"] == "component-a"
    assert exported["versionNumber"] == 1
    assert exported["record"]["componentId"] == "component-a"
    assert exported["record"]["content"]["schemaVersion"] == "f8studio-session/1"

    monkeypatch.setattr(
        "f8pystudio.assets.components.component_repository.new_asset_id",
        lambda: "component-imported-copy",
    )
    imported = import_component_from_json(str(export_path))

    assert imported.componentId == "component-imported-copy"
    assert imported.name == f"{saved.name} (2)"
    imported_entry = component_entry("component-imported-copy", include_uninstalled=True)
    assert imported_entry is not None
    assert imported_entry.record.content["schemaVersion"] == "f8studio-session/1"


def test_component_import_rejects_wrong_asset_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_component_service(monkeypatch, tmp_path)
    import_path = tmp_path / "wrong-component.json"
    payload = {
        "componentId": "component-a",
        "assetType": "variant",
        "versionNumber": 1,
        "record": {
            "componentId": "component-a",
            "name": "Wrong Type",
            "description": "",
            "tags": [],
            "content": {
                "schemaVersion": "f8studio-session/1",
                "layout": {
                    "nodes": {},
                    "connections": [],
                },
            },
            "createdAt": "2026-04-01T00:00:00+00:00",
            "updatedAt": "2026-04-01T00:00:00+00:00",
        },
    }
    import_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="Expected component asset payload"):
        import_component_from_json(str(import_path))


def test_component_upsert_rejects_conflicting_reserved_role_tags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_component_service(monkeypatch, tmp_path)
    record = copy_model(
        _make_component_record(component_id="component-invalid-role", name="Invalid Role"),
        update={"tags": ["role:shape", "role:output"]},
    )

    with pytest.raises(ValueError, match="at most one role tag"):
        upsert_component(record)


def test_component_export_sanitizes_launch_runtime_errors_and_publish_redactions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_component_service(monkeypatch, tmp_path)
    saved = upsert_component(
        F8ComponentRecord(
            componentId="component-dirty",
            name="Dirty Component",
            description="desc",
            tags=["demo"],
            content={
                "schemaVersion": "f8studio-session/1",
                "layout": {
                    "nodes": {
                        "svcA": {
                            "type_": "svc.f8.player",
                            "f8_spec": {
                                "schemaVersion": "f8service/1",
                                "specKind": "service",
                                "serviceClass": "f8.player",
                                "version": "0.0.1",
                                "label": "Player",
                                "launch": {
                                    "command": "H:\\Feel8\\f8studio\\services\\f8\\player\\win\\service.exe",
                                    "args": [],
                                    "env": {},
                                    "workdir": "H:\\Feel8\\f8studio\\services\\f8\\player",
                                },
                                "stateFields": [
                                    {
                                        "name": "path",
                                        "access": "rw",
                                        "redactOnPublish": True,
                                        "valueSchema": {"type": "string", "default": ""},
                                    },
                                    {
                                        "name": "preview",
                                        "access": "ro",
                                        "redactOnPublish": False,
                                        "valueSchema": {},
                                    },
                                    {
                                        "name": "lastError",
                                        "access": "wo",
                                        "redactOnPublish": False,
                                        "valueSchema": {"type": "string", "default": ""},
                                    },
                                ],
                            },
                            "custom": {
                                "path": "C:\\Users\\sshome\\video.mp4",
                                "preview": {"frame": 1},
                                "lastError": "Traceback (most recent call last):\n  File \"H:\\Feel8\\f8studio\\x.py\"",
                            },
                        }
                    },
                    "connections": [],
                },
            },
        )
    )

    export_path = tmp_path / "component-sanitized-export.json"
    export_component_to_json(str(saved.componentId), str(export_path))

    exported = json.loads(export_path.read_text(encoding="utf-8"))
    node_payload = exported["record"]["content"]["layout"]["nodes"]["svcA"]
    assert "launch" not in node_payload["f8_spec"]
    assert node_payload["custom"]["path"] == ""
    assert "preview" not in node_payload["custom"]
    assert "lastError" not in node_payload["custom"]


def test_component_protocol_record_rejects_legacy_top_level_schema_version() -> None:
    with pytest.raises(Exception):
        validate_as(
            ProtocolF8ComponentRecord,
            {
                "componentId": "component-legacy",
                "name": "Legacy Component",
                "schemaVersion": "f8studio-session/1",
                "content": {
                    "schemaVersion": "f8studio-session/1",
                    "layout": {"nodes": {}, "connections": []},
                },
            },
        )
