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

from f8pystudio.assets.common import decode_http_response_text
from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRemoteRequestError,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from f8pystudio.assets.components.component_sync import ComponentSyncClient
from f8pystudio.assets.db import component_remote_cache_table
from f8pystudio.assets.ui.component_catalog_dialog import component_row_state_for_entries


def _component_record(component_id: str, name: str) -> dict[str, object]:
    return {
        "componentId": component_id,
        "name": name,
        "description": "",
        "tags": [],
        "schemaVersion": "f8studio-session/1",
        "content": {
            "schemaVersion": "f8studio-session/1",
            "layout": {
                "nodes": {
                    component_id: {
                        "id": component_id,
                        "name": name,
                        "pos": [0, 0],
                    }
                },
                "connections": [],
            },
        },
        "createdAt": "2026-04-02T00:00:00+00:00",
        "updatedAt": "2026-04-02T00:00:00+00:00",
    }


class _ComponentApiHandler(BaseHTTPRequestHandler):
    server_version = "ComponentApiTest/1.0"

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
        self._write_bytes(status, body, set_cookie=set_cookie)

    def _write_bytes(
        self,
        status: int,
        body: bytes,
        *,
        set_cookie: str | None = None,
        content_type: str = "application/json",
        content_encoding: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
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
        if self.path == "/v1/components/public-1/subscribe":
            if not self._check_auth():
                return
            self._write_json(200, self.server.asset_payload(self.server.public_record, visibility="public", subscribed=True))
            return
        if self.path == "/v1/components":
            if not self._check_auth():
                return
            payload = self._read_json()
            self.server.last_create_payload = payload
            record = payload.get("record")
            if not isinstance(record, dict):
                self._write_json(400, {"message": "bad record"})
                return
            visibility = str(payload.get("visibility") or "private")
            self._write_json(200, self.server.asset_payload(record, visibility=visibility))
            return
        self._write_json(404, {"message": "missing"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/v1/components?"):
            cookie = str(self.headers.get("Cookie") or "")
            if "owner=me" in self.path:
                if not self._check_auth():
                    return
                self._write_json(200, {"entries": [self.server.private_asset_summary], "nextCursor": None})
                return
            if "owner=public" in self.path and not cookie:
                self._write_json(200, {"entries": [self.server.public_asset_summary], "nextCursor": None})
                return
            if not self._check_auth():
                return
            self._write_json(200, {"entries": [self.server.public_asset_summary], "nextCursor": None})
            return
        if self.path == "/v1/me":
            if not self._check_auth():
                return
            self._write_json(200, {"userId": "u1", "username": "u", "displayName": "User One"})
            return
        if self.path == "/v1/components/public-1":
            self._write_json(200, self.server.public_asset)
            return
        if self.path == "/v1/components/public-1/content":
            payload = {
                "componentId": "public-1",
                "assetType": "component",
                "versionNumber": 1,
                "revision": "r-public",
                "record": self.server.public_record,
            }
            compressed_once = zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), level=6, wbits=31)
            self._write_bytes(200, compressed_once, content_encoding="gzip")
            return
        if self.path == "/v1/components/public-1/versions":
            self._write_json(
                200,
                {
                    "versions": [
                        {
                            "componentId": "public-1",
                            "assetType": "component",
                            "versionNumber": 1,
                            "revision": "r-public",
                            "createdAt": str(self.server.public_record["createdAt"]),
                            "createdByUserId": "u2",
                            "changeSummary": None,
                        }
                    ]
                },
            )
            return
        if self.path == "/v1/components/public-1/versions/1":
            self._write_json(200, self.server.public_asset)
            return
        if self.path == "/v1/components/public-1/versions/1/content":
            self._write_json(
                200,
                {
                    "componentId": "public-1",
                    "assetType": "component",
                    "versionNumber": 1,
                    "revision": "r-public",
                    "record": self.server.public_record,
                },
            )
            return
        self._write_json(404, {"message": "missing"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path == "/v1/components/public-1/subscribe":
            if not self._check_auth():
                return
            self._write_json(200, self.server.asset_payload(self.server.public_record, visibility="public", subscribed=False))
            return
        self._write_json(404, {"message": "missing"})


class _Server(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int]):
        super().__init__(server_address, _ComponentApiHandler)
        self.last_login_user_agent = ""
        self.last_signout_origin = ""
        self.public_record = _component_record("public-1", "Public Component")
        self.private_record = _component_record("private-1", "Private Component")
        self.public_asset = self.asset_payload(self.public_record, visibility="public")
        self.private_asset = self.asset_payload(self.private_record, visibility="private")
        self.public_asset_summary = self.asset_summary_payload(self.public_record, visibility="public")
        self.private_asset_summary = self.asset_summary_payload(self.private_record, visibility="private")
        self.last_create_payload: dict[str, object] = {}

    @staticmethod
    def asset_payload(
        record: dict[str, object],
        *,
        visibility: str,
        subscribed: bool = False,
    ) -> dict[str, object]:
        return {
            "componentId": str(record["componentId"]),
            "assetType": "component",
            "ownerUserId": "u2" if visibility == "public" else "u1",
            "ownerDisplayName": "Remote User" if visibility == "public" else "User One",
            "visibility": visibility,
            "revision": "r-public" if visibility == "public" else "r-private",
            "latestRevision": "r-public" if visibility == "public" else "r-private",
            "versionNumber": 1,
            "latestVersionNumber": 1,
            "createdAt": str(record["createdAt"]),
            "updatedAt": str(record["updatedAt"]),
            "editable": visibility != "public",
            "subscribed": subscribed,
            "record": record,
        }

    @staticmethod
    def asset_summary_payload(
        record: dict[str, object],
        *,
        visibility: str,
        subscribed: bool = False,
    ) -> dict[str, object]:
        return {
            "componentId": str(record["componentId"]),
            "assetType": "component",
            "ownerUserId": "u2" if visibility == "public" else "u1",
            "ownerDisplayName": "Remote User" if visibility == "public" else "User One",
            "visibility": visibility,
            "revision": "r-public" if visibility == "public" else "r-private",
            "latestRevision": "r-public" if visibility == "public" else "r-private",
            "versionNumber": 1,
            "latestVersionNumber": 1,
            "name": str(record["name"]),
            "description": str(record["description"]),
            "tags": list(record["tags"]),
            "schemaVersion": str(record["schemaVersion"]),
            "createdAt": str(record["createdAt"]),
            "updatedAt": str(record["updatedAt"]),
            "editable": visibility != "public",
            "subscribed": subscribed,
            "record": {
                "componentId": str(record["componentId"]),
                "name": str(record["name"]),
                "description": str(record["description"]),
                "tags": list(record["tags"]),
                "schemaVersion": str(record["schemaVersion"]),
                "content": {},
                "createdAt": str(record["createdAt"]),
                "updatedAt": str(record["updatedAt"]),
            },
        }


def test_component_sync_client_supports_anonymous_public_install_and_cookie_sessions(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "component-sync.ini"), QtCore.QSettings.IniFormat)
        service = ComponentCatalogService(db_path=tmp_path / "assets.db")
        client = ComponentSyncClient(settings=settings, catalog_service=service)
        client.set_base_url(f"http://127.0.0.1:{server.server_port}")

        anonymous_page = client.list_components(scope="community")
        assert anonymous_page.entries[0].record.componentId == "public-1"
        assert anonymous_page.entries[0].source == F8ComponentSourceKind.remote_public
        assert anonymous_page.entries[0].remoteVersionNumber == 1

        installed = client.install_component("public-1")
        assert installed.installed is True
        assert service.entry("public-1", include_uninstalled=False) is not None
        public_version = client.get_component_version("public-1", 1)
        assert public_version.record.componentId == "public-1"

        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", username="u", password="p", remember=True)
        assert auth.user.displayName == "User One"
        assert server.last_login_user_agent == "F8Studio/1.0"

        refreshed_page = client.list_components(scope="community")
        assert refreshed_page.entries[0].record.componentId == "public-1"
        assert client.current_access_token() == "session=active-1"
        cached_public = service.entry("public-1", include_uninstalled=True)
        assert cached_public is not None
        assert isinstance(cached_public.record.content.get("layout"), dict)
        assert cached_public.remoteVersionNumber == 1

        mine_page = client.list_components(scope="mine")
        assert mine_page.entries[0].record.componentId == "private-1"
        assert mine_page.entries[0].source == F8ComponentSourceKind.remote_private
        assert mine_page.entries[0].installed is False
        remote_versions = client.list_component_versions("public-1")
        assert remote_versions.versions[0].versionNumber == 1

        subscribed = client.subscribe_component("public-1")
        assert subscribed.subscribed is True
        unsubscribed = client.unsubscribe_component("public-1")
        assert unsubscribed.subscribed is False

        historical_entry = client.get_component_version("public-1", 1)
        forked_entry = F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="forked-1",
                name="Forked Component",
                description=historical_entry.record.description,
                tags=list(historical_entry.record.tags),
                content=historical_entry.record.content,
            ),
            source=F8ComponentSourceKind.local,
            installed=True,
        )
        created = client.fork_component(
            source_component_id="public-1",
            forked_entry=forked_entry,
            visibility=F8ComponentVisibility.private,
            version_number=1,
        )
        assert created.record.componentId == "forked-1"
        assert created.source == F8ComponentSourceKind.remote_private
        assert server.last_create_payload["changeSummary"] == "Forked from public-1 v1"
        assert isinstance(server.last_create_payload["record"], dict)
        assert server.last_create_payload["record"]["name"] == "Forked Component"
        client.logout()
        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.saved_sessions() == []
        assert server.last_signout_origin == f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_component_sync_client_drops_legacy_saved_sessions_without_crashing(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "component-sync-legacy.ini"), QtCore.QSettings.IniFormat)
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

    client = ComponentSyncClient(settings=settings, catalog_service=ComponentCatalogService(db_path=tmp_path / "assets.db"))

    assert client.saved_sessions() == []
    assert client.current_session() is None


def test_decode_http_response_text_only_decodes_one_gzip_layer() -> None:
    payload = json.dumps({"message": "ok"}, ensure_ascii=False).encode("utf-8")
    compressed_once = zlib.compress(payload, level=6, wbits=31)
    compressed_twice = zlib.compress(compressed_once, level=6, wbits=31)

    assert decode_http_response_text(compressed_once, content_encoding="gzip") == payload.decode("utf-8")
    decoded = decode_http_response_text(compressed_twice, content_encoding="gzip")
    assert decoded != payload.decode("utf-8")


def test_component_sync_client_does_not_fallback_from_content_endpoint(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "component-sync-no-fallback.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    client = ComponentSyncClient(settings=settings, catalog_service=service)
    calls: list[str] = []

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        del method, payload, authorized
        calls.append(path)
        raise F8ComponentRemoteRequestError("missing", status_code=404)

    monkeypatch.setattr(client, "_request_json", _request_json)

    with pytest.raises(F8ComponentRemoteRequestError):
        client.get_component_content("public-1")

    assert calls == ["/v1/components/public-1/content"]


def test_component_sync_client_accepts_flat_content_payloads(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "component-sync-flat-content.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    client = ComponentSyncClient(settings=settings, catalog_service=service)

    def _request_json(method: str, path: str, payload: dict[str, object] | None, *, authorized: bool) -> dict[str, object]:
        del method, payload, authorized
        assert path == "/v1/components/public-1/content"
        now = component_now_iso()
        return {
            "componentId": "public-1",
            "name": "Flat Content Component",
            "description": "",
            "tags": ["flat"],
            "schemaVersion": "f8studio-session/1",
            "content": {
                "schemaVersion": "f8studio-session/1",
                "layout": {"nodes": {}, "connections": []},
            },
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    record = client.get_component_content("public-1")

    assert record.componentId == "public-1"
    assert record.schemaVersion == "f8studio-session/1"
    assert record.content["schemaVersion"] == "f8studio-session/1"


def test_component_sync_client_rejects_upload_without_full_content(tmp_path: Path) -> None:
    settings = QtCore.QSettings(str(tmp_path / "component-sync-upload-guard.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    client = ComponentSyncClient(settings=settings, catalog_service=service)

    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-empty",
            name="Empty Component",
            description="",
            tags=[],
            schemaVersion="f8studio-session/1",
            content={},
            createdAt="2026-04-07T00:00:00+00:00",
            updatedAt="2026-04-07T00:00:00+00:00",
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )

    with pytest.raises(ValueError, match="missing full content"):
        client.upload_entry(entry)


def test_component_logout_clears_local_session_when_remote_signout_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "component-sync-logout.ini"), QtCore.QSettings.IniFormat)
        service = ComponentCatalogService(db_path=tmp_path / "assets.db")
        client = ComponentSyncClient(settings=settings, catalog_service=service)
        base_url = f"http://127.0.0.1:{server.server_port}"
        _ = client.login(base_url=base_url, username="u", password="p", remember=True)

        def _raise_signout_timeout(_path: str, _payload: dict[str, object], *, authorized: bool) -> dict[str, object]:
            assert authorized is True
            raise F8ComponentRemoteRequestError("POST /api/auth/sign-out timed out after 10s")

        monkeypatch.setattr(client, "_post_json", _raise_signout_timeout)

        with caplog.at_level(logging.WARNING):
            client.logout()

        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.saved_sessions() == []
        assert "Component remote sign-out failed; cleared local session anyway" in caplog.text
        assert "Traceback" not in caplog.text
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_component_remote_cache_load_cleans_empty_component_ids(tmp_path: Path) -> None:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    provider = service._remote_provider
    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(component_remote_cache_table).values(
                component_id="",
                name="Broken Row",
                description="",
                tags_json="[]",
                schema_version="f8studio-session/1",
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
        rows = conn.execute(select(component_remote_cache_table.c.component_id)).all()
    assert rows == []


def test_component_remote_cache_row_with_content_loads_as_installed(tmp_path: Path) -> None:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    provider = service._remote_provider
    canonical_record = _component_record("remote-1", "Remote Installed Component")
    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(component_remote_cache_table).values(
                component_id="remote-1",
                name=str(canonical_record["name"]),
                description=str(canonical_record["description"]),
                tags_json=json.dumps(canonical_record["tags"]),
                schema_version=str(canonical_record["schemaVersion"]),
                remote_version_number=1,
                created_at=str(canonical_record["createdAt"]),
                updated_at=str(canonical_record["updatedAt"]),
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
                content=zlib.compress(json.dumps(canonical_record["content"]).encode("utf-8"), level=6, wbits=31),
            )
        )

    loaded = service.entry("remote-1", include_uninstalled=False)
    assert loaded is not None
    assert loaded.record.componentId == "remote-1"
    assert loaded.installed is True
    assert loaded.hasCachedContent is True
    assert loaded.remoteVersionNumber == 1
    assert loaded.record.content["schemaVersion"] == "f8studio-session/1"


def test_component_row_state_badges_cover_local_remote_and_both() -> None:
    local_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="asset-1", name="Local"),
        source=F8ComponentSourceKind.local,
        localVersionNumber=3,
    )
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="asset-1",
            name="Remote",
            schemaVersion="f8studio-session/1",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
        ),
        source=F8ComponentSourceKind.remote_public,
        visibility=F8ComponentVisibility.public,
        installed=True,
        remoteVersionNumber=5,
    )

    both_state = component_row_state_for_entries(
        component_id="asset-1",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )
    remote_state = component_row_state_for_entries(
        component_id="asset-2",
        local_entry=None,
        remote_entry=F8ComponentEntry(
            record=F8ComponentRecord(componentId="asset-2", name="Remote Only"),
            source=F8ComponentSourceKind.remote_public,
            visibility=F8ComponentVisibility.public,
            installed=False,
            remoteVersionNumber=2,
        ),
    )
    local_state = component_row_state_for_entries(
        component_id="asset-3",
        local_entry=F8ComponentEntry(
            record=F8ComponentRecord(componentId="asset-3", name="Local Only"),
            source=F8ComponentSourceKind.local,
            localVersionNumber=1,
        ),
        remote_entry=None,
    )

    assert both_state.badge_texts() == ["both", "public", "synced", "L3", "R5"]
    assert remote_state.badge_texts() == ["remote", "public", "R2"]
    assert local_state.badge_texts() == ["local", "L1"]
