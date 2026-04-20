from __future__ import annotations

from pathlib import Path

import msgspec
from f8pysdk.codec import copy_model

from f8pystudio.assets.variants.variant_catalog import LocalVariantProvider, RemoteCacheProvider, VariantCatalogService
from f8pystudio.assets.variants.variant_models import (
    F8VariantEntry,
    F8VariantKind,
    F8VariantSourceKind,
    F8VariantSyncState,
    variant_now_iso,
)
from f8pysdk.specs import F8VariantRecord


def _make_record(*, variant_id: str, base_node_type: str, name: str) -> F8VariantRecord:
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
        spec={"label": name},
        createdAt=now,
        updatedAt=now,
    )


def test_variant_catalog_providers_persist_local_and_remote_entries_in_assets_db(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.db"
    local_provider = LocalVariantProvider(db_path=db_path)
    remote_provider = RemoteCacheProvider(db_path=db_path)

    local_provider.save_entries(
        [
            F8VariantEntry(
                record=_make_record(variant_id="local-1", base_node_type="svc.a.op", name="Local One"),
                source=F8VariantSourceKind.local,
                syncState=F8VariantSyncState.local_only,
            )
        ]
    )
    remote_provider.save_entries(
        [
            F8VariantEntry(
                record=_make_record(variant_id="remote-1", base_node_type="svc.a.op", name="Remote One"),
                source=F8VariantSourceKind.remote_public,
                syncState=F8VariantSyncState.synced,
                installed=False,
            )
        ]
    )

    loaded_local = local_provider.load_entries()
    loaded_remote = remote_provider.load_entries()

    assert [entry.record.variantId for entry in loaded_local] == ["local-1"]
    assert [entry.record.variantId for entry in loaded_remote] == ["remote-1"]


def test_catalog_service_prefers_local_and_hides_uninstalled_public_remote(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.db"
    remote_provider = RemoteCacheProvider(db_path=db_path)
    remote_provider.save_entries(
        [
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
    )
    service = VariantCatalogService(db_path=db_path, remote_provider=remote_provider)
    service.upsert_local_entry(
        F8VariantEntry(
            record=_make_record(variant_id="shared", base_node_type="svc.a.op", name="Local Shared"),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
            isLocalDraft=True,
        )
    )

    visible = service.list_records_for_base("svc.a.op")
    all_entries = service.list_entries_for_base("svc.a.op", include_uninstalled=True)

    assert [record.variantId for record in visible] == ["shared"]
    assert [entry.record.variantId for entry in all_entries] == ["shared", "public_only"]
    assert all_entries[0].record.name == "Local Shared"


def test_remote_variant_provider_persists_library_slug(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.db"
    provider = RemoteCacheProvider(db_path=db_path)
    provider.save_entries(
        [
            F8VariantEntry(
                record=_make_record(variant_id="remote-1", base_node_type="svc.a.op", name="Remote One"),
                source=F8VariantSourceKind.remote_public,
                librarySlug="official/default",
                syncState=F8VariantSyncState.synced,
                installed=False,
            )
        ]
    )

    loaded = provider.load_entries()

    assert len(loaded) == 1
    assert loaded[0].librarySlug == "official/default"


def test_local_variant_provider_preserves_service_variant_without_operator_class(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.db"
    provider = LocalVariantProvider(db_path=db_path)
    now = variant_now_iso()
    provider.save_entries(
        [
            F8VariantEntry(
                record=F8VariantRecord(
                    variantId="svc-1",
                    kind=F8VariantKind.service,
                    baseNodeType="svc.a",
                    serviceClass="svc.a",
                    operatorClass=msgspec.UNSET,
                    name="Service Variant",
                    description="",
                    tags=[],
                    spec={"label": "Service Variant"},
                    createdAt=now,
                    updatedAt=now,
                ),
                source=F8VariantSourceKind.local,
                syncState=F8VariantSyncState.local_only,
            )
        ]
    )

    loaded = provider.load_entries()

    assert len(loaded) == 1
    assert isinstance(loaded[0].record.operatorClass, msgspec.UnsetType)


def test_variant_catalog_service_keeps_only_latest_draft_snapshot_for_metadata_only_edits(tmp_path: Path) -> None:
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    first = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_record(variant_id="local-1", base_node_type="svc.a.op", name="Local One"),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
        )
    )
    second = service.upsert_local_entry(
        F8VariantEntry(
            record=copy_model(
                _make_record(variant_id="local-1", base_node_type="svc.a.op", name="Local One Renamed"),
                update={
                    "description": "metadata only",
                    "tags": ["updated"],
                    "spec": {"label": "Local One"},
                },
            ),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
        )
    )

    loaded = service.entry("local-1", include_uninstalled=True)

    assert first.localVersionNumber is None
    assert second.localVersionNumber is None
    assert service.list_local_versions("local-1") == []
    assert service.local_version_record("local-1", 1) is None
    assert loaded is not None
    assert loaded.localVersionNumber is None
    assert loaded.isLocalDraft is True
    assert loaded.record.name == "Local One Renamed"
    assert loaded.record.description == "metadata only"
    assert loaded.record.tags == ["updated"]


def test_variant_catalog_service_keeps_only_latest_draft_snapshot_for_content_changes(tmp_path: Path) -> None:
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    _ = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_record(variant_id="local-1", base_node_type="svc.a.op", name="Local One"),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
        )
    )
    second = service.upsert_local_entry(
        F8VariantEntry(
            record=copy_model(
                _make_record(variant_id="local-1", base_node_type="svc.a.op", name="Local One"),
                update={"spec": {"label": "Local One v2"}},
            ),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
        )
    )

    versions = service.list_local_versions("local-1")
    version_one = service.local_version_record("local-1", 1)
    version_two = service.local_version_record("local-1", 2)
    loaded = service.entry("local-1", include_uninstalled=True)

    assert second.localVersionNumber is None
    assert versions == []
    assert version_one is None
    assert version_two is None
    assert loaded is not None
    assert loaded.record.spec == {"label": "Local One v2"}


def test_variant_catalog_service_ignores_remote_version_when_saving_draft(tmp_path: Path) -> None:
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    saved = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_record(variant_id="remote-seeded", base_node_type="svc.a.op", name="Remote Seeded"),
            source=F8VariantSourceKind.local,
            syncState=F8VariantSyncState.local_only,
            remoteVersionNumber=5,
        )
    )

    versions = service.list_local_versions("remote-seeded")

    assert saved.localVersionNumber is None
    assert saved.isLocalDraft is True
    assert versions == []
