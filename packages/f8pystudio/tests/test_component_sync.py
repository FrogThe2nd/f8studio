from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import zlib

import pytest
from qtpy import QtCore, QtWidgets
from sqlalchemy import insert, select
from f8pysdk.codec import copy_model

from f8pystudio.assets.common import decode_http_response_text, redact_http_body_for_log, redact_json_for_log
from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_models import (
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRemoteUser,
    F8ComponentRemoteRequestError,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from f8pystudio.assets.components.component_sync import ComponentSyncClient
from f8pystudio.assets.db import component_remote_cache_table
from f8pystudio.assets.ui.component_catalog_dialog import ComponentCatalogDialog
from f8pystudio.nodegraph.graph_component_actions import GraphComponentActionsMixin


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


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class _GraphComponentSaveHost(GraphComponentActionsMixin):
    def __init__(self) -> None:
        self._parent = QtWidgets.QWidget()

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return self._parent

    def context_nodes_menu(self) -> None:
        return None

    def selected_nodes(self) -> list[object]:
        return [type("SelectedNode", (), {"id": "node-a", "name": lambda self: "Node A"})()]

    def serialize_publish_session(self) -> dict[str, object]:
        return {
            "schemaVersion": "f8studio-session/1",
            "layout": {
                "nodes": {"node-a": {"id": "node-a", "name": "Node A"}},
                "connections": [],
            },
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
        if self.path == "/api/auth/sign-in/email":
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
            self._write_json(200, {"userId": "u1", "name": "User One", "displayName": "User One", "email": "u@example.com"})
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
        if self.path == "/v1/components/private-1":
            if not self._check_auth():
                return
            self.server.deleted_component_ids.append("private-1")
            self._write_json(200, {"ok": True})
            return
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
        self.deleted_component_ids: list[str] = []

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

        installed = client.install_component("public-1")
        assert installed.installed is True
        assert service.entry("public-1", include_uninstalled=False) is not None
        public_version = client.get_component_version("public-1", 1)
        assert public_version.record.componentId == "public-1"

        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", email="u@example.com", password="p", remember=True)
        assert auth.user.displayName == "User One"
        assert server.last_login_user_agent == "F8Studio/1.0"

        refreshed_page = client.list_components(scope="community")
        assert refreshed_page.entries[0].record.componentId == "public-1"
        assert client.current_access_token() == "session=active-1"
        cached_public = service.entry("public-1", include_uninstalled=True)
        assert cached_public is not None
        assert isinstance(cached_public.record.content.get("layout"), dict)

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
        server.server_close()
        thread.join(timeout=5)


def test_component_sync_client_can_cache_content_without_installing(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "component-cache.ini"), QtCore.QSettings.IniFormat)
        service = ComponentCatalogService(db_path=tmp_path / "assets.db")
        client = ComponentSyncClient(settings=settings, catalog_service=service)
        client.set_base_url(f"http://127.0.0.1:{server.server_port}")

        cached = client.cache_component_content("public-1")

        assert cached.installed is False
        assert cached.hasCachedContent is True
        assert isinstance(cached.record.content.get("layout"), dict)
        assert service.entry("public-1", include_uninstalled=False) is None
        cached_entry = service.entry("public-1", include_uninstalled=True)
        assert cached_entry is not None
        assert cached_entry.installed is False
        assert cached_entry.hasCachedContent is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_component_sync_client_rejects_legacy_saved_sessions(tmp_path: Path) -> None:
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
                    "email": "legacy@example.com",
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

    with pytest.raises(ValueError, match="sessionCookie"):
        client.saved_sessions()
    with pytest.raises(ValueError, match="sessionCookie"):
        client.current_session()


def test_component_sync_client_uses_env_base_url_when_settings_are_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787/")
    settings = QtCore.QSettings(str(tmp_path / "component-sync-env.ini"), QtCore.QSettings.IniFormat)
    client = ComponentSyncClient(settings=settings, catalog_service=ComponentCatalogService(db_path=tmp_path / "assets.db"))

    assert client.base_url() == "http://127.0.0.1:8787"


def test_component_sync_client_prefers_saved_base_url_over_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787")
    settings = QtCore.QSettings(str(tmp_path / "component-sync-env-override.ini"), QtCore.QSettings.IniFormat)
    client = ComponentSyncClient(settings=settings, catalog_service=ComponentCatalogService(db_path=tmp_path / "assets.db"))
    client.set_base_url("https://preview-assetcloud.feel8.fun/")

    assert client.base_url() == "https://preview-assetcloud.feel8.fun"


def test_decode_http_response_text_only_decodes_one_gzip_layer() -> None:
    payload = json.dumps({"message": "ok"}, ensure_ascii=False).encode("utf-8")
    compressed_once = zlib.compress(payload, level=6, wbits=31)
    compressed_twice = zlib.compress(compressed_once, level=6, wbits=31)

    assert decode_http_response_text(compressed_once, content_encoding="gzip") == payload.decode("utf-8")
    decoded = decode_http_response_text(compressed_twice, content_encoding="gzip")
    assert decoded != payload.decode("utf-8")


def test_redact_http_body_for_log_hides_auth_payload_secrets() -> None:
    raw = json.dumps(
        {
            "token": "plain-token",
            "user": {"email": "user@example.com"},
            "nested": {
                "sessionCookie": "session=secret",
                "accessToken": "old-token-only",
            },
        },
    )

    redacted = redact_http_body_for_log(raw, max_chars=1000)

    assert "plain-token" not in redacted
    assert "session=secret" not in redacted
    assert "old-token-only" not in redacted
    assert "[redacted]" in redacted
    assert "user@example.com" in redacted


def test_redact_json_for_log_hides_header_style_secret_keys() -> None:
    redacted = redact_json_for_log(
        {
            "Authorization": "Bearer secret",
            "Set-Cookie": "session=secret",
            "safe": "visible",
        },
    )

    assert redacted == {
        "Authorization": "[redacted]",
        "Set-Cookie": "[redacted]",
        "safe": "visible",
    }


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
        _ = client.login(base_url=base_url, email="u@example.com", password="p", remember=True)

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
        server.server_close()
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
                created_at="2026-04-04T00:00:00+00:00",
                updated_at="2026-04-04T00:00:00+00:00",
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                remote_revision="r1",
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
                created_at=str(canonical_record["createdAt"]),
                updated_at=str(canonical_record["updatedAt"]),
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                remote_revision="r1",
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
    assert loaded.record.content["schemaVersion"] == "f8studio-session/1"


def test_component_row_state_badges_cover_local_remote_and_both() -> None:
    local_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="asset-1", name="Local"),
        source=F8ComponentSourceKind.local,
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
        remoteRevision="r5",
    )

    both_state = ComponentCatalogDialog._component_row_state_for_entries(
        component_id="asset-1",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )
    remote_state = ComponentCatalogDialog._component_row_state_for_entries(
        component_id="asset-2",
        local_entry=None,
        remote_entry=F8ComponentEntry(
            record=F8ComponentRecord(componentId="asset-2", name="Remote Only"),
            source=F8ComponentSourceKind.remote_public,
            visibility=F8ComponentVisibility.public,
            installed=False,
        ),
    )
    local_state = ComponentCatalogDialog._component_row_state_for_entries(
        component_id="asset-3",
        local_entry=F8ComponentEntry(
            record=F8ComponentRecord(componentId="asset-3", name="Local Only"),
            source=F8ComponentSourceKind.local,
        ),
        remote_entry=None,
    )

    assert both_state.badge_texts() == ["both", "public"]
    assert remote_state.badge_texts() == ["remote", "public"]
    assert local_state.badge_texts() == ["local"]


def test_component_row_state_uses_local_draft_owner_label() -> None:
    local_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="draft-component", name="Draft Component"),
        source=F8ComponentSourceKind.local,
        isLocalDraft=True,
    )

    row_state = ComponentCatalogDialog._component_row_state_for_entries(
        component_id="draft-component",
        local_entry=local_entry,
        remote_entry=None,
    )

    assert row_state.owner_display_name == "Local Draft"


def test_component_row_state_prefers_remote_owner_when_remote_head_exists() -> None:
    local_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="draft-component-remote", name="Draft Component Remote"),
        source=F8ComponentSourceKind.local,
        isLocalDraft=True,
    )
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="draft-component-remote", name="Draft Component Remote"),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        remoteRevision="r1",
        installed=True,
        hasCachedContent=True,
    )

    row_state = ComponentCatalogDialog._component_row_state_for_entries(
        component_id="draft-component-remote",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )

    assert row_state.owner_display_name == "User One"


def test_component_catalog_load_owned_remote_keeps_remote_only_cache(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-load.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="owned-component",
            name="Owned Component",
            schemaVersion="f8studio-session/1",
            content={},
            createdAt="2026-04-02T00:00:00+00:00",
            updatedAt="2026-04-02T00:00:00+00:00",
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        remoteRevision="r4",
        installed=False,
        hasCachedContent=False,
    )
    service.replace_remote_entries([remote_entry])

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_selected_action_entries", lambda: (remote_entry, None, remote_entry))
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8ComponentRemoteUser(userId="u1", name="User One", displayName="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "hydrate_component",
        lambda _component_id: service.install_remote_entry(
            copy_model(
                remote_entry,
                update={
                    "installed": True,
                    "hasCachedContent": True,
                    "record": copy_model(
                        remote_entry.record,
                        update={
                            "content": {
                                "schemaVersion": "f8studio-session/1",
                                "layout": {"nodes": {}, "connections": []},
                            }
                        },
                    ),
                },
            )
        ),
    )

    dialog._on_install_clicked()

    cached_entry = service.entry("owned-component", include_uninstalled=True)
    assert cached_entry is not None
    assert cached_entry.source == F8ComponentSourceKind.remote_private
    assert cached_entry.installed is True
    assert cached_entry.hasCachedContent is True
    assert dialog._draft_service_for_catalog().draft_for_publish_target("owned-component") is None

    dialog.close()


def test_component_publish_new_draft_keeps_linked_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-push-draft.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    local_entry = service.upsert_local_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="draft-component",
                name="Draft Component",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8ComponentDraftOriginKind.new,
        )
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_choose_visibility", lambda: F8ComponentVisibility.private)
    monkeypatch.setattr(dialog, "_ensure_component_hydrated", lambda entry, operation_name: entry)

    create_calls: list[F8ComponentEntry] = []

    def _create_component(entry: F8ComponentEntry) -> F8ComponentEntry:
        create_calls.append(entry)
        uploaded_entry = copy_model(
            entry,
            update={
                "source": F8ComponentSourceKind.remote_private,
                "visibility": F8ComponentVisibility.private,
                "ownerUserId": "u1",
                "ownerDisplayName": "User One",
                "remoteRevision": "r1",
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(dialog._sync_client, "create_component", _create_component)

    uploaded = dialog._publish_component_draft(local_entry)

    assert uploaded is not None
    assert len(create_calls) == 1
    saved_draft = dialog._draft_service_for_catalog().draft("draft-component")
    assert saved_draft is not None
    assert saved_draft.publishTargetAssetId == str(uploaded.record.componentId)
    assert saved_draft.publishBaseRemoteRevision == "r1"
    saved_local = service.entry("draft-component", include_uninstalled=True)
    assert saved_local is not None
    assert saved_local.isLocalDraft is True
    assert saved_local.draftOriginAssetId == str(uploaded.record.componentId)
    assert saved_local.draftOriginRevision == "r1"

    dialog.close()


def test_component_upload_clicked_publishes_selected_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-upload-clicked.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    draft_entry = service.upsert_local_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="draft-upload-clicked",
                name="Draft Upload Clicked",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8ComponentDraftOriginKind.new,
        )
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    dialog._entries = [draft_entry]
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_DRAFTS)
    monkeypatch.setattr(dialog, "_selected_entry", lambda: draft_entry)

    publish_calls: list[F8ComponentEntry] = []
    info_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dialog,
        "_publish_component_draft",
        lambda entry: publish_calls.append(entry) or entry,
    )
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_catalog_actions_mixin.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    assert dialog._selected_local_entry() is not None

    dialog._on_upload_clicked()

    assert publish_calls == [draft_entry]
    assert info_messages == [("Published", "Published draft:\nDraft Upload Clicked")]

    dialog.close()


def test_component_publish_metadata_only_uses_patch_meta(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-sync-noop.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    local_entry = service.upsert_local_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="linked-draft",
                name="Owned Overwrite Draft",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8ComponentDraftOriginKind.copy_remote,
            draftOriginAssetId="owned-overwrite",
            draftOriginRevision="r4",
        )
    )
    remote_entry = service.install_remote_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="owned-overwrite",
                name="Owned Overwrite",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.remote_private,
            visibility=F8ComponentVisibility.private,
            ownerUserId="u1",
            ownerDisplayName="User One",
            remoteRevision="r4",
            installed=True,
            hasCachedContent=True,
        )
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_ensure_component_hydrated", lambda entry, operation_name: entry)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8ComponentRemoteUser(userId="u1", name="User One", displayName="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    patch_calls: list[tuple[str, str, str, list[str]]] = []
    update_calls: list[F8ComponentEntry] = []

    def _patch_component_meta(component_id: str, *, name: str, description: str, tags: list[str]) -> F8ComponentEntry:
        patch_calls.append((str(component_id), str(name), str(description), list(tags)))
        uploaded_entry = copy_model(
            remote_entry,
            update={
                "record": copy_model(remote_entry.record, update={"name": name, "description": description, "tags": tags}),
                "remoteRevision": "r5",
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(dialog._sync_client, "patch_component_meta", _patch_component_meta)
    monkeypatch.setattr(dialog._sync_client, "update_component", lambda entry: update_calls.append(entry) or entry)

    published = dialog._publish_component_draft(local_entry)

    assert published is not None
    assert patch_calls == [("owned-overwrite", "Owned Overwrite Draft", "", [])]
    assert update_calls == []
    saved_local = service.entry("linked-draft", include_uninstalled=True)
    saved_remote = service.remote_entry("owned-overwrite")
    assert saved_local is not None
    assert saved_remote is not None
    assert saved_local.isLocalDraft is True
    assert saved_local.draftOriginAssetId == "owned-overwrite"
    assert saved_local.draftOriginRevision == "r5"
    assert saved_remote.remoteRevision == "r5"

    dialog.close()


def test_component_publish_missing_linked_asset_can_create_replacement(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-missing-linked.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    local_entry = service.upsert_local_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="draft-linked-missing",
                name="Draft Linked Missing",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8ComponentDraftOriginKind.copy_remote,
            draftOriginAssetId="missing-component",
            draftOriginRevision="r4",
        )
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_choose_visibility", lambda: F8ComponentVisibility.private)
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_catalog_actions_mixin.new_asset_id",
        lambda: "replacement-component",
    )

    question_calls: list[str] = []
    create_calls: list[F8ComponentEntry] = []

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: question_calls.append("asked") or QtWidgets.QMessageBox.Yes,
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "get_component",
        lambda _component_id: (_ for _ in ()).throw(
            F8ComponentRemoteRequestError("Component not found.", status_code=404)
        ),
    )

    def _create_component(entry: F8ComponentEntry) -> F8ComponentEntry:
        create_calls.append(entry)
        uploaded_entry = copy_model(
            entry,
            update={
                "source": F8ComponentSourceKind.remote_private,
                "visibility": F8ComponentVisibility.private,
                "ownerUserId": "u1",
                "ownerDisplayName": "User One",
                "remoteRevision": "r8",
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(dialog._sync_client, "create_component", _create_component)

    published = dialog._publish_component_draft(local_entry)

    assert published is not None
    assert question_calls == ["asked"]
    assert len(create_calls) == 1
    assert create_calls[0].record.componentId == "replacement-component"
    saved_local = service.entry("draft-linked-missing", include_uninstalled=True)
    assert saved_local is not None
    assert saved_local.isLocalDraft is True
    assert saved_local.draftOriginAssetId == "replacement-component"
    assert saved_local.draftOriginRevision == "r8"

    dialog.close()


def test_component_catalog_copy_to_draft_creates_disconnected_local_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-copy-draft.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="remote-component",
            name="Remote Component",
            schemaVersion="f8studio-session/1",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            createdAt="2026-04-02T00:00:00+00:00",
            updatedAt="2026-04-02T00:00:00+00:00",
        ),
        source=F8ComponentSourceKind.remote_public,
        visibility=F8ComponentVisibility.public,
        ownerUserId="u2",
        ownerDisplayName="Remote User",
        remoteRevision="r9",
        installed=False,
        hasCachedContent=False,
    )
    service.replace_remote_entries([remote_entry])

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_selected_entry", lambda: remote_entry)
    monkeypatch.setattr(dialog, "_ensure_component_hydrated", lambda entry, operation_name: entry)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.show_info", lambda *_args, **_kwargs: None)

    dialog._on_copy_local_clicked()

    local_entries = [entry for entry in service.load_all_entries() if entry.source == F8ComponentSourceKind.local]
    assert len(local_entries) == 1
    draft_entry = local_entries[0]
    assert draft_entry.record.componentId != "remote-component"
    assert draft_entry.isLocalDraft is True
    assert draft_entry.draftOriginAssetId is None
    assert draft_entry.draftOriginRevision is None

    dialog.close()


def test_component_sync_client_delete_component_removes_remote_cache(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "component-delete.ini"), QtCore.QSettings.IniFormat)
        service = ComponentCatalogService(db_path=tmp_path / "assets.db")
        client = ComponentSyncClient(settings=settings, catalog_service=service)
        base_url = f"http://127.0.0.1:{server.server_port}"
        _ = client.login(base_url=base_url, email="u@example.com", password="p", remember=True)
        _ = client.refresh_scope_page(scope="mine", query="", cursor="", append=False)

        assert service.entry("private-1", include_uninstalled=True) is not None

        client.delete_component("private-1")

        assert server.deleted_component_ids == ["private-1"]
        assert service.entry("private-1", include_uninstalled=True) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_component_catalog_delete_removes_local_and_owned_remote(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-delete-dialog.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="owned-delete",
            name="Owned Delete",
            schemaVersion="f8studio-session/1",
            content={},
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        remoteRevision="r7",
        installed=True,
        hasCachedContent=False,
    )
    service.replace_remote_entries([remote_entry])

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        copy_model(remote_entry.record, update={"componentId": "draft-owned-delete", "content": {"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}}}),
        origin_kind=F8ComponentDraftOriginKind.copy_remote,
        publish_target_asset_id="owned-delete",
        publish_base_remote_revision="r7",
        draft_id="draft-owned-delete",
    )
    reload_calls: list[str] = []
    remote_delete_calls: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes,
    )
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: reload_calls.append("reload"))
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    monkeypatch.setattr(dialog, "_selected_action_entries", lambda: (remote_entry, None, remote_entry))
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8ComponentRemoteUser(userId="u1", name="User One", displayName="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "delete_component",
        lambda component_id: remote_delete_calls.append(str(component_id)),
    )

    dialog._on_delete_clicked()

    assert remote_delete_calls == ["owned-delete"]
    assert reload_calls
    assert dialog._draft_service_for_catalog().draft("draft-owned-delete") is not None
    dialog.close()


def test_component_catalog_disables_load_offload_for_local_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-draft-actions.ini"), QtCore.QSettings.IniFormat)
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    monkeypatch.setattr(ComponentCatalogDialog, "_refresh_remote_catalog_if_needed", lambda self: None)
    draft_entry = service.upsert_local_entry(
        F8ComponentEntry(
            record=F8ComponentRecord(
                componentId="draft-component-disable",
                name="Draft Component Disable",
                schemaVersion="f8studio-session/1",
                content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            ),
            source=F8ComponentSourceKind.local,
            isLocalDraft=True,
        )
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    dialog._entries = [draft_entry]
    monkeypatch.setattr(dialog, "_selected_entry", lambda: draft_entry)
    monkeypatch.setattr(dialog, "_selected_action_entries", lambda: (draft_entry, draft_entry, None))
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "draft-component-disable")
    dialog._list.addItem(item)
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    dialog._on_selection_changed()
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_install.isEnabled() is False
    assert dialog._btn_install.toolTip() == "Not available for Local Draft"

    delete_calls: list[str] = []
    monkeypatch.setattr(service, "delete_local_entry", lambda component_id: delete_calls.append(str(component_id)) or True)
    monkeypatch.setattr(dialog, "_selected_action_entries", lambda: (draft_entry, draft_entry, None))

    dialog._on_install_clicked()

    assert delete_calls == []
    dialog.close()


def test_graph_component_actions_overwrite_choices_only_include_drafts(monkeypatch) -> None:
    _ensure_app()
    captured_choice_ids: list[str] = []
    draft_entry = F8ComponentEntry(
        record=F8ComponentRecord(componentId="draft-component", name="Draft Component"),
        source=F8ComponentSourceKind.local,
        isLocalDraft=True,
    )
    host = _GraphComponentSaveHost()

    class _FakeOverwriteDialog:
        def __init__(
            self,
            *,
            parent: QtWidgets.QWidget | None,
            title: str,
            name: str,
            description: str,
            tags: list[str],
            overwrite_choices: list[object],
            overwrite_label: str,
            name_validator: object,
        ) -> None:
            del parent, title, name, description, tags, overwrite_label, name_validator
            captured_choice_ids.extend([str(choice.asset_id) for choice in overwrite_choices])

        def exec(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(
        GraphComponentActionsMixin,
        "_draft_component_entries",
        classmethod(lambda cls: [draft_entry]),
    )
    monkeypatch.setattr("f8pystudio.nodegraph.graph_component_actions.AssetOverwriteMetaDialog", _FakeOverwriteDialog)

    host._save_selected_nodes_as_component()

    assert captured_choice_ids == ["draft-component"]


def test_component_catalog_action_buttons_start_hidden_inside_toolbar(monkeypatch) -> None:
    _ = _ensure_app()
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    action_buttons = (
        dialog._btn_install,
        dialog._btn_upload,
        dialog._btn_subscribe,
        dialog._btn_copy_local,
        dialog._btn_delete,
        dialog._btn_edit,
        dialog._btn_visibility,
        dialog._btn_history,
        dialog._btn_create,
    )

    for button in action_buttons:
        assert button.isHidden() is True
        assert dialog._toolbar.isAncestorOf(button) is True

    dialog.close()


def test_component_catalog_row_without_description_stays_compact(monkeypatch) -> None:
    _ = _ensure_app()
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="compact-row",
            name="Compact Row",
            description="This description should not appear in the row.",
            schemaVersion="f8studio-session/1",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
        ),
        source=F8ComponentSourceKind.remote_public,
        visibility=F8ComponentVisibility.public,
        installed=True,
        hasCachedContent=True,
    )

    row_widget = dialog._build_list_row(entry)
    row_labels = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert row_widget.sizeHint().height() <= 56
    assert "This description should not appear in the row." not in row_labels

    dialog.close()
