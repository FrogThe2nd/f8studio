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

from f8pystudio.assets.variants.variant_catalog import LocalVariantProvider, RemoteCacheProvider, VariantCatalogService
from f8pystudio.assets.variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantKind,
    F8VariantRemoteAuth,
    F8VariantRemoteAuthError,
    F8VariantRemoteRequestError,
    F8VariantRemoteConflictError,
    F8VariantRemoteListPage,
    F8VariantRemoteVersionEntry,
    F8VariantRemoteVersionList,
    F8VariantRemoteUser,
    F8VariantSourceKind,
    F8VariantVisibility,
    variant_now_iso,
)
from f8pystudio.assets.variants.variant_sync import VariantSyncClient
from f8pystudio.assets.db import variant_remote_cache_table
from f8pystudio.assets.ui.variant_catalog_dialog import VariantCatalogDialog, variant_row_state_for_entries
from f8pystudio.nodegraph.graph_variant_actions import GraphVariantActionsMixin
from f8pysdk.specs import F8ServiceSpec, F8VariantRecord


def _make_entry(*, variant_id: str, source: F8VariantSourceKind, installed: bool = True, remote_version_number: int | None = None) -> F8VariantEntry:
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
        remoteVersionNumber=remote_version_number,
        installed=installed,
    )


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class _MemoryCredentialStore:
    def __init__(self) -> None:
        self._refresh_tokens_by_account_id: dict[str, str] = {}

    def load_refresh_token(self, *, account_id: str) -> str:
        return str(self._refresh_tokens_by_account_id.get(str(account_id), ""))

    def store_refresh_token(self, *, account_id: str, refresh_token: str) -> None:
        self._refresh_tokens_by_account_id[str(account_id)] = str(refresh_token)

    def delete_refresh_token(self, *, account_id: str) -> None:
        self._refresh_tokens_by_account_id.pop(str(account_id), None)

    def load_session_cookie(self, *, account_id: str) -> str:
        return self.load_refresh_token(account_id=account_id)

    def store_session_cookie(self, *, account_id: str, session_cookie: str) -> None:
        self.store_refresh_token(account_id=account_id, refresh_token=session_cookie)

    def delete_session_cookie(self, *, account_id: str) -> None:
        self.delete_refresh_token(account_id=account_id)


class _GraphVariantSaveHost(GraphVariantActionsMixin):
    def __init__(self) -> None:
        self._parent = QtWidgets.QWidget()

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return self._parent

    def context_nodes_menu(self) -> None:
        return None

    def selected_nodes(self) -> list[object]:
        return []

    def create_node(self, node_type: str, *, pos: tuple[float, float] | None = None, selected: bool = True, push_undo: bool = True) -> None:
        _ = (node_type, pos, selected, push_undo)
        return None


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

    def _session_cookie(self) -> str:
        cookie = str(self.headers.get("Cookie") or "")
        if "session=active-2" in cookie:
            return "session=active-2"
        if "session=active-1" in cookie:
            return "session=active-1"
        return ""

    def _authorization_token(self) -> str:
        header = str(self.headers.get("Authorization") or "").strip()
        if not header.startswith("Bearer "):
            return ""
        return header[len("Bearer ") :].strip()

    def _user_key_for_browser_session(self) -> str:
        session_cookie = self._session_cookie()
        if session_cookie == "session=active-2":
            return "u2"
        if session_cookie == "session=active-1":
            return "u1"
        return ""

    def _user_key_for_access_token(self) -> str:
        access_token = self._authorization_token()
        if access_token in {"access-u2", "access-u2-rotated"}:
            return "u2"
        if access_token in {"access-u1", "access-u1-rotated"}:
            return "u1"
        return ""

    def _check_auth(self) -> bool:
        if self._user_key_for_access_token():
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
        if self.path == "/v1/auth/desktop/session":
            self.server.last_desktop_session_origin = str(self.headers.get("Origin") or "")
            user_key = self._user_key_for_browser_session()
            if not user_key:
                self._write_json(401, {"message": "expired"})
                return
            self._write_json(200, self.server.desktop_auth_payload(user_key))
            return
        if self.path == "/v1/auth/desktop/token":
            payload = self._read_json()
            self.server.last_desktop_token_payload = payload
            self._write_json(200, self.server.desktop_auth_payload("u1"))
            return
        if self.path == "/v1/auth/desktop/refresh":
            payload = self._read_json()
            self.server.last_desktop_refresh_payload = payload
            refresh_token = str(payload.get("refreshToken") or "")
            if refresh_token in self.server.revoked_refresh_tokens:
                self._write_json(401, {"message": "expired"})
                return
            user_key = self.server.user_key_for_refresh_token(refresh_token)
            if not user_key:
                self._write_json(401, {"message": "expired"})
                return
            self._write_json(200, self.server.desktop_auth_payload(user_key))
            return
        if self.path == "/v1/auth/desktop/revoke":
            payload = self._read_json()
            self.server.last_desktop_revoke_payload = payload
            refresh_token = str(payload.get("refreshToken") or "")
            if refresh_token:
                self.server.revoked_refresh_tokens.add(refresh_token)
            self._write_json(200, {"ok": True})
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
            if self._user_key_for_access_token() == "u2":
                self._write_json(200, {"userId": "u2", "name": "User Two", "email": "u2@example.com"})
                return
            self._write_json(200, {"userId": "u1", "name": "User One", "email": "u@example.com"})
            return
        if self.path.startswith("/v1/variants?"):
            if "owner=subscribed" in self.path:
                if not self._check_auth():
                    return
                self._write_json(200, {"entries": [self.server.subscribed_asset], "nextCursor": None})
                return
            if ("owner=public" not in self.path or bool(self._authorization_token())) and not self._check_auth():
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
                    "versionNumber": 1,
                    "record": self.server.public_record,
                },
            )
            return
        self._write_json(404, {"message": "missing"})

    def do_PUT(self) -> None:  # noqa: N802
        if self.path == "/v1/variants/conflict-1":
            if not self._check_auth():
                return
            self._write_json(409, {"message": "conflict", "variantId": "conflict-1", "versionNumber": 2})
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
        self.last_desktop_session_origin = ""
        self.last_signout_origin = ""
        self.last_desktop_token_payload: dict[str, object] = {}
        self.last_desktop_refresh_payload: dict[str, object] = {}
        self.last_desktop_revoke_payload: dict[str, object] = {}
        self.revoked_refresh_tokens: set[str] = set()
        self.refresh_access_tokens_by_user_key: dict[str, str] = {}
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
    def _user_payload(user_key: str) -> dict[str, str]:
        if user_key == "u2":
            return {"userId": "u2", "name": "User Two", "email": "u2@example.com"}
        return {"userId": "u1", "name": "User One", "email": "u@example.com"}

    @staticmethod
    def user_key_for_refresh_token(refresh_token: str) -> str:
        if refresh_token == "refresh-u2":
            return "u2"
        if refresh_token == "refresh-u1":
            return "u1"
        return ""

    def desktop_auth_payload(self, user_key: str) -> dict[str, object]:
        access_token = self.refresh_access_tokens_by_user_key.get(user_key, f"access-{user_key}")
        return {
            "accessToken": access_token,
            "accessTokenExpiresAt": "2026-05-01T00:05:00Z",
            "refreshToken": f"refresh-{user_key}",
            "refreshTokenExpiresAt": "2026-06-01T00:00:00Z",
            "user": self._user_payload(user_key),
        }

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
            "versionNumber": 1 if visibility == "public" else 1,
            "createdAt": str(record["createdAt"]),
            "updatedAt": str(record["updatedAt"]),
            "editable": visibility != "public",
            "subscribed": subscribed,
            "record": record,
        }


class _FakeVersionBrowserDialog:
    seen_titles: list[str] = []
    seen_item_counts: list[int] = []

    def __init__(self, *args: object, title: str, items: list[object], load_payload: object, **kwargs: object) -> None:
        del args, load_payload, kwargs
        type(self).seen_titles.append(str(title))
        type(self).seen_item_counts.append(len(items))

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted


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
        auth = client.login(base_url=f"http://127.0.0.1:{server.server_port}", email="u@example.com", password="p", remember=True)

        assert auth.user.name == "User One"
        assert auth.accessToken == "access-u1"
        assert auth.refreshToken == "refresh-u1"
        assert server.last_login_user_agent == "F8Studio/1.0"
        assert server.last_desktop_session_origin == f"http://127.0.0.1:{server.server_port}"
        assert len(client.saved_sessions()) == 1
        assert client.current_session() is not None
        settings.beginGroup("assetcloud/v1")
        saved_sessions_raw = settings.value("saved_sessions", [])
        stored_session_cookie = settings.value("session_cookie", "")
        settings.endGroup()
        assert str(stored_session_cookie or "") == ""
        assert isinstance(saved_sessions_raw, list)
        assert len(saved_sessions_raw) == 1
        assert isinstance(saved_sessions_raw[0], dict)
        assert "sessionCookie" not in saved_sessions_raw[0]
        assert "accessToken" not in saved_sessions_raw[0]
        assert "refreshToken" not in saved_sessions_raw[0]
        page = client.list_variants(scope="community", base_node_type="svc.a.op")
        assert page.entries[0].record.variantId == "public-1"
        assert client.current_access_token() == "access-u1"

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
        assert uploaded.remoteVersionNumber == 1

        conflict_entry = _make_entry(
            variant_id="conflict-1",
            source=F8VariantSourceKind.remote_private,
            remote_version_number=1,
        )
        service.replace_remote_entries([conflict_entry])
        try:
            client.upload_entry(conflict_entry)
        except F8VariantRemoteConflictError as exc:
            assert exc.remote_version_number == 2
        else:
            raise AssertionError("expected conflict error")
        marked = service.entry("conflict-1", include_uninstalled=True)
        assert marked is not None
        assert marked.remoteVersionNumber == 2
        client.logout()
        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.saved_sessions() == []
        assert server.last_desktop_revoke_payload == {"refreshToken": "refresh-u1"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_can_exchange_browser_authorization_code(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-browser-auth.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service)
        base_url = f"http://127.0.0.1:{server.server_port}"

        auth = client.exchange_browser_auth_code(
            base_url=base_url,
            client_id="pystudio",
            code="desktop-code-1",
            redirect_uri="http://127.0.0.1:41234/callback",
            code_verifier="desktop-verifier-1",
            remember=True,
        )

        assert auth.user.name == "User One"
        assert auth.accessToken == "access-u1"
        assert auth.refreshToken == "refresh-u1"
        assert client.current_access_token() == "access-u1"
        assert server.last_desktop_token_payload == {
            "clientId": "pystudio",
            "code": "desktop-code-1",
            "redirectUri": "http://127.0.0.1:41234/callback",
            "codeVerifier": "desktop-verifier-1",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_refresh_auth_preserves_switched_account_cookie(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-switch.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        credential_store = _MemoryCredentialStore()
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
        base_url = f"http://127.0.0.1:{server.server_port}"
        account_id_1 = f"{base_url}::u@example.com"
        account_id_2 = f"{base_url}::u2@example.com"
        credential_store.store_refresh_token(account_id=account_id_1, refresh_token="refresh-u1")
        credential_store.store_refresh_token(account_id=account_id_2, refresh_token="refresh-u2")
        settings.beginGroup("assetcloud/v1")
        settings.setValue(
            "saved_sessions",
            [
                {
                    "accountId": account_id_1,
                    "baseUrl": base_url,
                    "user": {"userId": "u1", "name": "User One", "email": "u@example.com"},
                    "lastUsedAt": "2026-04-21T10:00:00+00:00",
                },
                {
                    "accountId": account_id_2,
                    "baseUrl": base_url,
                    "user": {"userId": "u2", "name": "User Two", "email": "u2@example.com"},
                    "lastUsedAt": "2026-04-21T10:05:00+00:00",
                },
            ],
        )
        settings.setValue("current_account_id", account_id_1)
        settings.setValue("user", {"userId": "u1", "name": "User One", "email": "u@example.com"})
        settings.endGroup()

        assert client.current_access_token() == ""

        settings.beginGroup("assetcloud/v1")
        settings.setValue("current_account_id", account_id_2)
        settings.setValue("user", {"userId": "u2", "name": "User Two", "email": "u2@example.com"})
        settings.endGroup()

        assert client.current_access_token() == ""

        auth = client.refresh_auth()
        assert auth.accessToken == "access-u2"
        assert auth.refreshToken == "refresh-u2"
        assert auth.user.email == "u2@example.com"
        assert client.current_access_token() == "access-u2"
        assert credential_store.load_refresh_token(account_id=account_id_2) == "refresh-u2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_persists_rotated_session_cookie_from_refresh_auth(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-rotated-cookie.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        credential_store = _MemoryCredentialStore()
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
        base_url = f"http://127.0.0.1:{server.server_port}"
        account_id = f"{base_url}::u@example.com"
        credential_store.store_refresh_token(account_id=account_id, refresh_token="refresh-u1")
        settings.beginGroup("assetcloud/v1")
        settings.setValue(
            "saved_sessions",
            [
                {
                    "accountId": account_id,
                    "baseUrl": base_url,
                    "user": {"userId": "u1", "name": "User One", "email": "u@example.com"},
                    "lastUsedAt": "2026-04-21T10:00:00+00:00",
                }
            ],
        )
        settings.setValue("current_account_id", account_id)
        settings.setValue("user", {"userId": "u1", "name": "User One", "email": "u@example.com"})
        settings.endGroup()

        server.refresh_access_tokens_by_user_key["u1"] = "access-u1-rotated"
        auth = client.refresh_auth()

        assert auth.accessToken == "access-u1-rotated"
        assert client.current_access_token() == "access-u1-rotated"
        assert credential_store.load_refresh_token(account_id=account_id) == "refresh-u1"

        restarted_client = VariantSyncClient(
            settings=settings,
            catalog_service=service,
            credential_store=credential_store,
        )

        assert restarted_client.current_access_token() == ""
        refreshed_auth = restarted_client.refresh_auth()
        assert refreshed_auth.accessToken == "access-u1-rotated"
        assert refreshed_auth.user.email == "u@example.com"
        assert restarted_client.current_access_token() == "access-u1-rotated"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_switch_account_refreshes_tokens(tmp_path: Path) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-switch-local.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        credential_store = _MemoryCredentialStore()
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
        base_url = f"http://127.0.0.1:{server.server_port}"
        account_id_1 = f"{base_url}::u1@example.com"
        account_id_2 = f"{base_url}::u2@example.com"
        credential_store.store_refresh_token(account_id=account_id_1, refresh_token="refresh-u1")
        credential_store.store_refresh_token(account_id=account_id_2, refresh_token="refresh-u2")
        settings.beginGroup("assetcloud/v1")
        settings.setValue(
            "saved_sessions",
            [
                {
                    "accountId": account_id_1,
                    "baseUrl": base_url,
                    "user": {"userId": "u1", "name": "User One", "email": "u1@example.com"},
                    "lastUsedAt": "2026-04-21T10:00:00+00:00",
                },
                {
                    "accountId": account_id_2,
                    "baseUrl": base_url,
                    "user": {"userId": "u2", "name": "User Two", "email": "u2@example.com"},
                    "lastUsedAt": "2026-04-21T10:05:00+00:00",
                },
            ],
        )
        settings.setValue("current_account_id", account_id_1)
        settings.setValue("user", {"userId": "u1", "name": "User One", "email": "u1@example.com"})
        settings.endGroup()

        auth = client.switch_account(account_id_2)

        assert auth.accessToken == "access-u2"
        assert auth.refreshToken == "refresh-u2"
        assert auth.user.email == "u2@example.com"
        assert client.current_account_id() == account_id_2
        assert client.current_user() is not None
        assert client.current_user().email == "u2@example.com"
        assert client.current_access_token() == "access-u2"
        assert client.base_url() == base_url
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
        credential_store = _MemoryCredentialStore()
        client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
        base_url = f"http://127.0.0.1:{server.server_port}"
        client._set_auth(
            F8VariantRemoteAuth(
                accessToken="access-u1",
                accessTokenExpiresAt="2026-04-21T12:00:00+00:00",
                refreshToken="refresh-u1",
                refreshTokenExpiresAt="2026-04-22T12:00:00+00:00",
                user=F8VariantRemoteUser(userId="u1", name="User One", email="u@example.com"),
            ),
            base_url=base_url,
            remember=True,
        )

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


def test_variant_catalog_service_skips_noop_remote_replace_and_supports_silent_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    entry = _make_entry(
        variant_id="public-1",
        source=F8VariantSourceKind.remote_public,
        installed=False,
        remote_version_number=1,
    )
    change_events: list[str] = []
    monkeypatch.setattr(
        "f8pystudio.assets.variants.variant_catalog.emit_variants_changed",
        lambda: change_events.append("changed"),
    )

    service.replace_remote_entries([entry])
    service.replace_remote_entries([entry])

    assert change_events == ["changed"]

    cached_entry = copy_model(
        entry,
        update={
            "hasCachedContent": True,
            "downloadedAt": "2026-04-21T00:01:00+00:00",
        },
    )
    service.cache_remote_entry(cached_entry, emit_changed=False)

    stored_entry = service.entry("public-1", include_uninstalled=True)
    assert stored_entry is not None
    assert stored_entry.hasCachedContent is True
    assert stored_entry.installed is False
    assert change_events == ["changed"]


def test_variant_sync_client_uses_env_base_url_when_settings_are_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787/")
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-env.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    assert client.base_url() == "http://127.0.0.1:8787"


def test_variant_sync_client_drops_saved_sessions_missing_keyring_refresh_token(tmp_path: Path, caplog) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-missing-keyring.ini"), QtCore.QSettings.IniFormat)
    settings.beginGroup("assetcloud/v1")
    settings.setValue(
        "saved_sessions",
        [
            {
                "accountId": "acct-missing-cookie",
                "baseUrl": "https://assetcloud.feel8.fun",
                "user": {
                    "userId": "u1",
                    "name": "User One",
                    "email": "u@example.com",
                },
                "lastUsedAt": "2026-04-20T00:00:00+00:00",
            }
        ],
    )
    settings.setValue("current_account_id", "acct-missing-cookie")
    settings.setValue(
        "user",
        {
            "userId": "u1",
            "name": "User One",
            "email": "u@example.com",
        },
    )
    settings.endGroup()
    settings.sync()

    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    with caplog.at_level(logging.WARNING):
        assert client.saved_sessions() == []

    assert client.current_session() is None
    assert client.current_user() is None
    assert "Dropping saved variant session with missing keyring refresh token" in caplog.text


def test_variant_sync_client_clears_invalid_saved_session_after_refresh_auth_failure(tmp_path: Path, caplog) -> None:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = QtCore.QSettings(str(tmp_path / "variant-sync-expired.ini"), QtCore.QSettings.IniFormat)
        db_path = tmp_path / "assets.db"
        credential_store = _MemoryCredentialStore()
        service = VariantCatalogService(
            local_provider=LocalVariantProvider(db_path=db_path),
            remote_provider=RemoteCacheProvider(db_path=db_path),
        )
        client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
        base_url = f"http://127.0.0.1:{server.server_port}"
        account_id = f"{base_url}::u@example.com"
        credential_store.store_refresh_token(account_id=account_id, refresh_token="refresh-expired")
        settings.beginGroup("assetcloud/v1")
        settings.setValue(
            "saved_sessions",
            [
                {
                    "accountId": account_id,
                    "baseUrl": base_url,
                    "user": {"userId": "u1", "name": "User One", "email": "u@example.com"},
                    "lastUsedAt": "2026-04-21T10:00:00+00:00",
                }
            ],
        )
        settings.setValue("current_account_id", account_id)
        settings.setValue("user", {"userId": "u1", "name": "User One", "email": "u@example.com"})
        settings.endGroup()

        with caplog.at_level(logging.WARNING):
            with pytest.raises(F8VariantRemoteAuthError, match="was cleared"):
                client.refresh_auth()

        assert client.current_access_token() == ""
        assert client.current_session() is None
        assert client.current_user() is None
        assert client.saved_sessions() == []
        assert credential_store.load_refresh_token(account_id=account_id) == ""
        assert "Variant saved session became unauthorized and was cleared" in caplog.text
        assert account_id in caplog.text
        assert "u@example.com" in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_sync_client_env_base_url_overrides_saved_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787")
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-env-override.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)
    client.set_base_url("https://preview-assetcloud.feel8.fun/")

    assert client.base_url() == "http://127.0.0.1:8787"


def test_variant_sync_client_hides_current_session_when_env_base_url_differs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F8_ASSET_CLOUD_BASE_URL", "http://127.0.0.1:8787")
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-env-filter.ini"), QtCore.QSettings.IniFormat)
    credential_store = _MemoryCredentialStore()
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service, credential_store=credential_store)
    account_id = "https://assetcloud.feel8.fun::u@example.com"
    credential_store.store_refresh_token(account_id=account_id, refresh_token="refresh-prod")
    settings.beginGroup("assetcloud/v1")
    settings.setValue(
        "saved_sessions",
        [
            {
                "accountId": account_id,
                "baseUrl": "https://assetcloud.feel8.fun",
                "user": {"userId": "u1", "name": "User One", "email": "u@example.com"},
                "lastUsedAt": "2026-04-21T10:00:00+00:00",
            }
        ],
    )
    settings.setValue("current_account_id", account_id)
    settings.setValue("user", {"userId": "u1", "name": "User One", "email": "u@example.com"})
    settings.endGroup()
    settings.sync()

    assert client.base_url() == "http://127.0.0.1:8787"
    assert client.current_session() is None
    assert client.current_user() is None
    assert client.current_access_token() == ""


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
        _ = client.login(base_url=base_url, email="u@example.com", password="p", remember=True)

        original_request_json = client._request_json

        def _raise_signout_timeout(
            method: str,
            path: str,
            payload: dict[str, object] | None,
            *,
            authorized: bool,
            retry_on_auth_failure: bool = True,
        ) -> dict[str, object]:
            if method == "POST" and path == "/v1/auth/desktop/revoke":
                raise F8VariantRemoteRequestError("POST /v1/auth/desktop/revoke timed out after 10s")
            return original_request_json(
                method,
                path,
                payload,
                authorized=authorized,
                retry_on_auth_failure=retry_on_auth_failure,
            )

        monkeypatch.setattr(client, "_request_json", _raise_signout_timeout)

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


def test_variant_sync_client_preview_load_uses_content_endpoint_only(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-preview-only.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)
    calls: list[str] = []
    preview_entry = _make_entry(
        variant_id="public-1",
        source=F8VariantSourceKind.remote_public,
        installed=False,
        remote_version_number=1,
    )

    def _request_json(
        method: str,
        path: str,
        payload: dict[str, object] | None,
        *,
        authorized: bool,
        retry_on_auth_failure: bool = True,
    ) -> dict[str, object]:
        del method, payload, authorized, retry_on_auth_failure
        calls.append(path)
        now = variant_now_iso()
        assert path == "/v1/variants/public-1/content"
        return {
            "variantId": "public-1",
            "kind": "operator",
            "baseNodeType": "svc.a.op",
            "serviceClass": "svc.test",
            "operatorClass": "op.test",
            "name": "Preview Variant",
            "description": "",
            "tags": ["preview"],
            "spec": {"label": "Preview Variant"},
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    hydrated = client.load_variant_preview_entry(preview_entry)

    assert calls == ["/v1/variants/public-1/content"]
    assert hydrated.installed is False
    assert hydrated.hasCachedContent is True
    assert hydrated.record.spec == {"label": "Preview Variant"}


def test_variant_sync_client_accepts_summary_variant_payloads_without_record(tmp_path: Path, monkeypatch) -> None:
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-summary.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    client = VariantSyncClient(settings=settings, catalog_service=service)

    def _request_json(
        method: str,
        path: str,
        payload: dict[str, object] | None,
        *,
        authorized: bool,
        retry_on_auth_failure: bool = True,
    ) -> dict[str, object]:
        del method, path, payload, authorized, retry_on_auth_failure
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
                    "versionNumber": 1,
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
    assert entry.remoteVersionNumber == 1
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


def test_variant_refresh_scope_page_preserves_cached_content_for_matching_version_number(tmp_path: Path, monkeypatch) -> None:
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
            remote_version_number=1,
        ),
        update={
            "downloadedAt": "2026-04-07T00:00:00+00:00",
            "hasCachedContent": True,
        },
    )
    service.replace_remote_entries([existing_entry])

    incoming_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_version_number=1,
        ),
        update={
            "record": copy_model(existing_entry.record, update={"spec": {}}),
            "visibility": F8VariantVisibility.public,
            "ownerUserId": "u2",
            "ownerDisplayName": "Remote User",
            "hasCachedContent": False,
            "downloadedAt": None,
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
    assert refreshed_entry.remoteVersionNumber == 1


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
            remote_version_number=1,
        ),
        update={
            "downloadedAt": "2026-04-07T00:00:00+00:00",
            "hasCachedContent": True,
        },
    )
    service.replace_remote_entries([existing_entry])

    incoming_entry = copy_model(
        _make_entry(
            variant_id="public-1",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_version_number=1,
        ),
        update={
            "record": copy_model(existing_entry.record, update={"spec": {}}),
            "visibility": F8VariantVisibility.public,
            "ownerUserId": "u2",
            "ownerDisplayName": "Remote User",
            "hasCachedContent": False,
            "downloadedAt": None,
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


def test_variant_upload_uses_latest_local_snapshot_only(tmp_path: Path, monkeypatch) -> None:
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
            remote_version_number=1,
        ),
        update={
            "visibility": F8VariantVisibility.private,
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
            "versionNumber": next_version,
            "createdAt": str(record_payload["createdAt"]),
            "updatedAt": str(record_payload["updatedAt"]),
            "installed": True,
            "hasCachedContent": True,
            "subscribed": False,
        }

    monkeypatch.setattr(client, "_request_json", _request_json)

    uploaded = client.upload_entry(
        copy_model(
            third,
            update={
                "source": F8VariantSourceKind.remote_private,
                "visibility": F8VariantVisibility.private,
                "remoteVersionNumber": 1,
                "installed": True,
                "hasCachedContent": True,
            },
        )
    )

    assert len(request_payloads) == 1
    assert request_payloads[0]["record"]["spec"]["label"] == "v3"
    assert "changeSummary" not in request_payloads[0]
    assert uploaded.remoteVersionNumber == 2


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
                created_at="2026-04-04T00:00:00+00:00",
                updated_at="2026-04-04T00:00:00+00:00",
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                remote_version_number=1,
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
    entry = _make_entry(variant_id="remote-1", source=F8VariantSourceKind.remote_public, installed=True, remote_version_number=1)

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
                created_at=str(entry.record.createdAt),
                updated_at=str(entry.record.updatedAt),
                source="remote_public",
                visibility="public",
                owner_user_id="u1",
                owner_display_name="User One",
                remote_version_number=1,
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
    assert loaded.record.spec == {"label": "remote-1"}


def test_variant_row_state_badges_cover_local_remote_and_both() -> None:
    local_entry = _make_entry(variant_id="asset-1", source=F8VariantSourceKind.local)
    remote_entry = _make_entry(variant_id="asset-1", source=F8VariantSourceKind.remote_public, installed=True, remote_version_number=1)
    remote_entry = copy_model(
        remote_entry,
        update={"visibility": F8VariantVisibility.public},
    )
    both_state = variant_row_state_for_entries(
        variant_id="asset-1",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )
    installed_remote_state = variant_row_state_for_entries(
        variant_id="asset-2",
        local_entry=None,
        remote_entry=copy_model(
            _make_entry(variant_id="asset-2", source=F8VariantSourceKind.remote_public, installed=True, remote_version_number=1),
            update={"visibility": F8VariantVisibility.public},
        ),
    )
    remote_state = variant_row_state_for_entries(
        variant_id="asset-3",
        local_entry=None,
        remote_entry=copy_model(
            _make_entry(variant_id="asset-3", source=F8VariantSourceKind.remote_public, installed=False, remote_version_number=1),
            update={"visibility": F8VariantVisibility.public},
        ),
    )
    synced_state = variant_row_state_for_entries(
        variant_id="asset-4",
        local_entry=_make_entry(variant_id="asset-4", source=F8VariantSourceKind.local),
        remote_entry=copy_model(
            _make_entry(variant_id="asset-4", source=F8VariantSourceKind.remote_public, installed=True, remote_version_number=1),
            update={"visibility": F8VariantVisibility.public},
        ),
    )
    local_changes_state = variant_row_state_for_entries(
        variant_id="asset-5",
        local_entry=_make_entry(variant_id="asset-5", source=F8VariantSourceKind.local),
        remote_entry=copy_model(
            _make_entry(variant_id="asset-5", source=F8VariantSourceKind.remote_public, installed=True, remote_version_number=1),
            update={"visibility": F8VariantVisibility.public},
        ),
    )

    assert both_state.badge_texts() == ["both", "public"]
    assert installed_remote_state.badge_texts() == ["both", "public"]
    assert remote_state.badge_texts() == ["remote", "public"]
    assert synced_state.badge_texts() == ["both", "public"]
    assert local_changes_state.badge_texts() == ["both", "public"]


def test_variant_manager_sync_button_is_disabled_for_owned_remote_without_draft(monkeypatch, tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-noop.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    local_entry = service.upsert_local_entry(_make_entry(variant_id="sync-1", source=F8VariantSourceKind.local))
    remote_entry = copy_model(
        _make_entry(
            variant_id="sync-1",
            source=F8VariantSourceKind.remote_private,
            installed=True,
            remote_version_number=1,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._refresh_action_buttons(remote_entry)

    assert local_entry.isLocalDraft is True
    assert dialog._btn_upload.isVisible() is False
    assert dialog._btn_upload.isEnabled() is False

    dialog.close()


def test_variant_publish_new_draft_keeps_linked_draft_and_restores_remote_owner(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-push-draft.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    local_entry = service.upsert_local_entry(
        copy_model(
            _make_entry(variant_id="draft-promote", source=F8VariantSourceKind.local),
            update={
                "isLocalDraft": True,
                "draftOriginKind": F8VariantDraftOriginKind.new,
            },
        )
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_choose_visibility", lambda: F8VariantVisibility.private)
    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_sync_flows.new_asset_id",
        lambda: "published-variant",
    )

    create_calls: list[tuple[F8VariantEntry, str | None]] = []

    def _create_variant(entry: F8VariantEntry, *, change_summary: str | None = None) -> F8VariantEntry:
        create_calls.append((entry, change_summary))
        uploaded_entry = copy_model(
            _make_entry(variant_id="published-variant", source=F8VariantSourceKind.remote_private),
            update={
                "record": copy_model(entry.record, update={"variantId": "published-variant"}),
                "visibility": F8VariantVisibility.private,
                "ownerUserId": "u1",
                "ownerDisplayName": "User One",
                "remoteVersionNumber": 1,
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(dialog._sync_client, "create_variant", _create_variant)
    monkeypatch.setattr(dialog, "_request_publish_version_notes", lambda **kwargs: "Initial variant note")

    uploaded = dialog._publish_variant_draft(local_entry)

    assert uploaded is not None
    assert len(create_calls) == 1
    assert create_calls[0][1] == "Initial variant note"
    saved_draft = dialog._draft_service_for_catalog().draft("draft-promote")
    saved_remote = service.remote_entry("published-variant")
    assert saved_draft is not None
    assert saved_draft.record.variantId == "draft-promote"
    assert saved_draft.originKind == F8VariantDraftOriginKind.new
    assert saved_draft.publishTargetAssetId == "published-variant"
    assert saved_draft.publishBaseRemoteVersionNumber == 1
    assert saved_remote is not None
    assert saved_remote.ownerDisplayName == "User One"

    dialog.close()


def test_variant_manager_load_owned_remote_only_updates_remote_cache(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-load.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-remote",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=1,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )

    def _install_variant(_variant_id: str) -> F8VariantEntry:
        return service.install_remote_entry(
            copy_model(
                remote_entry,
                update={
                    "installed": True,
                    "hasCachedContent": True,
                },
            )
        )

    monkeypatch.setattr(dialog._sync_client, "install_variant", _install_variant)
    monkeypatch.setattr(dialog, "_selected_remote_entry", lambda: service.remote_entry("owned-remote"))

    loaded = dialog._load_selected_remote_variant()

    assert loaded is not None
    remote_after = service.remote_entry("owned-remote")
    assert dialog._draft_service_for_catalog().draft_for_publish_target("owned-remote") is None
    assert remote_after is not None
    assert remote_after.installed is True
    assert remote_after.hasCachedContent is True

    dialog.close()


def test_variant_manager_copy_to_draft_creates_disconnected_local_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-copy-draft.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="remote-draft-source",
            source=F8VariantSourceKind.remote_public,
            installed=False,
            remote_version_number=7,
        ),
        update={
            "visibility": F8VariantVisibility.public,
            "ownerUserId": "u2",
            "ownerDisplayName": "Remote User",
        },
    )
    service.replace_remote_entries([remote_entry])

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_selected_entry", lambda: remote_entry)
    monkeypatch.setattr(dialog._sync_client, "cache_variant_content", lambda _variant_id: remote_entry)

    duplicated = dialog._duplicate_selected_variant_as_local()

    assert duplicated is not None
    assert duplicated.record.variantId != "remote-draft-source"
    assert duplicated.isLocalDraft is True
    assert duplicated.draftOriginAssetId is None
    assert duplicated.draftOriginVersionNumber is None

    dialog.close()


def test_variant_catalog_action_buttons_start_hidden_inside_toolbar(monkeypatch) -> None:
    _ = _ensure_app()
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)

    dialog = VariantCatalogDialog(parent=None, base_node_type="svc.a.op", base_node_name="Variant", node_graph=None)

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


def test_variant_row_state_uses_local_draft_owner_label() -> None:
    local_entry = copy_model(
        _make_entry(variant_id="draft-owner", source=F8VariantSourceKind.local),
        update={"isLocalDraft": True},
    )

    row_state = variant_row_state_for_entries(
        variant_id="draft-owner",
        local_entry=local_entry,
        remote_entry=None,
    )

    assert row_state.owner_display_name == "Local Draft"


def test_variant_row_state_prefers_remote_owner_when_remote_head_exists() -> None:
    local_entry = copy_model(
        _make_entry(variant_id="draft-owner-remote", source=F8VariantSourceKind.local),
        update={"isLocalDraft": True},
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="draft-owner-remote",
            source=F8VariantSourceKind.remote_private,
            installed=True,
            remote_version_number=1,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )

    row_state = variant_row_state_for_entries(
        variant_id="draft-owner-remote",
        local_entry=local_entry,
        remote_entry=remote_entry,
    )

    assert row_state.owner_display_name == "User One"


def test_variant_manager_save_over_remote_offload_seeds_remote_sync_base(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-save-overwrite.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-overwrite",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=4,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])
    modified_record = copy_model(
        remote_entry.record,
        update={
            "spec": {"label": "owned-overwrite", "changed": True},
            "updatedAt": variant_now_iso(),
        },
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)

    saved_entry = dialog._save_variant_record(record=modified_record, overwrite_entry=remote_entry)

    assert saved_entry.isLocalDraft is True
    assert saved_entry.record.variantId != "owned-overwrite"
    assert saved_entry.draftOriginAssetId == "owned-overwrite"
    assert saved_entry.draftOriginVersionNumber == 4

    update_calls: list[tuple[F8VariantEntry, str | None]] = []

    def _update_variant(entry: F8VariantEntry, *, change_summary: str | None = None) -> F8VariantEntry:
        update_calls.append((entry, change_summary))
        uploaded_entry = copy_model(
            remote_entry,
            update={
                "record": copy_model(entry.record, update={"variantId": "owned-overwrite"}),
                "remoteVersionNumber": 5,
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(
        dialog._sync_client,
        "cache_variant_content",
        lambda _variant_id: copy_model(remote_entry, update={"hasCachedContent": True}),
    )
    monkeypatch.setattr(dialog._sync_client, "update_variant", _update_variant)
    monkeypatch.setattr(dialog, "_request_publish_version_notes", lambda **kwargs: "Updated variant note")

    published = dialog._publish_variant_draft(saved_entry)

    assert published is not None
    assert len(update_calls) == 1
    assert update_calls[0][0].record.variantId == "owned-overwrite"
    assert update_calls[0][1] == "Updated variant note"
    updated_draft = dialog._draft_service_for_catalog().draft(str(saved_entry.record.variantId))
    assert updated_draft is not None
    assert updated_draft.publishTargetAssetId == "owned-overwrite"
    assert updated_draft.publishBaseRemoteVersionNumber == 5

    dialog.close()


def test_variant_publish_missing_linked_asset_can_create_replacement(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-missing-linked.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    local_entry = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_entry(variant_id="draft-linked-missing", source=F8VariantSourceKind.local).record,
            source=F8VariantSourceKind.local,
            isLocalDraft=True,
            draftOriginKind=F8VariantDraftOriginKind.copy_remote,
            draftOriginAssetId="missing-variant",
            draftOriginVersionNumber=4,
        )
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dialog, "_choose_visibility", lambda: F8VariantVisibility.private)
    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_sync_flows.new_asset_id",
        lambda: "replacement-variant",
    )

    question_calls: list[str] = []
    create_calls: list[tuple[F8VariantEntry, str | None]] = []

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: question_calls.append("asked") or QtWidgets.QMessageBox.Yes,
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "get_variant",
        lambda _variant_id: (_ for _ in ()).throw(
            F8VariantRemoteRequestError("Variant not found.", status_code=404)
        ),
    )

    def _create_variant(entry: F8VariantEntry, *, change_summary: str | None = None) -> F8VariantEntry:
        create_calls.append((entry, change_summary))
        uploaded_entry = copy_model(
            _make_entry(variant_id="replacement-variant", source=F8VariantSourceKind.remote_private),
            update={
                "record": copy_model(entry.record, update={"variantId": "replacement-variant"}),
                "visibility": F8VariantVisibility.private,
                "ownerUserId": "u1",
                "ownerDisplayName": "User One",
                "remoteVersionNumber": 8,
                "installed": True,
                "hasCachedContent": True,
            },
        )
        return service.install_remote_entry(uploaded_entry)

    monkeypatch.setattr(dialog._sync_client, "create_variant", _create_variant)
    monkeypatch.setattr(dialog, "_request_publish_version_notes", lambda **kwargs: "Replacement variant note")

    published = dialog._publish_variant_draft(local_entry)

    assert published is not None
    assert question_calls == ["asked"]
    assert len(create_calls) == 1
    assert create_calls[0][0].record.variantId == "replacement-variant"
    assert create_calls[0][1] == "Replacement variant note"
    saved_local = service.entry("draft-linked-missing", include_uninstalled=True)
    assert saved_local is not None
    assert saved_local.isLocalDraft is True
    assert saved_local.draftOriginAssetId == "replacement-variant"
    assert saved_local.draftOriginVersionNumber == 8

    dialog.close()


def test_variant_publish_no_diff_ignores_timestamp_only_changes(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-sync-no-diff.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_record = copy_model(
        _make_entry(
            variant_id="owned-no-diff",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=4,
        ).record,
        update={
            "createdAt": "2026-04-21T00:00:00+00:00",
            "updatedAt": "2026-04-21T00:00:00+00:00",
        },
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-no-diff",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=4,
        ),
        update={
            "record": remote_record,
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])
    local_record = copy_model(
        remote_record,
        update={
            "variantId": "linked-no-diff",
            "createdAt": "2026-04-22T00:00:00+00:00",
            "updatedAt": "2026-04-22T00:00:00+00:00",
        },
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)

    saved_entry = dialog._save_variant_record(record=local_record, overwrite_entry=remote_entry)
    patch_calls: list[tuple[str, str, str, list[str]]] = []
    update_calls: list[tuple[F8VariantEntry, str | None]] = []
    info_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_sync_flows.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "cache_variant_content",
        lambda _variant_id: copy_model(remote_entry, update={"hasCachedContent": True}),
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "patch_variant_meta",
        lambda variant_id, *, name, description, tags: patch_calls.append(
            (str(variant_id), str(name), str(description), list(tags))
        ) or remote_entry,
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "update_variant",
        lambda entry, *, change_summary=None: update_calls.append((entry, change_summary)) or entry,
    )

    published = dialog._publish_variant_draft(saved_entry)

    assert published is None
    assert patch_calls == []
    assert update_calls == []
    assert info_messages == [("No changes", f"No changes to publish for:\n{saved_entry.record.name}")]

    dialog.close()


def test_variant_manager_offload_keeps_draft_and_clears_remote_cache(tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-offload.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-offload",
            source=F8VariantSourceKind.remote_private,
            installed=True,
            remote_version_number=2,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
            "hasCachedContent": True,
        },
    )
    service.replace_remote_entries([remote_entry])
    local_entry = service.upsert_local_entry(
        copy_model(
            remote_entry,
            update={
                "source": F8VariantSourceKind.local,
                "isLocalDraft": True,
                "draftOriginKind": F8VariantDraftOriginKind.copy_remote,
                "draftOriginAssetId": "owned-offload",
                "draftOriginVersionNumber": 2,
            },
        )
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    changed = dialog._offload_selected_variant(local_entry=local_entry, remote_entry=remote_entry)

    assert changed is True
    saved_draft = dialog._draft_service_for_catalog().draft(str(local_entry.record.variantId))
    assert saved_draft is not None
    remote_after = service.remote_entry("owned-offload")
    assert remote_after is not None
    assert remote_after.installed is False
    assert remote_after.hasCachedContent is False

    dialog.close()


def test_variant_manager_pull_replace_does_not_mutate_local_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-pull-replace.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    local_entry = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_entry(variant_id="replace-me", source=F8VariantSourceKind.local).record,
            source=F8VariantSourceKind.local,
        )
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="replace-me",
            source=F8VariantSourceKind.remote_private,
            installed=True,
            remote_version_number=4,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
            "hasCachedContent": True,
        },
    )
    service.replace_remote_entries([remote_entry])

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_selected_local_entry", lambda: local_entry)
    monkeypatch.setattr(dialog, "_selected_remote_entry", lambda: remote_entry)
    monkeypatch.setattr(dialog, "_render_browser_from_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dialog._sync_client,
        "install_variant",
        lambda _variant_id: copy_model(remote_entry, update={"installed": True, "hasCachedContent": True}),
    )

    pulled = dialog._pull_selected_variant(force_replace_local=True)

    assert pulled is not None
    saved_draft = dialog._draft_service_for_catalog().draft("replace-me")
    assert saved_draft is not None
    assert saved_draft.publishTargetAssetId is None
    remote_after = service.remote_entry("replace-me")
    assert remote_after is not None
    assert remote_after.installed is True
    assert remote_after.hasCachedContent is True

    dialog.close()


def test_variant_manager_mine_buttons_hide_sync_for_owned_remote_without_local(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-buttons.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-sync",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=3,
        ),
        update={
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._refresh_action_buttons(remote_entry)

    assert dialog._btn_upload.isVisible() is False
    assert dialog._btn_upload.isEnabled() is False

    dialog.close()


def test_variant_manager_resolve_overwrite_target_uses_local_draft_only(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-overwrite.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="owned-name",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=1,
        ),
        update={
            "record": copy_model(_make_entry(variant_id="owned-name", source=F8VariantSourceKind.remote_private).record, update={"name": "Same Name"}),
            "visibility": F8VariantVisibility.private,
            "ownerUserId": "u1",
            "ownerDisplayName": "User One",
        },
    )
    service.replace_remote_entries([remote_entry])
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    draft_service = dialog._draft_service_for_catalog()
    _ = draft_service.create_draft_from_record(
        copy_model(
            remote_entry.record,
            update={
                "variantId": "draft-same-name",
                "name": "Same Name",
                "updatedAt": variant_now_iso(),
            },
        ),
        origin_kind=F8VariantDraftOriginKind.new,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id="draft-same-name",
    )

    target = dialog._resolve_overwrite_target(name="Same Name", overwrite_variant_id=None)
    choices = dialog._overwrite_choices_for_base()

    assert target is not None
    assert target.record.variantId == "draft-same-name"
    assert [choice.asset_id for choice in choices] == ["draft-same-name"]

    dialog.close()


def test_variant_manager_disables_load_offload_for_local_draft(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-draft-actions.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    draft_entry = service.upsert_local_entry(
        F8VariantEntry(
            record=_make_entry(variant_id="draft-disable", source=F8VariantSourceKind.local).record,
            source=F8VariantSourceKind.local,
            isLocalDraft=True,
        )
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)

    dialog._refresh_action_buttons(draft_entry)

    assert dialog._btn_install.isEnabled() is False
    assert dialog._btn_install.toolTip() == "Not available for Local Draft"

    delete_calls: list[str] = []
    monkeypatch.setattr(service, "delete_local_entry", lambda variant_id: delete_calls.append(str(variant_id)) or True)
    monkeypatch.setattr(dialog, "_selected_action_entries", lambda: (draft_entry, draft_entry, None))

    dialog._on_load_or_offload_clicked()

    assert delete_calls == []

    dialog.close()


def test_variant_dialog_skips_redundant_action_button_updates_for_same_selection(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-selection-buttons-skip.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)

    entry = service.upsert_local_entry(_make_entry(variant_id="variant-button-state", source=F8VariantSourceKind.local))

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.record.variantId)
    dialog._list.addItem(item)

    dialog._list.setCurrentRow(0)
    assert dialog._current_action_button_signature is not None

    button_state_calls: list[str] = []

    def _record_button_state(
        button: QtWidgets.QPushButton,
        *,
        visible: bool,
        enabled: bool,
        tooltip: str,
        icon_token: object,
    ) -> None:
        del visible, enabled, tooltip, icon_token
        button_state_calls.append(button.objectName())

    monkeypatch.setattr(VariantCatalogDialog, "_set_button_state", staticmethod(_record_button_state))

    dialog._on_selection_changed()
    dialog._on_selection_changed()

    assert button_state_calls == []

    dialog.close()


def test_variant_dialog_selection_change_resolves_action_entries_once(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-selection-resolution.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)

    entry = service.upsert_local_entry(_make_entry(variant_id="variant-resolution-state", source=F8VariantSourceKind.local))

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    dialog._entries = [entry]
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_DRAFTS)
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.record.variantId)
    dialog._list.addItem(item)
    dialog._list.setCurrentRow(0)

    original_local_entry_for_variant_id = dialog._local_entry_for_variant_id
    original_remote_entry_for_variant_id = dialog._remote_entry_for_variant_id
    local_lookup_calls: list[str] = []
    remote_lookup_calls: list[str] = []

    def _record_local_lookup(variant_id: str):
        local_lookup_calls.append(str(variant_id))
        return original_local_entry_for_variant_id(variant_id)

    def _record_remote_lookup(variant_id: str):
        remote_lookup_calls.append(str(variant_id))
        return original_remote_entry_for_variant_id(variant_id)

    monkeypatch.setattr(dialog, "_local_entry_for_variant_id", _record_local_lookup)
    monkeypatch.setattr(dialog, "_remote_entry_for_variant_id", _record_remote_lookup)

    dialog._current_preview_signature = None
    dialog._current_action_button_signature = None
    dialog._refresh_selected_preview()

    assert local_lookup_calls == ["variant-resolution-state"]
    assert remote_lookup_calls == ["variant-resolution-state"]

    dialog.close()


def test_variant_dialog_skips_redundant_raw_preview_updates_for_same_selection(monkeypatch, tmp_path: Path) -> None:
    _ = _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-selection-raw-skip.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)

    entry = service.upsert_local_entry(_make_entry(variant_id="variant-raw-state", source=F8VariantSourceKind.local))

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.a.op",
        base_node_name="Variant",
        node_graph=None,
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.record.variantId)
    dialog._list.addItem(item)
    dialog._list.setCurrentRow(0)
    assert dialog._current_raw_preview_text is not None

    raw_calls: list[str] = []
    original_set_plain_text = dialog._raw.setPlainText

    def _record_raw_set(text: str) -> None:
        raw_calls.append(str(text))
        original_set_plain_text(text)

    monkeypatch.setattr(dialog._raw, "setPlainText", _record_raw_set)

    dialog._on_selection_changed()
    dialog._on_selection_changed()

    assert raw_calls == []

    dialog.close()


def test_graph_variant_actions_name_conflicts_only_use_local_drafts(monkeypatch) -> None:
    draft_entry = copy_model(
        _make_entry(variant_id="graph-draft", source=F8VariantSourceKind.local),
        update={
            "record": copy_model(
                _make_entry(variant_id="graph-draft", source=F8VariantSourceKind.local).record,
                update={"name": "Graph Name"},
            ),
            "isLocalDraft": True,
        },
    )
    monkeypatch.setattr(
        GraphVariantActionsMixin,
        "_draft_variant_entries_for_base",
        classmethod(lambda cls, node_type: [draft_entry] if node_type == "svc.a.op" else []),
    )

    target = GraphVariantActionsMixin._draft_variant_entry_by_name(
        node_type="svc.a.op",
        name="Graph Name",
    )

    assert target is not None
    assert target.record.variantId == "graph-draft"


def test_graph_variant_actions_overwrite_choices_only_include_drafts(monkeypatch) -> None:
    _ensure_app()
    captured_choice_ids: list[str] = []
    draft_entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="draft-variant",
            kind=F8VariantKind.service,
            baseNodeType="svc.a.op",
            serviceClass="svc.test",
            operatorClass=None,
            name="Draft Variant",
            description="",
            tags=[],
            spec={"label": "Draft Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        isLocalDraft=True,
    )

    class _FakeVariantNode:
        NODE_NAME = "Variant Node"
        type_ = "svc.a.op"
        spec = F8ServiceSpec(serviceClass="svc.test", label="Variant Node")

        def name(self) -> str:
            return "Node Variant"

    host = _GraphVariantSaveHost()

    monkeypatch.setattr(
        GraphVariantActionsMixin,
        "_variant_node_or_none",
        staticmethod(lambda node: node),
    )
    monkeypatch.setattr(
        GraphVariantActionsMixin,
        "_draft_variant_entries_for_base",
        classmethod(lambda cls, node_type: [draft_entry] if node_type == "svc.a.op" else []),
    )

    def _capture_prompt(
        self,
        *,
        default_name: str,
        default_description: str,
        default_tags: list[str],
        overwrite_choices: list[object],
        name_validator: object = None,
    ) -> None:
        del self, default_name, default_description, default_tags, name_validator
        captured_choice_ids.extend([str(choice.asset_id) for choice in overwrite_choices])
        return None

    monkeypatch.setattr(GraphVariantActionsMixin, "_prompt_variant_metadata", _capture_prompt)

    host._save_node_as_variant(_FakeVariantNode())

    assert captured_choice_ids == ["draft-variant"]


def test_variant_manager_history_uses_local_version_browser(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-history-local.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    dialog = VariantCatalogDialog(parent=None, base_node_type="svc.a.op", base_node_name="Variant", node_graph=None)
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    local_entry = dialog._draft_service_for_catalog().list_catalog_entries()
    if not local_entry:
        _ = dialog._draft_service_for_catalog().create_draft_from_record(
            _make_entry(variant_id="local-history", source=F8VariantSourceKind.local).record,
            origin_kind=F8VariantDraftOriginKind.new,
            publish_target_asset_id=None,
            publish_base_remote_version_number=None,
            draft_id="local-history",
        )
    draft_entry = dialog._draft_service_for_catalog().list_catalog_entries()[0]
    info_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_version_flows.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(dialog, "_selected_entry", lambda: draft_entry)

    dialog._on_history_clicked()

    assert info_messages == [("Variant History", "Local drafts do not keep local version history.")]
    dialog.close()


def test_variant_manager_history_uses_remote_version_browser(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "variant-history-remote.ini"), QtCore.QSettings.IniFormat)
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=tmp_path / "assets.db"),
        remote_provider=RemoteCacheProvider(db_path=tmp_path / "assets.db"),
    )
    remote_entry = copy_model(
        _make_entry(
            variant_id="remote-history",
            source=F8VariantSourceKind.remote_private,
            installed=False,
            remote_version_number=1,
        ),
        update={},
    )
    dialog = VariantCatalogDialog(parent=None, base_node_type="svc.a.op", base_node_name="Variant", node_graph=None)
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    _FakeVersionBrowserDialog.seen_titles = []
    _FakeVersionBrowserDialog.seen_item_counts = []

    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_version_flows.AssetVersionBrowserDialog", _FakeVersionBrowserDialog)
    monkeypatch.setattr(dialog, "_selected_entry", lambda: remote_entry)
    monkeypatch.setattr(
        dialog._sync_client,
        "list_variant_versions",
        lambda variant_id: F8VariantRemoteVersionList(
                versions=[
                    F8VariantRemoteVersionEntry(
                        variantId="remote-history",
                        assetType="variant",
                        versionNumber=1,
                        createdAt=variant_now_iso(),
                        createdByUserId="u1",
                        changeSummary="first",
                    ),
                    F8VariantRemoteVersionEntry(
                        variantId="remote-history",
                        assetType="variant",
                        versionNumber=2,
                        createdAt=variant_now_iso(),
                        createdByUserId="u1",
                        changeSummary="second",
                    ),
                ]
            ),
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "get_variant_version",
        lambda variant_id, version_number: remote_entry,
    )

    dialog._on_history_clicked()

    assert _FakeVersionBrowserDialog.seen_titles == [f"Variant History - {remote_entry.record.name}"]
    assert _FakeVersionBrowserDialog.seen_item_counts == [2]
    dialog.close()
