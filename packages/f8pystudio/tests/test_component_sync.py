from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from qtpy import QtCore
from sqlalchemy import insert, select

from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
)
from f8pystudio.assets.components.component_sync import ComponentSyncClient
from f8pystudio.assets.db import component_remote_cache_table


def _component_record(component_id: str, name: str) -> dict[str, object]:
    return {
        "componentId": component_id,
        "name": name,
        "description": "",
        "usageNotes": "",
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
                "usageNotes": "",
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

        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", username="u", password="p", remember=True)
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
                usageNotes=historical_entry.record.usageNotes,
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


def test_component_remote_cache_load_cleans_empty_component_ids(tmp_path: Path) -> None:
    service = ComponentCatalogService(db_path=tmp_path / "assets.db")
    provider = service._remote_provider
    with provider._db.begin_sqla() as conn:
        _ = conn.execute(
            insert(component_remote_cache_table).values(
                component_id="",
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
        rows = conn.execute(select(component_remote_cache_table.c.component_id)).all()
    assert rows == []
