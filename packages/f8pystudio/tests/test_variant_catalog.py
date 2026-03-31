from __future__ import annotations

import json
from pathlib import Path

from f8pysdk.msgspec_codec import dump_json
from f8pystudio.variants.variant_catalog import (
    LocalVariantProvider,
    RemoteCacheProvider,
    VariantCatalogService,
    load_catalog_snapshot,
    save_catalog_snapshot,
)
from f8pystudio.variants.variant_models import (
    F8VariantCatalogSnapshot,
    F8VariantEntry,
    F8VariantSourceKind,
    F8VariantSyncState,
)
from f8pystudio.variants.variant_models import F8VariantKind
from f8pysdk import F8VariantRecord


def _make_record(*, variant_id: str, base_node_type: str, name: str) -> F8VariantRecord:
    now = F8VariantRecord.now_iso()
    return F8VariantRecord(
        variantId=variant_id,
        kind=F8VariantKind.operator,
        baseNodeType=base_node_type,
        serviceClass="svc.test",
        operatorClass="op.test",
        name=name,
        description="",
        tags=[],
        spec={"label": name},
        createdAt=now,
        updatedAt=now,
    )


def test_migrates_legacy_library_into_local_catalog(monkeypatch, tmp_path: Path) -> None:
    legacy_path = tmp_path / "nodeVariants.json"
    local_path = tmp_path / "nodeVariants.local.json"
    payload = {
        "schemaVersion": "f8variantlib/1",
        "variants": [dump_json(_make_record(variant_id="v1", base_node_type="svc.a.op", name="Alpha"), mode="json")],
    }
    legacy_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr("f8pystudio.variants.variant_catalog.legacy_variants_file_path", lambda: legacy_path)

    snapshot = load_catalog_snapshot(local_path, migrate_legacy=True)

    assert local_path.is_file() is True
    assert legacy_path.with_suffix(".json.bak").is_file() is True
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].source == F8VariantSourceKind.local
    assert snapshot.entries[0].record.variantId == "v1"


def test_catalog_service_prefers_local_and_hides_uninstalled_public_remote(tmp_path: Path) -> None:
    local_path = tmp_path / "local.json"
    remote_path = tmp_path / "remote.json"
    save_catalog_snapshot(
        local_path,
        F8VariantCatalogSnapshot(
            entries=[
                F8VariantEntry(
                    record=_make_record(variant_id="shared", base_node_type="svc.a.op", name="Local Shared"),
                    source=F8VariantSourceKind.local,
                    syncState=F8VariantSyncState.local_only,
                )
            ]
        ),
    )
    save_catalog_snapshot(
        remote_path,
        F8VariantCatalogSnapshot(
            entries=[
                F8VariantEntry(
                    record=_make_record(variant_id="shared", base_node_type="svc.a.op", name="Remote Shared"),
                    source=F8VariantSourceKind.remote_public,
                    syncState=F8VariantSyncState.synced,
                    installed=False,
                ),
                F8VariantEntry(
                    record=_make_record(variant_id="public_only", base_node_type="svc.a.op", name="Public Only"),
                    source=F8VariantSourceKind.remote_public,
                    syncState=F8VariantSyncState.synced,
                    installed=False,
                ),
            ]
        ),
    )
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(path=local_path),
        remote_provider=RemoteCacheProvider(path=remote_path),
    )

    visible = service.list_records_for_base("svc.a.op")
    all_entries = service.list_entries_for_base("svc.a.op", include_uninstalled=True)

    assert [record.variantId for record in visible] == ["shared"]
    assert [entry.record.variantId for entry in all_entries] == ["shared", "public_only"]
    assert all_entries[0].record.name == "Local Shared"
