from __future__ import annotations

import json
from pathlib import Path

import pytest

from f8pysdk.codec import copy_model, dump_json
from f8pystudio.assets.variants.variant_models import F8VariantEntry, F8VariantKind, F8VariantSourceKind, variant_now_iso
from f8pysdk.specs import F8VariantRecord
from f8pystudio.assets.variants.variant_catalog import VariantCatalogService
from f8pystudio.assets.variants.variant_repository import (
    delete_variant,
    export_to_json,
    import_from_json,
    is_variant_name_conflict,
    list_variants_for_base,
    upsert_variant,
    variant_exists,
)


def _make_variant_record(*, variant_id: str, base_node_type: str, name: str) -> F8VariantRecord:
    now = variant_now_iso()
    return F8VariantRecord(
        variantId=variant_id,
        kind=F8VariantKind.operator,
        baseNodeType=base_node_type,
        serviceClass="svc.test",
        operatorClass="op.test",
        name=name,
        description="",
        tags=[],
        spec={"label": "x"},
        createdAt=now,
        updatedAt=now,
    )


def _patch_variants_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "nodeVariants.json"
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.variants.variant_repository._service", lambda: service)
    monkeypatch.setattr("f8pystudio.assets.variants.variant_repository.variants_file_path", lambda: target)
    return target


def test_variant_name_conflict_strip_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    upsert_variant(_make_variant_record(variant_id="v1", base_node_type="svc.a.op", name="foo"))

    assert is_variant_name_conflict("svc.a.op", " foo ") is True
    assert is_variant_name_conflict("svc.a.op", "FOO") is False


def test_upsert_rejects_duplicate_name_same_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    upsert_variant(_make_variant_record(variant_id="v1", base_node_type="svc.a.op", name="dup"))

    with pytest.raises(ValueError, match="already exists"):
        upsert_variant(_make_variant_record(variant_id="v2", base_node_type="svc.a.op", name=" dup "))


def test_upsert_allows_same_name_different_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    upsert_variant(_make_variant_record(variant_id="v1", base_node_type="svc.a.op", name="same"))
    upsert_variant(_make_variant_record(variant_id="v2", base_node_type="svc.b.op", name="same"))

    assert len(list_variants_for_base("svc.a.op")) == 1
    assert len(list_variants_for_base("svc.b.op")) == 1


def test_import_auto_renames_duplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    upsert_variant(_make_variant_record(variant_id="v-existing", base_node_type="svc.a.op", name="name"))

    import_path = tmp_path / "import.json"
    payload = {
        "schemaVersion": "f8variantlib/1",
        "entries": [
            {
                "record": dump_json(_make_variant_record(variant_id="v2", base_node_type="svc.a.op", name="name"), mode="json"),
            },
            {
                "record": dump_json(_make_variant_record(variant_id="v3", base_node_type="svc.a.op", name=" name "), mode="json"),
            },
        ],
    }
    import_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    import_from_json(str(import_path), mode="merge")
    names = [item.name for item in list_variants_for_base("svc.a.op")]

    assert "name" in names
    assert "name (2)" in names
    assert "name (3)" in names


def test_variant_exists_returns_expected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    upsert_variant(_make_variant_record(variant_id="v1", base_node_type="svc.a.op", name="name"))

    assert variant_exists("v1") is True
    assert variant_exists("missing") is False


def test_delete_variant_removes_local_history_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.variants.variant_repository._service", lambda: service)

    first = upsert_variant(_make_variant_record(variant_id="v-delete", base_node_type="svc.a.op", name="delete-me"))
    _ = upsert_variant(copy_model(first, update={"spec": {"label": "changed"}}))

    assert service._local_provider.list_versions("v-delete")

    deleted = delete_variant("v-delete")

    assert deleted is True
    assert service.entry("v-delete", include_uninstalled=True) is None
    assert service._local_provider.list_versions("v-delete") == []


def test_export_import_library_v1_entries_preserves_current_version_and_sync_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_variants_file(monkeypatch, tmp_path)
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.variants.variant_repository._service", lambda: service)
    entry = F8VariantEntry(
        record=_make_variant_record(variant_id="v-sync", base_node_type="svc.a.op", name="sync"),
        source=F8VariantSourceKind.local,
        localVersionNumber=5,
        syncBaseRemoteRevision="r7",
        syncBaseRemoteVersionNumber=7,
        syncBaseLocalVersionNumber=5,
    )
    _ = service.upsert_local_entry(entry)

    export_path = tmp_path / "variants-export.json"
    export_to_json(str(export_path))

    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["schemaVersion"] == "f8variantlib/1"
    assert exported["entries"][0]["localVersionNumber"] == 5
    assert exported["entries"][0]["syncBaseRemoteRevision"] == "r7"
    assert exported["entries"][0]["syncBaseRemoteVersionNumber"] == 7
    assert exported["entries"][0]["syncBaseLocalVersionNumber"] == 5

    replaced_service = VariantCatalogService(db_path=tmp_path / "imported-assets.db")
    monkeypatch.setattr("f8pystudio.assets.variants.variant_repository._service", lambda: replaced_service)
    import_from_json(str(export_path), mode="replace")

    imported = replaced_service.entry("v-sync", include_uninstalled=True)
    assert imported is not None
    assert imported.localVersionNumber == 5
    assert imported.syncBaseRemoteRevision == "r7"
    assert imported.syncBaseRemoteVersionNumber == 7
    assert imported.syncBaseLocalVersionNumber == 5
