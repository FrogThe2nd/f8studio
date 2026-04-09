from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import zlib

import pytest
from qtpy import QtCore
from sqlalchemy import insert, select
from f8pysdk.codec import copy_model

from f8pystudio.assets.variants.variant_catalog import LocalVariantProvider, RemoteCacheProvider, VariantCatalogService
from f8pystudio.assets.variants.variant_models import (
    F8VariantEntry,
    F8VariantKind,
    F8VariantRemoteRequestError,
    F8VariantRemoteConflictError,
    F8VariantRemoteListPage,
    F8VariantSourceKind,
    F8VariantSyncState,
    F8VariantVisibility,
    variant_now_iso,
)
from f8pystudio.assets.variants.variant_sync import VariantSyncClient
from f8pystudio.assets.db import variant_remote_cache_table
from f8pystudio.assets.ui.variant_manager_dialog import variant_row_state_for_entries
from f8pysdk.specs import F8VariantRecord


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
            self.server.last_signout_origin = str(self.headers.get("Origin") or "")
            if not self.server.last_signout_origin:
                self._write_json(403, {"message": "Missing or null Origin"})
                return
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
        if self.path == "/v1/variants/public-1/content":
            if not self._check_auth():
                return
            self._write_json(
                200,
                {
                    "variantId": "public-1",
                    "assetType": "variant",
                    "versionNumber": 1,
                    "revision": "r-public",
                    "record": self.server.public_record,
                },
            )
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
        self.last_signout_origin = ""
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
        client.logout()
        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.saved_sessions() == []
        assert server.last_signout_origin == f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_can_cache_remote_content_without_installing(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-sync-cache.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service)
        base_url = f"http://127.0.0.1:{server.server_port}"
        _ = client.login(base_url=base_url, username="u", password="p", remember=True)

        cached = client.cache_variant_content("public-1")
        installed_entry = service.entry("public-1", include_uninstalled=False)
        cached_entry = service.entry("public-1", include_uninstalled=True)

        assert cached.installed is False
        assert cached.hasCachedContent is True
        assert installed_entry is None
        assert cached_entry is not None
        assert cached_entry.installed is False
        assert cached_entry.hasCachedContent is True
        assert cached_entry.record.spec == {"label": "Public One"}
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


def test_variant_sync_client_uses_env_base_url_when_settings_are_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787/")
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-env.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    assert client.base_url() == "http://127.0.0.1:8787"


def test_variant_sync_client_prefers_saved_base_url_over_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787")
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-env-override.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)
    client.set_base_url("https://preview-assetcloud.feel8.fun/")

    assert client.base_url() == "https://preview-assetcloud.feel8.fun"


def test_variant_logout_clears_local_session_when_remote_signout_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-sync-logout.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service)
        base_url = f"http://127.0.0.1:{server.server_port}"
        _ = client.login(base_url=base_url, username="u", password="p", remember=True)

        def _raise_signout_timeout(_path: str, _payload: dict[str, object], *, authorized: bool) -> dict[str, object]:
            assert authorized is True
            raise F8VariantRemoteRequestError("POST /api/auth/sign-out timed out after 10s")

        monkeypatch.setattr(client, "_post_json", _raise_signout_timeout)

        with caplog.at_level(logging.WARNING):
            client.logout()

        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.saved_sessions() == []
        assert "Variant remote sign-out failed; cleared local session anyway" in caplog.text
        assert "Traceback" not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_does_not_fallback_from_content_endpoint(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-no-fallback.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)
    calls: list[str] = []

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        del method, payload, authorized
        calls.append(path)
        raise F8VariantRemoteRequestError("missing", status_code=404)

    monkeypatch.setattr(client, "_request_json", _request_json)

    with pytest.raises(F8VariantRemoteRequestError):
        client.get_variant_content("public-1")

    assert calls == ["/v1/variants/public-1/content"]


def test_variant_sync_client_accepts_flat_content_payloads(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-flat-content.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        del method, payload, authorized
        assert path == "/v1/variants/public-1/content"
        now = variant_now_iso()
        return {
            "variantId": "public-1",
            "kind": "operator",
            "baseNodeType": "svc.a.op",
            "serviceClass": "svc.test",
            "operatorClass": "op.test",
            "name": "Flat Variant",
            "description": "",
            "tags": ["flat"],
            "spec": {"label": "Flat Variant"},
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    record = client.get_variant_content("public-1")

    assert record.variantId == "public-1"
    assert record.kind == F8VariantKind.operator
    assert record.spec == {"label": "Flat Variant"}


def test_variant_sync_client_accepts_summary_variant_payloads_without_record(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-summary.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        del method, path, payload, authorized
        now = variant_now_iso()
        return {
            "entries": [
                {
                    "variantId": "summary-1",
                    "variantKind": "service",
                    "baseNodeType": "svc.summary.node",
                    "serviceClass": "svc.summary.Service",
                    "operatorClass": None,
                    "name": "Summary Variant",
                    "description": "summary payload from remote",
                    "tags": ["summary", "remote"],
                    "visibility": "public",
                    "ownerUserId": "u2",
                    "ownerDisplayName": "Remote User",
                    "revision": "r-summary",
                    "latestRevision": "r-summary",
                    "latestVersionNumber": 7,
                    "createdAt": now,
                    "updatedAt": now,
                    "subscribed": True,
                }
            ],
            "nextCursor": None,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    page = client.list_variants(scope="community", kind="service", base_node_type="svc.summary.node")

    assert len(page.entries) == 1
    entry = page.entries[0]
    assert entry.record.variantId == "summary-1"
    assert entry.record.kind == F8VariantKind.service
    assert entry.record.baseNodeType == "svc.summary.node"
    assert entry.record.serviceClass == "svc.summary.Service"
    assert entry.record.operatorClass is None
    assert entry.record.spec == {}
    assert entry.remoteRevision == "r-summary"
    assert entry.remoteVersionNumber == 7
    assert entry.subscribed is True


def test_variant_sync_client_rejects_upload_without_full_spec(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-upload-guard.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-empty",
            kind=F8VariantKind.operator,
            baseNodeType="svc.a.op",
            serviceClass="svc.test",
            operatorClass="op.test",
            name="Broken Variant",
            description="",
            tags=[],
            spec={},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        installed=True,
    )

    with pytest.raises(ValueError, match="missing full spec content"):
        client.upload_entry(entry)


def test_variant_refresh_scope_page_preserves_cached_content_for_matching_revision(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-refresh.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    existing_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=True,
            remote_revision="r-public",
        ),
        update={
            "downloadedAt": "2026-04-07T00:00:00+00:00",
            "hasCachedContent": True,
            "remoteVersionNumber": 1,
        },
    )
    service.replace_remote_entries([existing_entry])

    incoming_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_revision="r-public",
        ),
        update={
            "record": copy_model(existing_entry.record, update={"spec": {}}),
            "visibility": F8VariantVisibility.public,
            "ownerUserId": "u2",
            "ownerDisplayName": "Remote User",
            "librarySlug": "community",
            "hasCachedContent": False,
            "downloadedAt": None,
            "remoteVersionNumber": 2,
            "syncState": F8VariantSyncState.synced,
        },
    )

    def _list_variants(
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
    ) -> F8VariantRemoteListPage:
        del scope, kind, base_node_type, query, cursor
        return F8VariantRemoteListPage(entries=[incoming_entry], nextCursor=None)

    monkeypatch.setattr(client, "list_variants", _list_variants)

    page = client.refresh_scope_page(scope="community", base_node_type="svc.a.op", append=False)

    assert len(page.entries) == 1
    refreshed_entry = service.entry("public-1", include_uninstalled=True)
    assert refreshed_entry is not None
    assert refreshed_entry.installed is True
    assert refreshed_entry.hasCachedContent is True
    assert refreshed_entry.downloadedAt == "2026-04-07T00:00:00+00:00"
    assert refreshed_entry.record.spec == {"label": "public-1"}
    assert refreshed_entry.remoteVersionNumber == 2


def test_variant_refresh_scope_page_preserves_cached_preview_without_marking_installed(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-refresh-cached.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    existing_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_revision="r-public",
        ),
        update={
            "downloadedAt": "2026-04-07T00:00:00+00:00",
            "hasCachedContent": True,
            "remoteVersionNumber": 1,
        },
    )
    service.replace_remote_entries([existing_entry])

    incoming_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_revision="r-public",
        ),
        update={
            "record": copy_model(existing_entry.record, update={"spec": {}}),
            "visibility": F8VariantVisibility.public,
            "ownerUserId": "u2",
            "ownerDisplayName": "Remote User",
            "librarySlug": "community",
            "hasCachedContent": False,
            "downloadedAt": None,
            "remoteVersionNumber": 2,
            "syncState": F8VariantSyncState.synced,
        },
    )

    def _list_variants(
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
    ) -> F8VariantRemoteListPage:
        del scope, kind, base_node_type, query, cursor
        return F8VariantRemoteListPage(entries=[incoming_entry], nextCursor=None)

    monkeypatch.setattr(client, "list_variants", _list_variants)

    _ = client.refresh_scope_page(scope="community", base_node_type="svc.a.op", append=False)

    refreshed_entry = service.entry("public-1", include_uninstalled=True)
    assert refreshed_entry is not None
    assert refreshed_entry.installed is False
    assert refreshed_entry.hasCachedContent is True
    assert refreshed_entry.downloadedAt == "2026-04-07T00:00:00+00:00"
    assert refreshed_entry.record.spec == {"label": "public-1"}
    assert service.entry("public-1", include_uninstalled=False) is None


def test_variant_upload_replays_missing_local_history_versions(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-history.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    first = service.upsert_local_entry(
        F8VariantEntry(
            record=F8VariantRecord(
                variantId="history-1",
                kind=F8VariantKind.operator,
                baseNodeType="svc.a.op",
                serviceClass="svc.test",
                operatorClass="op.test",
                name="History Variant",
                description="",
                tags=[],
                spec={"label": "v1"},
                createdAt=variant_now_iso(),
                updatedAt=variant_now_iso(),
            ),
            source=F8VariantSourceKind.local,
        )
    )
    second = service.upsert_local_entry(copy_model(first, update={"record": copy_model(first.record, update={"spec": {"label": "v2"}})}))
    third = service.upsert_local_entry(copy_model(second, update={"record": copy_model(second.record, update={"spec": {"label": "v3"}})}))

    remote_entry = copy_model(
        _make_entry(
            variant_id="history-1",
            source=F8VariantSourceKind.remote_private,
            installed=True,
            remote_revision="r1",
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "remoteVersionNumber": 1,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])

    request_payloads: list[dict[str, object]] = []

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        assert authorized is True
        assert method == "PUT"
        assert path == "/v1/variants/history-1"
        assert payload is not None
        request_payloads.append(payload)
        next_version = len(request_payloads) + 1
        record_payload = payload["record"]
        assert isinstance(record_payload, dict)
        return {
            "record": record_payload,
            "variantId": "history-1",
            "assetType": "variant",
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
            "visibility": "private",
            "revision": f"r{next_version}",
            "latestRevision": f"r{next_version}",
            "latestVersionNumber": next_version,
            "createdAt": str(record_payload["createdAt"]),
            "updatedAt": str(record_payload["updatedAt"]),
            "installed": True,
            "hasCachedContent": True,
            "subscribed": False,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    uploaded = client.upload_entry(third)

    assert [payload["changeSummary"] for payload in request_payloads] == [
        "Sync local variant history v2",
        "Sync local variant history v3",
    ]
    assert [payload["record"]["spec"]["label"] for payload in request_payloads] == ["v2", "v3"]
    assert uploaded.remoteVersionNumber == 3
    assert uploaded.remoteRevision == "r3"


def test_variant_remote_cache_load_cleans_empty_variant_ids(tmp_path: Path) -> None:
    provider = RemoteCacheProvider(db_path=tmp_path / "assets.db")
    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(variant_remote_cache_table).values(
                variant_id="",
                name="Broken Row",
                description="",
                tags_json="[]",
                kind="operator",
                base_node_type="svc.a.op",
                service_class="svc.test",
                operator_class="op.test",
                remote_version_number=1,
                created_at="2026-04-04T00:00:00+00:00",
                updated_at="2026-04-04T00:00:00+00:00",
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                library_slug="community",
                remote_revision="r1",
                sync_state="synced",
                downloaded_at=None,
                installed=0,
                has_cached_content=0,
                subscribed=0,
                content=zlib.compress(b"{}", level=6, wbits=31),
            )
        )

    assert provider.load_entries() == []

    with provider._db.connect_sqla() as conn:
        rows = conn.execute(select(variant_remote_cache_table.c.variant_id)).all()
    assert rows == []


def test_variant_remote_cache_row_with_spec_loads_as_installed(tmp_path: Path) -> None:
    service = VariantCatalogService(db_path=tmp_path / "assets.db")
    provider = service._remote_provider
    entry = _make_entry(variant_id="remote-1", source=F8VariantSourceKind.remote_public, installed=True, remote_revision="r1")

    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(variant_remote_cache_table).values(
                variant_id="remote-1",
                name=str(entry.record.name),
                description=str(entry.record.description),
                tags_json=json.dumps(list(entry.record.tags or [])),
                kind=str(entry.record.kind.value),
                base_node_type=str(entry.record.baseNodeType),
                service_class=str(entry.record.serviceClass),
                operator_class=str(entry.record.operatorClass),
                remote_version_number=2,
                created_at=str(entry.record.createdAt),
                updated_at=str(entry.record.updatedAt),
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                library_slug="community",
                remote_revision="r1",
                sync_state="synced",
                downloaded_at="2026-04-04T00:00:00+00:00",
                installed=1,
                has_cached_content=1,
                subscribed=0,
                content=zlib.compress(json.dumps(entry.record.spec).encode("utf-8"), level=6, wbits=31),
            )
        )

    loaded = service.entry("remote-1", include_uninstalled=False)
    assert loaded is not None
    assert loaded.record.variantId == "remote-1"
    assert loaded.installed is True
    assert loaded.hasCachedContent is True
    assert loaded.remoteVersionNumber == 2
    assert loaded.record.spec == {"label": "remote-1"}


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
    synced_state = variant_row_state_for_entries(
        variant_id="asset-4",
        local_entry=copy_model(_make_entry(variant_id="asset-4", source=F8VariantSourceKind.local), update={"localVersionNumber": 6}),
        remote_entry=copy_model(
            _make_entry(variant_id="asset-4", source=F8VariantSourceKind.remote_public, installed=True, remote_revision="r1"),
            update={"visibility": F8VariantVisibility.public, "remoteVersionNumber": 6},
        ),
    )
    local_changes_state = variant_row_state_for_entries(
        variant_id="asset-5",
        local_entry=copy_model(_make_entry(variant_id="asset-5", source=F8VariantSourceKind.local), update={"localVersionNumber": 7}),
        remote_entry=copy_model(
            _make_entry(variant_id="asset-5", source=F8VariantSourceKind.remote_public, installed=True, remote_revision="r1"),
            update={"visibility": F8VariantVisibility.public, "remoteVersionNumber": 6},
        ),
    )

    assert both_state.badge_texts() == ["both", "public", "remote newer", "L4", "R6"]
    assert conflict_state.badge_texts() == ["both", "public", "conflict", "R3"]
    assert remote_state.badge_texts() == ["remote", "public", "R2"]
    assert synced_state.badge_texts() == ["both", "public", "synced", "L6", "R6"]
    assert local_changes_state.badge_texts() == ["both", "public", "local changes", "L7", "R6"]
