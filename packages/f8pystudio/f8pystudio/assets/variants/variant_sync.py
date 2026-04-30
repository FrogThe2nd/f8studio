from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import socket
import zlib
from typing import cast
from urllib import error, parse, request

import msgspec
from qtpy import QtCore

from f8pysdk.codec import copy_model, validate_as
from f8pysdk.specs import coerce_spec_payload

from ..common import (
    JsonObject,
    AssetCloudCredentialStore,
    decode_http_response_text,
    default_asset_cloud_credential_store,
    json_object_from_value,
    json_object_loads,
    origin_headers_for_base_url,
    redact_http_body_for_log,
    resolve_asset_cloud_base_url,
    saved_session_account_ids_from_raw,
)
from ..common.remote_http import (
    HttpResponseContext,
    build_json_request_data,
    session_cookie_from_headers,
)
from ..common.remote_sessions import (
    current_session_base_url_from_raw,
    remote_session_payload_base,
    saved_session_by_id,
    session_matches_base_url,
    upsert_saved_sessions,
)
from .variant_catalog import VariantCatalogService, variant_entry_has_cached_content, variant_entry_is_installed
from .variant_models import (
    F8VariantEntry,
    F8VariantRemoteAuth,
    F8VariantRemoteAuthError,
    F8VariantRemoteConflictError,
    F8VariantRemoteListPage,
    F8VariantRemoteRequestError,
    F8VariantRemoteSession,
    F8VariantRemoteUser,
    F8VariantRemoteVersionEntry,
    F8VariantRemoteVersionList,
    F8VariantLocalVersionSummary,
    F8VariantSourceKind,
    F8VariantVisibility,
)
from f8pysdk.specs import F8VariantKind, F8VariantRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VariantRemoteScopeRefreshRequest:
    scope: str
    kind: str = ""
    base_node_type: str = ""
    query: str = ""
    cursor: str = ""
    append: bool = False


@dataclass(frozen=True)
class VariantRemoteScopeRefreshResult:
    requests: tuple[VariantRemoteScopeRefreshRequest, ...]
    pages_by_scope: dict[str, F8VariantRemoteListPage]
    remote_entries: list[F8VariantEntry]


class VariantSyncClient:
    _SETTINGS_GROUP: str = "assetcloud/v1"
    _DEFAULT_BASE_URL: str = "https://assetcloud.feel8.fun"
    _USER_AGENT: str = "F8Studio/1.0"
    _SAVED_SESSIONS_KEY: str = "saved_sessions"
    _CURRENT_ACCOUNT_ID_KEY: str = "current_account_id"

    def __init__(
        self,
        *,
        settings: QtCore.QSettings | None = None,
        catalog_service: VariantCatalogService | None = None,
        credential_store: AssetCloudCredentialStore | None = None,
    ) -> None:
        self._settings: QtCore.QSettings
        self._settings = QtCore.QSettings() if settings is None else settings
        self._catalog_service: VariantCatalogService
        self._catalog_service = VariantCatalogService() if catalog_service is None else catalog_service
        self._credential_store: AssetCloudCredentialStore
        self._credential_store = default_asset_cloud_credential_store() if credential_store is None else credential_store
        self._access_token: str = ""
        self._access_token_account_id: str = ""

    def base_url(self) -> str:
        return resolve_asset_cloud_base_url(
            saved_base_url=self._value_str("base_url"),
            default_base_url=self._DEFAULT_BASE_URL,
            fallback_base_url=self._current_session_base_url(),
        )

    def clone_for_background(self) -> VariantSyncClient:
        return VariantSyncClient(
            settings=self._settings,
            catalog_service=self._catalog_service,
            credential_store=self._credential_store,
        )

    @classmethod
    def default_base_url(cls) -> str:
        return cls._DEFAULT_BASE_URL

    def set_base_url(self, base_url: str) -> None:
        self._set_value("base_url", str(base_url or "").strip().rstrip("/"))

    def catalog_db_path(self) -> Path:
        return self._catalog_service.db_path

    def load_cached_remote_entries(self) -> list[F8VariantEntry]:
        return self._catalog_service.load_remote_entries()

    def load_all_catalog_entries(self) -> list[F8VariantEntry]:
        return self._catalog_service.load_all_entries()

    def replace_cached_remote_entries(self, entries: list[F8VariantEntry], *, emit_changed: bool = True) -> None:
        self._catalog_service.replace_remote_entries(entries, emit_changed=emit_changed)

    def cache_cached_remote_entry(self, entry: F8VariantEntry, *, emit_changed: bool = True) -> F8VariantEntry:
        return self._catalog_service.cache_remote_entry(entry, emit_changed=emit_changed)

    def uninstall_cached_variant(self, variant_id: str) -> F8VariantEntry | None:
        return self._catalog_service.uninstall_remote_entry(variant_id)

    def list_local_variant_versions(self, variant_id: str) -> list[F8VariantLocalVersionSummary]:
        return self._catalog_service.list_local_versions(variant_id)

    def local_variant_version_record(self, variant_id: str, version_number: int) -> F8VariantRecord | None:
        return self._catalog_service.local_version_record(variant_id, version_number)

    def remembered_email(self) -> str:
        return self._value_str("email")

    def saved_sessions(self) -> list[F8VariantRemoteSession]:
        raw = self._value_list(self._SAVED_SESSIONS_KEY)
        if not raw:
            return []
        out: list[F8VariantRemoteSession] = []
        sanitized_payloads: list[JsonObject] = []
        changed = False
        for item in raw:
            if not isinstance(item, dict):
                changed = True
                continue
            payload = json_object_from_value(item)
            try:
                session = _remote_session_from_payload(
                    payload,
                    credential_store=self._credential_store,
                )
            except ValueError as exc:
                changed = True
                logger.warning("Dropping saved variant session with missing keyring refresh token: %s", str(exc))
                continue
            out.append(session)
            sanitized_payloads.append(_remote_session_payload(session))
        if changed:
            self._set_value(self._SAVED_SESSIONS_KEY, sanitized_payloads)
        return out

    def current_account_id(self) -> str:
        return self._value_str(self._CURRENT_ACCOUNT_ID_KEY)

    def current_session(self) -> F8VariantRemoteSession | None:
        account_id = self.current_account_id()
        if not account_id:
            return None
        session = saved_session_by_id(self.saved_sessions(), account_id=account_id)
        if session is None:
            self._clear_current_auth_state()
            return None
        if not self._session_matches_current_base_url(session):
            return None
        return session

    def current_access_token(self) -> str:
        current_account_id = self.current_account_id()
        session = self.current_session()
        if session is None:
            if self._access_token and self._access_token_account_id == current_account_id:
                self._access_token = ""
                self._access_token_account_id = ""
            return ""
        if self._access_token and self._access_token_account_id == current_account_id:
            return self._access_token
        if self._access_token and self._access_token_account_id != current_account_id:
            self._access_token = ""
            self._access_token_account_id = ""
        return ""

    def current_user(self) -> F8VariantRemoteUser | None:
        if self.current_account_id() and self.current_session() is None:
            return None
        raw = self._value_json_object("user")
        if not raw:
            return None
        return _remote_user_from_payload(raw)

    def login(self, *, base_url: str, email: str, password: str, remember: bool) -> F8VariantRemoteAuth:
        self.set_base_url(base_url)
        _, browser_session_cookie = self._request_json_response(
            "POST",
            "/api/auth/sign-in/email",
            {"email": str(email or ""), "password": str(password or "")},
            authorized=False,
        )
        if not browser_session_cookie:
            raise F8VariantRemoteAuthError("Variant sign-in succeeded but no browser session cookie was returned.")
        auth = self._exchange_browser_session_for_tokens(browser_session_cookie)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def _exchange_browser_session_for_tokens(self, browser_session_cookie: str) -> F8VariantRemoteAuth:
        payload, _ = self._request_json_response(
            "POST",
            "/v1/auth/desktop/session",
            {},
            authorized=False,
            session_cookie_override=browser_session_cookie,
        )
        return _remote_auth_from_payload(payload)

    def exchange_browser_auth_code(
        self,
        *,
        base_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        remember: bool,
    ) -> F8VariantRemoteAuth:
        self.set_base_url(base_url)
        payload = self._request_json(
            "POST",
            "/v1/auth/desktop/token",
            {
                "clientId": str(client_id),
                "code": str(code),
                "redirectUri": str(redirect_uri),
                "codeVerifier": str(code_verifier),
            },
            authorized=False,
        )
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def refresh_auth(self) -> F8VariantRemoteAuth:
        session = self.current_session()
        if session is None:
            raise F8VariantRemoteAuthError("No saved cloud session is available.")
        base_url = str(session.baseUrl)
        self.set_base_url(base_url)
        try:
            payload = self._request_json(
                "POST",
                "/v1/auth/desktop/refresh",
                {"refreshToken": str(session.refreshToken)},
                authorized=False,
                retry_on_auth_failure=False,
            )
        except F8VariantRemoteAuthError as exc:
            self._handle_invalid_saved_session(session=session, reason=str(exc))
            raise F8VariantRemoteAuthError(
                self._expired_session_message(session)
            ) from exc
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=True)
        return auth

    def logout(self) -> None:
        session = self.current_session()
        try:
            if session is not None and str(session.refreshToken).strip():
                _ = self._request_json(
                    "POST",
                    "/v1/auth/desktop/revoke",
                    {"refreshToken": str(session.refreshToken)},
                    authorized=False,
                    retry_on_auth_failure=False,
                )
        except (F8VariantRemoteAuthError, F8VariantRemoteRequestError) as exc:
            logger.warning(
                "Variant remote sign-out failed; cleared local session anyway: %s",
                str(exc),
            )
        self._access_token = ""
        self._access_token_account_id = ""
        if session is not None:
            self.clear_saved_session(session.accountId)
        else:
            self._clear_current_auth_state()

    def me(self) -> F8VariantRemoteUser:
        payload = self._request_json("GET", "/v1/me", None, authorized=True)
        user = _remote_user_from_payload(payload)
        self._set_value("user", _remote_user_payload(user))
        return user

    def list_variants(
        self,
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
    ) -> F8VariantRemoteListPage:
        return self.list_variants_for_refresh(
            scope=scope,
            kind=kind,
            base_node_type=base_node_type,
            query=query,
            cursor=cursor,
            retry_on_auth_failure=True,
        )

    def list_variants_for_refresh(
        self,
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
        retry_on_auth_failure: bool,
    ) -> F8VariantRemoteListPage:
        normalized_scope = str(scope or "").strip()
        params = _list_params_for_scope(
            scope=normalized_scope,
            kind=str(kind or "").strip(),
            base_node_type=str(base_node_type or "").strip(),
            query=str(query or "").strip(),
            cursor=str(cursor or "").strip(),
        )
        encoded = parse.urlencode({key: value for key, value in params.items() if value})
        suffix = f"?{encoded}" if encoded else ""
        authorized = normalized_scope != "community" or bool(self.current_access_token())
        logger.debug(
            "Variant cloud list request scope=%s base_node_type=%s kind=%s query=%s cursor=%s authorized=%s url=%s/v1/variants%s",
            normalized_scope,
            str(base_node_type or "").strip(),
            str(kind or "").strip(),
            str(query or "").strip(),
            str(cursor or "").strip(),
            authorized,
            self.base_url(),
            suffix,
        )
        payload = self._request_json(
            "GET",
            f"/v1/variants{suffix}",
            None,
            authorized=authorized,
            retry_on_auth_failure=retry_on_auth_failure,
        )
        page = _page_from_asset_payload(payload)
        logger.debug(
            "Variant cloud list response scope=%s count=%d next_cursor=%s variant_ids=%s",
            normalized_scope,
            len(page.entries),
            page.nextCursor,
            [str(entry.record.variantId) for entry in page.entries[:10]],
        )
        return page

    def get_variant(self, variant_id: str) -> F8VariantEntry:
        payload = self._request_json(
            "GET",
            f"/v1/variants/{parse.quote(str(variant_id))}",
            None,
            authorized=bool(self.current_access_token()),
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def get_variant_content(self, variant_id: str) -> F8VariantRecord:
        payload = self._request_json(
            "GET",
            f"/v1/variants/{parse.quote(str(variant_id))}/content",
            None,
            authorized=bool(self.current_access_token()),
        )
        return _variant_record_from_content_payload(payload, variant_id=variant_id)

    def cache_variant_content(self, variant_id: str) -> F8VariantEntry:
        detail_entry = self.get_variant(variant_id)
        record = self.get_variant_content(variant_id)
        cached_entry = copy_model(detail_entry, update={"record": record, "installed": False, "hasCachedContent": True})
        return self._catalog_service.cache_remote_entry(cached_entry)

    def load_variant_preview_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        variant_id = str(entry.record.variantId or "").strip()
        if not variant_id:
            raise F8VariantRemoteRequestError("Variant preview is missing variantId.")
        record = self.get_variant_content(variant_id)
        return copy_model(entry, update={"record": record, "installed": False, "hasCachedContent": True})

    def hydrate_variant(self, variant_id: str) -> F8VariantEntry:
        cached_entry = self.cache_variant_content(variant_id)
        return self._catalog_service.install_remote_entry(cached_entry)

    def create_variant(self, entry: F8VariantEntry, *, change_summary: str | None = None) -> F8VariantEntry:
        _require_variant_record_for_upload(entry.record)
        payload = self._request_json("POST", "/v1/variants", _asset_write_payload(entry, change_summary=change_summary), authorized=True)
        result = copy_model(_entry_from_asset_payload(payload), update={"record": entry.record, "installed": True, "hasCachedContent": True})
        return self._catalog_service.install_remote_entry(result)

    def update_variant(self, entry: F8VariantEntry, *, change_summary: str | None = None) -> F8VariantEntry:
        _require_variant_record_for_upload(entry.record)
        variant_id = str(entry.record.variantId)
        payload = self._request_json(
            "PUT",
            f"/v1/variants/{parse.quote(variant_id)}",
            _asset_write_payload(entry, change_summary=change_summary),
            authorized=True,
        )
        result = copy_model(_entry_from_asset_payload(payload), update={"record": entry.record, "installed": True, "hasCachedContent": True})
        return self._catalog_service.install_remote_entry(result)

    def delete_variant(self, variant_id: str) -> None:
        _ = self._request_json("DELETE", f"/v1/variants/{parse.quote(str(variant_id))}", None, authorized=True)
        _ = self._catalog_service.delete_remote_entry(str(variant_id))

    def install_variant(self, variant_id: str) -> F8VariantEntry:
        return self.hydrate_variant(variant_id)

    def update_variant_visibility(
        self,
        variant_id: str,
        *,
        visibility: F8VariantVisibility,
        version_number: int | None,
    ) -> F8VariantEntry:
        payload = self._request_json(
            "PUT",
            f"/v1/variants/{parse.quote(str(variant_id))}/visibility",
            {
                "visibility": visibility.value,
                "versionNumber": version_number,
            },
            authorized=True,
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def patch_variant_meta(
        self,
        variant_id: str,
        *,
        name: str,
        description: str,
        tags: list[str],
    ) -> F8VariantEntry:
        """Update variant metadata (name/description/tags) without creating a new content version."""
        payload = self._request_json(
            "PATCH",
            f"/v1/variants/{parse.quote(str(variant_id))}/meta",
            {"name": name, "description": description, "tags": tags},
            authorized=True,
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def subscribe_variant(self, variant_id: str) -> F8VariantEntry:
        payload = self._request_json(
            "POST",
            f"/v1/variants/{parse.quote(str(variant_id))}/subscribe",
            {},
            authorized=True,
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def unsubscribe_variant(self, variant_id: str) -> F8VariantEntry:
        payload = self._request_json(
            "DELETE",
            f"/v1/variants/{parse.quote(str(variant_id))}/subscribe",
            None,
            authorized=True,
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def list_variant_versions(self, variant_id: str) -> F8VariantRemoteVersionList:
        payload = self._request_json("GET", f"/v1/variants/{parse.quote(str(variant_id))}/versions", None, authorized=True)
        return _remote_version_list_from_payload(payload)

    def get_variant_version(self, variant_id: str, version_number: int) -> F8VariantEntry:
        detail_payload = self._request_json(
            "GET",
            f"/v1/variants/{parse.quote(str(variant_id))}/versions/{int(version_number)}",
            None,
            authorized=bool(self.current_access_token()),
        )
        content_payload = self._request_json(
            "GET",
            f"/v1/variants/{parse.quote(str(variant_id))}/versions/{int(version_number)}/content",
            None,
            authorized=bool(self.current_access_token()),
        )
        detail_entry = _entry_from_asset_payload(detail_payload)
        record = _variant_record_from_content_payload(
            content_payload,
            variant_id=variant_id,
            version_number=int(version_number),
        )
        return copy_model(detail_entry, update={"record": record, "installed": True})

    def switch_account(self, account_id: str) -> F8VariantRemoteAuth:
        session = self._saved_session_by_id(account_id)
        if session is None:
            raise F8VariantRemoteAuthError("Saved account session was not found.")
        if not self._session_matches_current_base_url(session):
            raise F8VariantRemoteAuthError(
                f"Saved account session is for {session.baseUrl}, but the current Asset Cloud is {self.base_url()}."
            )
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        self.set_base_url(session.baseUrl)
        self._set_value("user", _remote_user_payload(session.user))
        self._set_value("email", str(session.user.email or ""))
        self._access_token = ""
        self._access_token_account_id = ""
        return self.refresh_auth()

    def clear_saved_session(self, account_id: str) -> None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return
        remaining = [session for session in self.saved_sessions() if session.accountId != account_id]
        self._credential_store.delete_refresh_token(account_id=account_id)
        self._set_value(self._SAVED_SESSIONS_KEY, [_remote_session_payload(session) for session in remaining])
        if self.current_account_id() == account_id:
            self._clear_current_auth_state()

    def clear_all_saved_sessions(self) -> None:
        raw_sessions = self._value_list(self._SAVED_SESSIONS_KEY)
        for account_id in saved_session_account_ids_from_raw(raw_sessions):
            self._credential_store.delete_refresh_token(account_id=account_id)
        self._set_value(self._SAVED_SESSIONS_KEY, [])
        self._clear_current_auth_state()

    def _handle_invalid_saved_session(self, *, session: F8VariantRemoteSession, reason: str) -> None:
        logger.warning(
            "Variant saved session became unauthorized and was cleared account_id=%s email=%s base_url=%s reason=%s",
            str(session.accountId),
            str(session.user.email or session.user.userId),
            str(session.baseUrl),
            str(reason),
        )
        self.clear_saved_session(session.accountId)

    def _expired_session_message(self, session: F8VariantRemoteSession) -> str:
        identity = str(session.user.email or session.user.userId or session.accountId).strip()
        if not identity:
            return "Saved cloud session expired and was cleared. Please sign in again."
        return f"Saved cloud session expired for {identity} and was cleared. Please sign in again."

    def refresh_scope(self, *, scope: str, kind: str = "", base_node_type: str = "", query: str = "") -> list[F8VariantEntry]:
        page = self.refresh_scope_page(
            scope=scope,
            kind=kind,
            base_node_type=base_node_type,
            query=query,
            cursor="",
            append=False,
        )
        return page.entries

    def refresh_scope_page(
        self,
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
        append: bool = False,
    ) -> F8VariantRemoteListPage:
        result = self.collect_remote_scope_refreshes(
            [
                VariantRemoteScopeRefreshRequest(
                    scope=scope,
                    kind=kind,
                    base_node_type=base_node_type,
                    query=query,
                    cursor=cursor,
                    append=append,
                )
            ],
            retry_on_auth_failure=True,
        )
        self.apply_remote_entries(result.remote_entries)
        return result.pages_by_scope[scope]

    def collect_remote_scope_refreshes(
        self,
        requests: list[VariantRemoteScopeRefreshRequest],
        *,
        retry_on_auth_failure: bool,
    ) -> VariantRemoteScopeRefreshResult:
        current_entries = self._catalog_service.load_remote_entries()
        current_user = self.current_user()
        pages_by_scope: dict[str, F8VariantRemoteListPage] = {}
        for request_spec in requests:
            if retry_on_auth_failure:
                page = self.list_variants(
                    scope=request_spec.scope,
                    kind=request_spec.kind,
                    base_node_type=request_spec.base_node_type,
                    query=request_spec.query,
                    cursor=request_spec.cursor,
                )
            else:
                page = self.list_variants_for_refresh(
                    scope=request_spec.scope,
                    kind=request_spec.kind,
                    base_node_type=request_spec.base_node_type,
                    query=request_spec.query,
                    cursor=request_spec.cursor,
                    retry_on_auth_failure=False,
                )
            current_entries = _merge_refreshed_variant_scope_entries(
                current_entries=current_entries,
                page=page,
                scope=request_spec.scope,
                append=request_spec.append,
                user=current_user,
            )
            pages_by_scope[request_spec.scope] = page
        return VariantRemoteScopeRefreshResult(
            requests=tuple(requests),
            pages_by_scope=pages_by_scope,
            remote_entries=current_entries,
        )

    def apply_remote_entries(self, remote_entries: list[F8VariantEntry]) -> None:
        self._catalog_service.replace_remote_entries(remote_entries)

    def remote_entry(self, variant_id: str) -> F8VariantEntry | None:
        return self._catalog_service.remote_entry(variant_id)

    def upload_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        _require_variant_record_for_upload(entry.record)
        try:
            if entry.remoteVersionNumber is not None:
                return self.update_variant(entry)
            return self.create_variant(entry)
        except F8VariantRemoteConflictError as exc:
            _ = self._catalog_service.mark_conflict(str(entry.record.variantId), remote_version_number=exc.remote_version_number)
            raise
        except Exception as exc:
            recovered_entry = self._recover_uploaded_entry(entry)
            if recovered_entry is not None:
                logger.warning(
                    "Variant upload raised after remote write; recovered via follow-up fetch variant_id=%s error=%s",
                    str(entry.record.variantId),
                    str(exc),
                )
                return recovered_entry
            raise

    def _request_json(
        self,
        method: str,
        path: str,
        payload: JsonObject | None,
        *,
        authorized: bool,
        retry_on_auth_failure: bool = True,
    ) -> JsonObject:
        try:
            if authorized and not self.current_access_token():
                _ = self.refresh_auth()
            return self._request_json_once(method, path, payload, authorized=authorized)
        except F8VariantRemoteAuthError:
            if not authorized or not retry_on_auth_failure:
                raise
            _ = self.refresh_auth()
            return self._request_json_once(method, path, payload, authorized=True)

    def _request_json_once(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
        payload_obj, _ = self._request_json_response(method, path, payload, authorized=authorized)
        return payload_obj

    def _request_json_response(
        self,
        method: str,
        path: str,
        payload: JsonObject | None,
        *,
        authorized: bool,
        session_cookie_override: str | None = None,
    ) -> tuple[JsonObject, str]:
        base_url = self.base_url()
        if not base_url:
            raise F8VariantRemoteRequestError("Variant remote base URL is not configured.")
        url = f"{base_url}{path}"
        data: bytes | None = None
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "User-Agent": self._USER_AGENT,
        }
        if path.startswith("/api/auth/") or session_cookie_override:
            headers.update(origin_headers_for_base_url(base_url))
        data, payload_headers = build_json_request_data(payload)
        headers.update(payload_headers)

        if authorized:
            access_token = self.current_access_token()
            if not access_token:
                raise F8VariantRemoteAuthError("Not logged in.")
            headers["Authorization"] = f"Bearer {access_token}"
        elif session_cookie_override:
            headers["Cookie"] = str(session_cookie_override)

        req = request.Request(url=url, data=data, headers=headers, method=method)
        timeout_seconds = _request_timeout_seconds(method=method, path=path)
        try:
            response_context = cast(HttpResponseContext, request.urlopen(req, timeout=timeout_seconds))
            with response_context as response_like:
                content_encoding = response_like.headers.get("Content-Encoding")
                raw_bytes = response_like.read()
                try:
                    raw_body = decode_http_response_text(raw_bytes, content_encoding=str(content_encoding or ""))
                except Exception:
                    logger.exception("Failed to decode variant cloud response")
                    raw_body = raw_bytes.decode("utf-8", errors="replace")
                logger.debug(
                    "Variant cloud %s %s status=%s body=%s",
                    method,
                    url,
                    response_like.status,
                    redact_http_body_for_log(raw_body, max_chars=1000),
                )
                browser_session_cookie = session_cookie_from_headers(response_like.headers)
                if not raw_body:
                    return {}, browser_session_cookie
                try:
                    return json_object_loads(raw_body), browser_session_cookie
                except (ValueError, json.JSONDecodeError) as exc:
                    raise F8VariantRemoteRequestError(
                        f"{method} {path} returned non-JSON response",
                        status_code=response_like.status,
                    ) from exc
        except error.HTTPError as exc:
            try:
                content_encoding = exc.headers.get("Content-Encoding")
                body_bytes = exc.read()
                try:
                    body_text = decode_http_response_text(body_bytes, content_encoding=str(content_encoding or ""))
                except Exception:
                    body_text = body_bytes.decode("utf-8", errors="replace")
                logger.warning(
                    "Variant cloud %s %s failed status=%s body=%s",
                    method,
                    url,
                    exc.code,
                    redact_http_body_for_log(body_text, max_chars=1200),
                )
                payload_obj = _try_parse_json_object(body_text)
                message = _error_message(payload_obj) or body_text or f"{method} {path} failed with HTTP {exc.code}"
                if exc.code == 401:
                    raise F8VariantRemoteAuthError(message) from exc
                if exc.code == 409:
                    remote_version_number = None
                    if payload_obj.get("versionNumber") is not None:
                        remote_version_number = int(str(payload_obj["versionNumber"]))
                    raise F8VariantRemoteConflictError(
                        message or "Variant update conflict",
                        variant_id=_conflict_variant_id(payload_obj, path),
                        remote_version_number=remote_version_number,
                    ) from exc
                raise F8VariantRemoteRequestError(
                    message or f"{method} {path} failed with HTTP {exc.code}",
                    status_code=exc.code,
                ) from exc
            finally:
                exc.close()
        except (TimeoutError, socket.timeout) as exc:
            raise F8VariantRemoteRequestError(
                f"{method} {path} timed out after {timeout_seconds}s",
            ) from exc
        except error.URLError as exc:
            logger.warning("Variant cloud %s %s url_error=%s", method, url, str(exc.reason or exc))
            raise F8VariantRemoteRequestError(f"{method} {path} failed: {str(exc.reason or exc)}") from exc

    def _persist_refresh_token_for_account(self, *, account_id: str, refresh_token: str) -> None:
        normalized_account_id = str(account_id or "").strip()
        normalized_refresh_token = str(refresh_token or "").strip()
        if not normalized_account_id or not normalized_refresh_token:
            return
        stored_refresh_token = self._credential_store.load_refresh_token(account_id=normalized_account_id)
        if stored_refresh_token == normalized_refresh_token:
            return
        self._credential_store.store_refresh_token(
            account_id=normalized_account_id,
            refresh_token=normalized_refresh_token,
        )

    def _post_json(self, path: str, payload: JsonObject, *, authorized: bool) -> JsonObject:
        return self._request_json("POST", path, payload, authorized=authorized)

    def _recover_uploaded_entry(self, entry: F8VariantEntry) -> F8VariantEntry | None:
        variant_id = str(entry.record.variantId or "").strip()
        if not variant_id:
            return None
        try:
            return self.install_variant(variant_id)
        except Exception:
            logger.exception("Variant upload recovery fetch failed variant_id=%s", variant_id)
            return None

    def _update_cached_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        current = self._catalog_service.load_remote_entries()
        out: list[F8VariantEntry] = []
        replaced = False
        updated_entry = entry
        for current_entry in current:
            if str(current_entry.record.variantId) == str(entry.record.variantId):
                merged_entry = _merge_variant_entries(current_entry, entry)
                out.append(merged_entry)
                updated_entry = merged_entry
                replaced = True
            else:
                out.append(current_entry)
        if not replaced:
            out.append(entry)
        self._catalog_service.replace_remote_entries(out)
        return updated_entry

    def _value_object(self, key: str, default: object) -> object:
        self._settings.beginGroup(self._SETTINGS_GROUP)
        try:
            value = cast(object, self._settings.value(key, default))
        finally:
            self._settings.endGroup()
        return value

    def _value_str(self, key: str) -> str:
        value = self._value_object(key, "")
        return str("" if value is None else value).strip()

    def _value_list(self, key: str) -> list[object]:
        value = self._value_object(key, [])
        return cast(list[object], value) if isinstance(value, list) else []

    def _value_json_object(self, key: str) -> JsonObject:
        value = self._value_object(key, {})
        if isinstance(value, dict):
            return json_object_from_value(cast(object, value))
        return {}

    def _set_value(self, key: str, value: object) -> None:
        self._settings.beginGroup(self._SETTINGS_GROUP)
        try:
            self._settings.setValue(key, value)
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def _set_auth(self, auth: F8VariantRemoteAuth, *, base_url: str, remember: bool) -> None:
        session = F8VariantRemoteSession(
            accountId=_account_id_for(base_url=base_url, user=auth.user),
            baseUrl=str(base_url).strip().rstrip("/"),
            refreshToken=str(auth.refreshToken),
            accessTokenExpiresAt=str(auth.accessTokenExpiresAt),
            refreshTokenExpiresAt=str(auth.refreshTokenExpiresAt),
            user=auth.user,
            lastUsedAt=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
            accessToken=str(auth.accessToken),
        )
        self._access_token = str(auth.accessToken)
        self._access_token_account_id = str(session.accountId)
        self._set_value("user", _remote_user_payload(auth.user))
        self._set_value("email", str(auth.user.email or ""))
        self._persist_refresh_token_for_account(account_id=session.accountId, refresh_token=session.refreshToken)
        self._upsert_saved_session(session)
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        if not remember:
            logger.debug("Variant cloud login now persists account sessions for account switching support.")

    def _upsert_saved_session(self, session: F8VariantRemoteSession) -> None:
        out = upsert_saved_sessions(
            self.saved_sessions(),
            session=session,
            sort_key=lambda item: (item.baseUrl.lower(), str(item.user.name).lower(), item.user.userId),
        )
        self._set_value(
            self._SAVED_SESSIONS_KEY,
            [_remote_session_payload(item) for item in out],
        )

    def _saved_session_by_id(self, account_id: str) -> F8VariantRemoteSession | None:
        return saved_session_by_id(self.saved_sessions(), account_id=account_id)

    def _session_matches_current_base_url(self, session: F8VariantRemoteSession) -> bool:
        return session_matches_base_url(session, base_url=self.base_url())

    def _current_session_base_url(self) -> str:
        return current_session_base_url_from_raw(
            self._value_list(self._SAVED_SESSIONS_KEY),
            current_account_id=self.current_account_id(),
        )

    def _clear_current_auth_state(self) -> None:
        self._access_token = ""
        self._access_token_account_id = ""
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, "")
        self._set_value("user", {})
        self._set_value("email", "")

def _try_parse_json_object(raw: str) -> JsonObject:
    try:
        return json_object_loads(str(raw or ""))
    except (ValueError, json.JSONDecodeError):
        return {}


def _error_message(payload: JsonObject) -> str:
    if not payload:
        return ""
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    error_message = payload.get("error")
    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip()
    return ""


def _conflict_variant_id(payload: JsonObject, path: str) -> str:
    variant_id = payload.get("variantId")
    if isinstance(variant_id, str) and variant_id.strip():
        return variant_id.strip()
    return str(path.rstrip("/").split("/")[-1])


def _account_id_for(*, base_url: str, user: F8VariantRemoteUser) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    normalized_email = str(user.email or user.userId).strip().lower()
    return f"{normalized_base_url}::{normalized_email}"


def _list_params_for_scope(
    *,
    scope: str,
    kind: str,
    base_node_type: str,
    query: str,
    cursor: str,
) -> dict[str, str]:
    params: dict[str, str] = {
        "kind": kind,
        "baseNodeType": base_node_type,
        "q": query,
        "cursor": cursor,
    }
    if scope == "community":
        params["owner"] = "public"
        params["visibility"] = "public"
    elif scope == "mine":
        params["owner"] = "me"
    elif scope == "subscribed":
        params["owner"] = "subscribed"
    return params


def _asset_write_payload(entry: F8VariantEntry, *, change_summary: str | None = None) -> JsonObject:
    payload: JsonObject = {
        "record": _record_payload(entry),
        "visibility": (None if entry.visibility is None else entry.visibility.value),
    }
    if entry.remoteVersionNumber is not None:
        payload["versionNumber"] = int(entry.remoteVersionNumber)
    if change_summary is not None and str(change_summary).strip():
        payload["changeSummary"] = str(change_summary)
    return payload


def _page_from_asset_payload(payload: JsonObject) -> F8VariantRemoteListPage:
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise F8VariantRemoteRequestError("Variant remote list response is missing entries.")
    raw_entry_items = cast(list[object], raw_entries)
    entries = [_entry_from_asset_payload(json_object_from_value(cast(object, item))) for item in raw_entry_items if isinstance(item, dict)]
    next_cursor = payload.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        next_cursor = str(next_cursor)
    return F8VariantRemoteListPage(entries=entries, nextCursor=next_cursor)


def _entry_from_asset_payload(payload: JsonObject) -> F8VariantEntry:
    record_payload = payload.get("record")
    if isinstance(record_payload, dict):
        record = validate_as(F8VariantRecord, json_object_from_value(cast(object, record_payload)))
    else:
        record = _summary_variant_record_from_payload(payload)
    source = _source_from_asset_payload(payload)
    visibility = _visibility_from_payload(payload)
    return F8VariantEntry(
        record=record,
        source=source,
        visibility=visibility,
        ownerUserId=_payload_optional_str(payload, "ownerUserId"),
        ownerDisplayName=_payload_optional_str(payload, "ownerDisplayName"),
        remoteVersionNumber=_payload_optional_int(payload, "versionNumber"),
        downloadedAt=_payload_optional_str(payload, "downloadedAt"),
        installed=_payload_bool(payload, "installed", default=_installed_from_asset_payload(payload)),
        hasCachedContent=_payload_bool(payload, "hasCachedContent", default=False),
        subscribed=_payload_bool(payload, "subscribed", default=False),
    )


def _variant_record_from_content_payload(
    payload: JsonObject,
    *,
    variant_id: str,
    version_number: int | None = None,
) -> F8VariantRecord:
    record_payload = payload.get("record")
    if isinstance(record_payload, dict):
        return validate_as(F8VariantRecord, json_object_from_value(cast(object, record_payload)))
    if _looks_like_variant_record_payload(payload):
        return validate_as(F8VariantRecord, payload)
    version_suffix = "" if version_number is None else f" v{int(version_number)}"
    raise F8VariantRemoteRequestError(
        f"Variant content payload is missing record for {variant_id}{version_suffix}."
    )


def _looks_like_variant_record_payload(payload: JsonObject) -> bool:
    return "variantId" in payload and "spec" in payload


def _variant_record_has_full_content(record: F8VariantRecord) -> bool:
    spec = record.spec
    if not isinstance(spec, dict) or not spec:
        return False
    schema_version = spec.get("schemaVersion")
    if schema_version is None:
        return True
    if not isinstance(schema_version, str) or not schema_version.strip():
        return False
    try:
        _ = coerce_spec_payload(cast(JsonObject, spec))
    except (TypeError, ValueError):
        return False
    return True


def _require_variant_record_for_upload(record: F8VariantRecord) -> None:
    if _variant_record_has_full_content(record):
        return
    raise ValueError(
        f"Variant {record.variantId} is missing full spec content and cannot be uploaded. "
        "Load or rebuild the variant before uploading."
    )


def _source_from_asset_payload(payload: JsonObject) -> F8VariantSourceKind:
    visibility = str(payload.get("visibility") or "").strip()
    if visibility == "public":
        return F8VariantSourceKind.remote_public
    return F8VariantSourceKind.remote_private


def _installed_from_asset_payload(payload: JsonObject) -> bool:
    del payload
    return False


def _summary_variant_record_from_payload(payload: JsonObject) -> F8VariantRecord:
    variant_id = _payload_str(payload, "variantId")
    return F8VariantRecord(
        variantId=variant_id,
        kind=F8VariantKind(_payload_str(payload, "variantKind")),
        baseNodeType=_payload_str(payload, "baseNodeType"),
        serviceClass=_payload_str(payload, "serviceClass"),
        operatorClass=_payload_optional_str(payload, "operatorClass"),
        name=_payload_str(payload, "name"),
        spec={},
        createdAt=_payload_str(payload, "createdAt"),
        updatedAt=_payload_str(payload, "updatedAt"),
        description=_payload_optional_str(payload, "description") or "",
        tags=_payload_string_list(payload, "tags"),
    )


def _user_id_for_scope(scope: str, user: F8VariantRemoteUser | None) -> str:
    if scope != "mine" or user is None:
        return ""
    return str(user.userId)


def _entry_matches_scope(entry: F8VariantEntry, *, scope: str, user: F8VariantRemoteUser | None) -> bool:
    if scope == "community":
        return entry.source == F8VariantSourceKind.remote_public
    if scope == "mine":
        user_id = _user_id_for_scope(scope, user)
        return entry.source == F8VariantSourceKind.remote_private or (
            bool(user_id) and str(entry.ownerUserId or "") == user_id
        )
    if scope == "subscribed":
        return bool(entry.subscribed)
    return False
def _request_timeout_seconds(*, method: str, path: str) -> int:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    if normalized_method == "GET" and normalized_path.endswith("/content"):
        return 45
    if normalized_method == "GET" and "/versions/" in normalized_path:
        return 30
    return 10


def _merge_variant_entries(existing_entry: F8VariantEntry, incoming_entry: F8VariantEntry) -> F8VariantEntry:
    if incoming_entry.installed:
        if existing_entry.installed and not incoming_entry.downloadedAt:
            return copy_model(incoming_entry, update={"hasCachedContent": True, "downloadedAt": existing_entry.downloadedAt})
        return incoming_entry
    if not variant_entry_has_cached_content(existing_entry):
        return incoming_entry
    if existing_entry.remoteVersionNumber != incoming_entry.remoteVersionNumber:
        return incoming_entry
    return copy_model(
        incoming_entry,
        update={
            "record": existing_entry.record,
            "installed": bool(variant_entry_is_installed(existing_entry)),
            "hasCachedContent": variant_entry_has_cached_content(existing_entry),
            "downloadedAt": incoming_entry.downloadedAt or existing_entry.downloadedAt,
        },
    )


def _merge_refreshed_variant_scope_entries(
    *,
    current_entries: list[F8VariantEntry],
    page: F8VariantRemoteListPage,
    scope: str,
    append: bool,
    user: F8VariantRemoteUser | None,
) -> list[F8VariantEntry]:
    preserved = [entry for entry in current_entries if not _entry_matches_scope(entry, scope=scope, user=user)]
    existing_scope_entries = [entry for entry in current_entries if _entry_matches_scope(entry, scope=scope, user=user)]
    existing_scope_by_id: dict[str, F8VariantEntry] = {
        str(entry.record.variantId): entry for entry in existing_scope_entries if str(entry.record.variantId).strip()
    }
    refreshed_by_id: dict[str, F8VariantEntry] = {}
    refreshed_ids: list[str] = []
    for entry in page.entries:
        variant_id = str(entry.record.variantId or "").strip()
        if not variant_id:
            continue
        existing_entry = refreshed_by_id.get(variant_id)
        if existing_entry is None:
            existing_entry = existing_scope_by_id.get(variant_id)
        if existing_entry is not None:
            entry = _merge_variant_entries(existing_entry, entry)
        if variant_id not in refreshed_by_id:
            refreshed_ids.append(variant_id)
        refreshed_by_id[variant_id] = entry
    refreshed = [refreshed_by_id[variant_id] for variant_id in refreshed_ids]
    if append:
        merged_scope_entries: dict[str, F8VariantEntry] = {
            str(entry.record.variantId): entry for entry in existing_scope_entries
        }
        for entry in refreshed:
            merged_scope_entries[str(entry.record.variantId)] = entry
        combined_scope_entries = list(merged_scope_entries.values())
    else:
        combined_scope_entries = refreshed
    return preserved + combined_scope_entries


def _remote_user_from_payload(payload: JsonObject) -> F8VariantRemoteUser:
    user_id = _payload_str(payload, "userId")
    name = _payload_str(payload, "name")
    return F8VariantRemoteUser(
        userId=user_id,
        name=name,
        email=_payload_optional_str(payload, "email"),
    )


def _remote_user_payload(user: F8VariantRemoteUser) -> JsonObject:
    payload: JsonObject = {
        "userId": str(user.userId),
        "name": str(user.name),
        "email": None if user.email is None else str(user.email),
    }
    return payload


def _remote_auth_from_payload(payload: JsonObject) -> F8VariantRemoteAuth:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise F8VariantRemoteAuthError("Desktop sign-in succeeded but user payload was missing.")
    return F8VariantRemoteAuth(
        accessToken=_payload_str(payload, "accessToken"),
        accessTokenExpiresAt=_payload_str(payload, "accessTokenExpiresAt"),
        refreshToken=_payload_str(payload, "refreshToken"),
        refreshTokenExpiresAt=_payload_str(payload, "refreshTokenExpiresAt"),
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
    )


def _remote_session_from_payload(
    payload: JsonObject,
    *,
    credential_store: AssetCloudCredentialStore,
) -> F8VariantRemoteSession:
    account_id = _payload_str(payload, "accountId")
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise ValueError("Saved variant session is missing user.")
    refresh_token = credential_store.load_refresh_token(account_id=account_id)
    if not refresh_token:
        raise ValueError("Saved variant session is missing refresh token in keyring.")
    return F8VariantRemoteSession(
        accountId=account_id,
        baseUrl=_payload_str(payload, "baseUrl"),
        refreshToken=refresh_token,
        accessTokenExpiresAt=_payload_optional_str(payload, "accessTokenExpiresAt") or "",
        refreshTokenExpiresAt=_payload_optional_str(payload, "refreshTokenExpiresAt") or "",
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
        lastUsedAt=_payload_str(payload, "lastUsedAt"),
    )


def _remote_session_payload(session: F8VariantRemoteSession) -> JsonObject:
    return {
        **remote_session_payload_base(
            account_id=session.accountId,
            base_url=session.baseUrl,
            last_used_at=session.lastUsedAt,
        ),
        "accessTokenExpiresAt": str(session.accessTokenExpiresAt),
        "refreshTokenExpiresAt": str(session.refreshTokenExpiresAt),
        "user": _remote_user_payload(session.user),
    }


def _remote_version_list_from_payload(payload: JsonObject) -> F8VariantRemoteVersionList:
    raw_versions = payload.get("versions")
    if not isinstance(raw_versions, list):
        raise F8VariantRemoteRequestError("Variant version response is missing versions.")
    versions: list[F8VariantRemoteVersionEntry] = []
    for item in cast(list[object], raw_versions):
        if not isinstance(item, dict):
            continue
        version_payload = json_object_from_value(cast(object, item))
        versions.append(
            F8VariantRemoteVersionEntry(
                variantId=_payload_str(version_payload, "variantId"),
                assetType=_payload_str(version_payload, "assetType"),
                versionNumber=_payload_int(version_payload, "versionNumber"),
                createdAt=_payload_str(version_payload, "createdAt"),
                createdByUserId=_payload_str(version_payload, "createdByUserId"),
                changeSummary=_payload_optional_str(version_payload, "changeSummary"),
            )
        )
    return F8VariantRemoteVersionList(versions=versions)


def _record_payload(entry: F8VariantEntry) -> JsonObject:
    record = entry.record
    if isinstance(record.tags, msgspec.UnsetType):
        tags: list[str] = []
    else:
        tags = [str(tag) for tag in list(record.tags or []) if str(tag).strip()]
    payload: JsonObject = {
        "variantId": str(record.variantId),
        "kind": str(record.kind.value),
        "baseNodeType": str(record.baseNodeType),
        "serviceClass": str(record.serviceClass),
        "name": str(record.name),
        "spec": cast(JsonObject, record.spec),
        "createdAt": str(record.createdAt),
        "updatedAt": str(record.updatedAt),
        "description": str(record.description),
        "tags": tags,
    }
    if not isinstance(record.operatorClass, msgspec.UnsetType):
        payload["operatorClass"] = None if record.operatorClass is None else str(record.operatorClass)
    return payload


def _visibility_from_payload(payload: JsonObject) -> F8VariantVisibility | None:
    raw_visibility = _payload_optional_str(payload, "visibility")
    if raw_visibility is None or not raw_visibility.strip():
        return None
    return F8VariantVisibility(raw_visibility)


def _payload_str(payload: JsonObject, key: str) -> str:
    return str(payload[key])


def _payload_optional_str(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _payload_bool(payload: JsonObject, key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return default


def _payload_optional_int(payload: JsonObject, key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    return int(str(value))


def _payload_string_list(payload: JsonObject, key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in cast(list[object], value):
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _payload_int(payload: JsonObject, key: str) -> int:
    return int(str(payload[key]))
