from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from qtpy import QtCore
from sqlalchemy import insert, select
from f8pysdk.msgspec_codec import copy_model

from f8pystudio.assets.variants.variant_catalog import LocalVariantProvider, RemoteCacheProvider, VariantCatalogService
from f8pystudio.assets.variants.variant_models import (
    F8VariantEntry,
    F8VariantKind,
    F8VariantRemoteConflictError,
    F8VariantSourceKind,
    F8VariantSyncState,
    F8VariantVisibility,
    variant_now_iso,
)
from f8pystudio.assets.variants.variant_sync import VariantSyncClient
from f8pystudio.assets.db import variant_remote_cache_table
from f8pystudio.assets.ui.variant_manager_dialog import variant_row_state_for_entries
from f8pysdk import F8VariantRecord


def _make_entry(*, variant_id: str, source: F8VariantSourceKind, installed: bool = True, remote_revision: str | None = None) -> F8VariantEntry:
    now = variant_now_iso()
    record = F8VariantRecord(
        variantId=variant_id,
        kind=F8VariantKind.operator,
        baseNodeType="svc.a.op",
        serviceClass="svc.test",
        operatorClass="op.test",
        name=f"Variant {variant_id}",
        description="",
        tags=[],
        spec={"label": variant_id},
        createdAt=now,
        updatedAt=now,
    )
    return F8VariantEntry(
        record=record,
        source=source,
        visibility=F8VariantVisibility.private if source == F8VariantSourceKind.remote_private else None,
        remoteRevision=remote_revision,
        syncState=F8VariantSyncState.synced if remote_revision else F8VariantSyncState.local_only,
        installed=installed,
    )


class _VariantApiHandler(BaseHTTPRequestHandler):
    server_version = "VariantApiTest/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw)
        assert isinstance(value, dict)
        return value

    def _write_json(self, status: int, payload: dict[str, object], *, set_cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        cookie = str(self.headers.get("Cookie") or "")
        if "session=active-1" in cookie:
            return True
        self._write_json(401, {"message": "expired"})
        return False

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/auth/sign-in/username":
            self.server.last_login_user_agent = str(self.headers.get("User-Agent") or "")
            self._write_json(
                200,
                {"ok": True},
                set_cookie="session=active-1; Path=/; HttpOnly",
            )
            return
        if self.path == "/api/auth/sign-out":
            if not self._check_auth():
                return
            self._write_json(200, {}, set_cookie="session=; Path=/; Max-Age=0")
            return
        if self.path == "/v1/variants/public-1/subscribe":
            if not self._check_auth():
                return
            self._write_json(200, self.server.asset_payload_from_variant_record(self.server.public_record, visibility="public", subscribed=True))
            return
        if self.path == "/v1/variants":
            if not self._check_auth():
                return
            payload = self._read_json()
            payload["visibility"] = payload.get("visibility") or "private"
            self._write_json(200, self.server.asset_payload_from_variant_record(payload["record"], visibility=str(payload["visibility"])))
            return
        self._write_json(404, {"message": "missing"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/me":
            if not self._check_auth():
                return
            self._write_json(200, {"userId": "u1", "username": "u", "displayName": "User One"})
            return
        if self.path.startswith("/v1/variants?"):
            cookie = str(self.headers.get("Cookie") or "")
            if "owner=subscribed" in self.path:
                if not self._check_auth():
                    return
                self._write_json(200, {"entries": [self.server.subscribed_asset], "nextCursor": None})
                return
            if ("owner=public" not in self.path or bool(cookie)) and not self._check_auth():
                return
            self._write_json(200, {"entries": [self.server.public_asset], "nextCursor": None})
            return
        if self.path == "/v1/variants/public-1":
            if not self._check_auth():
                return
            self._write_json(200, self.server.public_asset)
            return
        self._write_json(404, {"message": "missing"})

    def do_PUT(self) -> None:  # noqa: N802
        if self.path == "/v1/variants/conflict-1":
            if not self._check_auth():
                return
            self._write_json(409, {"message": "conflict", "variantId": "conflict-1", "remoteRevision": "r-remote"})
            return
        self._write_json(404, {"message": "missing"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path == "/v1/variants/public-1/subscribe":
            if not self._check_auth():
                return
            self._write_json(200, self.server.asset_payload_from_variant_record(self.server.public_record, visibility="public", subscribed=False))
            return
        self._write_json(404, {"message": "missing"})


class _Server(ThreadingHTTPServer):
    def __init__(self, server_address):
        super().__init__(server_address, _VariantApiHandler)
        self.last_login_user_agent = ""
        self.public_record = {
            "variantId": "public-1",
            "kind": "operator",
            "baseNodeType": "svc.a.op",
            "serviceClass": "svc.test",
            "operatorClass": "op.test",
            "name": "Public One",
            "description": "",
            "tags": [],
            "spec": {"label": "Public One"},
            "createdAt": variant_now_iso(),
            "updatedAt": variant_now_iso(),
        }
        self.public_asset = self.asset_payload_from_variant_record(self.public_record, visibility="public")
        self.subscribed_asset = self.asset_payload_from_variant_record(
            {
                "variantId": "subscribed-1",
                "kind": "operator",
                "baseNodeType": "svc.a.op",
                "serviceClass": "svc.test",
                "operatorClass": "op.test",
                "name": "Subscribed One",
                "description": "",
                "tags": [],
                "spec": {"label": "Subscribed One"},
                "createdAt": variant_now_iso(),
                "updatedAt": variant_now_iso(),
            },
            visibility="public",
            subscribed=True,
        )

    @staticmethod
    def asset_payload_from_variant_record(
        record: dict[str, object],
        *,
        visibility: str,
        subscribed: bool = False,
    ) -> dict[str, object]:
        return {
            "variantId": str(record["variantId"]),
            "assetType": "variant",
            "ownerUserId": "u2" if visibility == "public" else "u1",
            "ownerDisplayName": "Remote User" if visibility == "public" else "User One",
            "visibility": visibility,
            "revision": "r-public" if visibility == "public" else "r1",
            "latestRevision": "r-public" if visibility == "public" else "r1",
            "versionNumber": 1,
            "latestVersionNumber": 1,
            "createdAt": str(record["createdAt"]),
            "updatedAt": str(record["updatedAt"]),
            "editable": visibility != "public",
            "subscribed": subscribed,
            "record": record,
        }


def test_variant_sync_client_uses_cookie_sessions_and_marks_conflicts(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-sync.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service)
        client.set_base_url(f"http://127.0.0.1:{server.server_port}")
        anonymous_page = client.list_variants(scope="community", base_node_type="svc.a.op")
        assert anonymous_page.entries[0].record.variantId == "public-1"
        assert anonymous_page.entries[0].remoteVersionNumber == 1
        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", username="u", password="p", remember=True)

        assert auth.user.displayName == "User One"
        assert server.last_login_user_agent == "F8Studio/1.0"
        assert len(client.saved_sessions()) == 1
        assert client.current_session() is not None
        page = client.list_variants(scope="community", base_node_type="svc.a.op")
        assert page.entries[0].record.variantId == "public-1"
        assert client.current_access_token() == "session=active-1"

        subscribed_page = client.list_variants(scope="subscribed", base_node_type="svc.a.op")
        assert subscribed_page.entries[0].record.variantId == "subscribed-1"
        assert subscribed_page.entries[0].subscribed is True

        subscribed = client.subscribe_variant("public-1")
        assert subscribed.subscribed is True
        unsubscribed = client.unsubscribe_variant("public-1")
        assert unsubscribed.subscribed is False

        installed = client.install_variant("public-1")
        assert installed.installed is True
        assert service.variant_exists("public-1") is True
        assert installed.remoteVersionNumber == 1

        local_entry = _make_entry(variant_id="local-1", source=F8VariantSourceKind.local)
        uploaded = client.upload_entry(local_entry)
        assert uploaded.remoteRevision == "r1"

        conflict_entry = _make_entry(
            variant_id="conflict-1",
            source=F8VariantSourceKind.remote_private,
            remote_revision="r-local",
        )
        service.replace_remote_entries([conflict_entry])
        try:
            client.upload_entry(conflict_entry)
        except F8VariantRemoteConflictError as exc:
            assert exc.remote_revision == "r-remote"
        else:
            raise AssertionError("expected conflict error")
        marked = service.entry("conflict-1", include_uninstalled=True)
        assert marked is not None
        assert marked.syncState == F8VariantSyncState.conflict
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_drops_legacy_saved_sessions_without_crashing(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-legacy.ini"), QtCore.QSettings.IniFormat)
    settings.beginGroup("variants/remote_sync/v1")
    settings.setValue(
        "saved_sessions",
        [
            {
                "accountId": "legacy-account",
                "baseUrl": "https://assetcloud.feel8.fun",
                "user": {
                    "userId": "u1",
                    "displayName": "Legacy User",
                    "username": "legacy",
                },
                "accessToken": "old-token-only",
                "lastUsedAt": "2026-04-04T00:00:00+00:00",
            }
        ],
    )
    settings.setValue("current_account_id", "legacy-account")
    settings.endGroup()
    settings.sync()

    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    assert client.saved_sessions() == []
    assert client.current_session() is None


def test_variant_remote_cache_load_cleans_empty_variant_ids(tmp_path: Path) -> None:
    provider = RemoteCacheProvider(db_path=tmp_path / "assets.db")
    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(variant_remote_cache_table).values(
                variant_id="",
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                library_slug="community",
                remote_revision="r1",
                sync_state="synced",
                downloaded_at=None,
                installed=0,
                subscribed=0,
                content=b"{}",
                updated_at="2026-04-04T00:00:00+00:00",
            )
        )

    assert provider.load_entries() == []

    with provider._db.connect_sqla() as conn:
        rows = conn.execute(select(variant_remote_cache_table.c.variant_id)).all()
    assert rows == []


def test_variant_row_state_badges_cover_remote_both_and_conflict() -> None:
    local_entry = _make_entry(variant_id="asset-1", source=F8VariantSourceKind.local)
    local_entry = copy_model(local_entry, update={"localVersionNumber": 4})
    remote_entry = _make_entry(variant_id="asset-1", source=F8VariantSourceKind.remote_public, installed=True, remote_revision="r1")
    remote_entry = copy_model(
        remote_entry,
        update={"visibility": F8VariantVisibility.public, "remoteVersionNumber": 6},
    )
    both_state = variant_row_state_for_entries(
        variant_id="asset-1",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )
    conflict_remote = _make_entry(variant_id="asset-2", source=F8VariantSourceKind.remote_public, installed=True, remote_revision="r1")
    conflict_remote = copy_model(
        conflict_remote,
        update={
            "visibility": F8VariantVisibility.public,
            "syncState": F8VariantSyncState.conflict,
            "remoteVersionNumber": 3,
        },
    )
    conflict_state = variant_row_state_for_entries(
        variant_id="asset-2",
        local_entry=None,
        remote_entry=conflict_remote,
    )
    remote_state = variant_row_state_for_entries(
        variant_id="asset-3",
        local_entry=None,
        remote_entry=copy_model(
            _make_entry(variant_id="asset-3", source=F8VariantSourceKind.remote_public, installed=False, remote_revision="r1"),
            update={"visibility": F8VariantVisibility.public, "remoteVersionNumber": 2},
        ),
    )

    assert both_state.badge_texts() == ["both", "public", "synced", "L4", "R6"]
    assert conflict_state.badge_texts() == ["both", "public", "conflict", "R3"]
    assert remote_state.badge_texts() == ["remote", "public", "R2"]
