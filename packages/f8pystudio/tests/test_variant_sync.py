from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from qtpy import QtCore

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

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        auth = str(self.headers.get("Authorization") or "")
        if auth == "Bearer access-2":
            return True
        self._write_json(401, {"message": "expired"})
        return False

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/auth/login":
            self.server.last_login_user_agent = str(self.headers.get("User-Agent") or "")
            self._write_json(
                200,
                {
                    "accessToken": "access-1",
                    "refreshToken": "refresh-1",
                    "user": {"userId": "u1", "username": "u", "displayName": "User One"},
                },
            )
            return
        if self.path == "/v1/auth/refresh":
            payload = self._read_json()
            if payload.get("refreshToken") != "refresh-1":
                self._write_json(401, {"message": "bad refresh"})
                return
            self._write_json(
                200,
                {
                    "accessToken": "access-2",
                    "refreshToken": "refresh-1",
                    "user": {"userId": "u1", "username": "u", "displayName": "User One"},
                },
            )
            return
        if self.path == "/v1/auth/logout":
            if not self._check_auth():
                return
            self._write_json(200, {})
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
            auth = str(self.headers.get("Authorization") or "")
            if "owner=subscribed" in self.path:
                if not self._check_auth():
                    return
                self._write_json(200, {"entries": [self.server.subscribed_asset], "nextCursor": None})
                return
            if ("owner=public" not in self.path or bool(auth)) and not self._check_auth():
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


def test_variant_sync_client_refreshes_auth_and_marks_conflicts(tmp_path: Path) -> None:
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
        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", username="u", password="p", remember=True)

        assert auth.user.displayName == "User One"
        assert server.last_login_user_agent == "F8Studio/1.0"
        assert len(client.saved_sessions()) == 1
        assert client.current_session() is not None
        page = client.list_variants(scope="community", base_node_type="svc.a.op")
        assert page.entries[0].record.variantId == "public-1"
        assert client.current_access_token() == "access-2"

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
