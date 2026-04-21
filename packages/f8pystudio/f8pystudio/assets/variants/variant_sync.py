from __future__ import annotations

import http.cookies
import json
import logging
import socket
import zlib
from typing import Protocol, cast
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
    F8VariantSourceKind,
    F8VariantVisibility,
)
from f8pysdk.specs import F8VariantKind, F8VariantRecord

logger = logging.getLogger(__name__)


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

    def base_url(self) -> str:
        return resolve_asset_cloud_base_url(
            saved_base_url=self._value_str("base_url"),
            default_base_url=self._DEFAULT_BASE_URL,
        )

    @classmethod
    def default_base_url(cls) -> str:
        return cls._DEFAULT_BASE_URL

    def set_base_url(self, base_url: str) -> None:
        self._set_value("base_url", str(base_url or "").strip().rstrip("/"))

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
                logger.warning("Dropping saved variant session with missing keyring cookie: %s", str(exc))
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
        for session in self.saved_sessions():
            if session.accountId == account_id:
                return session
        self._clear_current_auth_state()
        return None

    def current_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        session = self.current_session()
        if session is not None and str(session.sessionCookie).strip():
            self._access_token = str(session.sessionCookie).strip()
            return self._access_token
        return self._access_token

    def current_user(self) -> F8VariantRemoteUser | None:
        if self.current_account_id() and self.current_session() is None:
            return None
        raw = self._value_json_object("user")
        if not raw:
            return None
        return _remote_user_from_payload(raw)

    def login(self, *, base_url: str, email: str, password: str, remember: bool) -> F8VariantRemoteAuth:
        self.set_base_url(base_url)
        _, session_cookie = self._request_json_response(
            "POST",
            "/api/auth/sign-in/email",
            {"email": str(email or ""), "password": str(password or "")},
            authorized=False,
        )
        if not session_cookie:
            raise F8VariantRemoteAuthError("Variant sign-in succeeded but no session cookie was returned.")
        user = self._fetch_current_user(session_cookie)
        auth = F8VariantRemoteAuth(sessionCookie=session_cookie, user=user)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def refresh_auth(self) -> F8VariantRemoteAuth:
        session = self.current_session()
        if session is None:
            raise F8VariantRemoteAuthError("No saved cloud session is available.")
        session_cookie = str(session.sessionCookie)
        base_url = str(session.baseUrl)
        self.set_base_url(base_url)
        user = self._fetch_current_user(session_cookie)
        auth = F8VariantRemoteAuth(sessionCookie=self._access_token, user=user)
        self._set_auth(auth, base_url=base_url, remember=True)
        return auth

    def logout(self) -> None:
        session = self.current_session()
        try:
            if self.current_access_token():
                _ = self._post_json("/api/auth/sign-out", {}, authorized=True)
        except (F8VariantRemoteAuthError, F8VariantRemoteRequestError) as exc:
            logger.warning(
                "Variant remote sign-out failed; cleared local session anyway: %s",
                str(exc),
            )
        self._access_token = ""
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
        payload = self._request_json("GET", f"/v1/variants{suffix}", None, authorized=authorized)
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
        revision: str | None,
    ) -> F8VariantEntry:
        payload = self._request_json(
            "PUT",
            f"/v1/variants/{parse.quote(str(variant_id))}/visibility",
            {
                "visibility": visibility.value,
                "revision": revision,
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
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        self.set_base_url(session.baseUrl)
        self._set_value("email", str(session.user.email or ""))
        self._access_token = ""
        return self.refresh_auth()

    def clear_saved_session(self, account_id: str) -> None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return
        remaining = [session for session in self.saved_sessions() if session.accountId != account_id]
        self._credential_store.delete_session_cookie(account_id=account_id)
        self._set_value(self._SAVED_SESSIONS_KEY, [_remote_session_payload(session) for session in remaining])
        if self.current_account_id() == account_id:
            self._clear_current_auth_state()

    def clear_all_saved_sessions(self) -> None:
        raw_sessions = self._value_list(self._SAVED_SESSIONS_KEY)
        for account_id in saved_session_account_ids_from_raw(raw_sessions):
            self._credential_store.delete_session_cookie(account_id=account_id)
        self._set_value(self._SAVED_SESSIONS_KEY, [])
        self._clear_current_auth_state()

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
        page = self.list_variants(
            scope=scope,
            kind=kind,
            base_node_type=base_node_type,
            query=query,
            cursor=cursor,
        )
        current = self._catalog_service.load_remote_entries()
        logger.debug(
            "Variant cloud refresh scope=%s cursor=%s append=%s current_remote_cache=%d fetched=%d",
            scope,
            cursor,
            append,
            len(current),
            len(page.entries),
        )
        preserved = [
            entry
            for entry in current
            if not _entry_matches_scope(entry, scope=scope, user=self.current_user())
        ]
        existing_scope_entries = [
            entry
            for entry in current
            if _entry_matches_scope(entry, scope=scope, user=self.current_user())
        ]
        existing_scope_by_id: dict[str, F8VariantEntry] = {
            str(entry.record.variantId): entry for entry in existing_scope_entries if str(entry.record.variantId).strip()
        }
        refreshed: list[F8VariantEntry] = []
        for entry in page.entries:
            existing_entry = existing_scope_by_id.get(str(entry.record.variantId))
            if existing_entry is not None:
                entry = _merge_variant_entries(existing_entry, entry)
            refreshed.append(entry)
        if append:
            merged_scope_entries: dict[str, F8VariantEntry] = {
                str(entry.record.variantId): entry for entry in existing_scope_entries
            }
            for entry in refreshed:
                merged_scope_entries[str(entry.record.variantId)] = entry
            combined_scope_entries = list(merged_scope_entries.values())
        else:
            combined_scope_entries = refreshed
        self._catalog_service.replace_remote_entries(preserved + combined_scope_entries)
        logger.debug(
            "Variant cloud refresh applied scope=%s preserved=%d refreshed=%d scope_total=%d new_remote_cache=%d next_cursor=%s",
            scope,
            len(preserved),
            len(refreshed),
            len(combined_scope_entries),
            len(preserved) + len(combined_scope_entries),
            page.nextCursor,
        )
        return page

    def upload_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        _require_variant_record_for_upload(entry.record)
        try:
            if entry.remoteRevision:
                return self.update_variant(entry)
            return self.create_variant(entry)
        except F8VariantRemoteConflictError as exc:
            _ = self._catalog_service.mark_conflict(str(entry.record.variantId), remote_revision=exc.remote_revision)
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

    def _fetch_current_user(self, session_cookie: str) -> F8VariantRemoteUser:
        payload, _ = self._request_json_response("GET", "/v1/me", None, authorized=False, session_cookie_override=session_cookie)
        user = _remote_user_from_payload(payload)
        self._set_value("user", _remote_user_payload(user))
        return user

    def _request_json(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
        try:
            return self._request_json_once(method, path, payload, authorized=authorized)
        except F8VariantRemoteAuthError:
            if not authorized:
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
        if path.startswith("/api/auth/"):
            headers.update(origin_headers_for_base_url(base_url))
        if payload is not None:
            raw_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(raw_data) > 4096:
                # compress with wbits=31 for gzip format (not just deflate)
                data = zlib.compress(raw_data, level=6, wbits=31)
                headers["Content-Encoding"] = "gzip"
            else:
                data = raw_data

        if authorized:
            session_cookie = self.current_access_token()
            if not session_cookie:
                raise F8VariantRemoteAuthError("Not logged in.")
            headers["Cookie"] = session_cookie
        elif session_cookie_override:
            headers["Cookie"] = str(session_cookie_override)

        req = request.Request(url=url, data=data, headers=headers, method=method)
        timeout_seconds = _request_timeout_seconds(method=method, path=path)
        try:
            response_context = cast(_HttpResponseContext, request.urlopen(req, timeout=timeout_seconds))
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
                session_cookie = _session_cookie_from_headers(response_like.headers)
                if session_cookie:
                    self._access_token = session_cookie
                if not raw_body:
                    return {}, session_cookie
                try:
                    return json_object_loads(raw_body), session_cookie
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
                    remote_revision = None
                    if isinstance(payload_obj.get("revision"), str):
                        remote_revision = str(payload_obj["revision"])
                    raise F8VariantRemoteConflictError(
                        message or "Variant update conflict",
                        variant_id=_conflict_variant_id(payload_obj, path),
                        remote_revision=remote_revision,
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
        self._access_token = str(auth.sessionCookie)
        self._set_value("user", _remote_user_payload(auth.user))
        self._set_value("email", str(auth.user.email or ""))
        session_cookie = str(auth.sessionCookie)
        session = F8VariantRemoteSession(
            accountId=_account_id_for(base_url=base_url, user=auth.user),
            baseUrl=str(base_url).strip().rstrip("/"),
            sessionCookie=session_cookie,
            user=auth.user,
            lastUsedAt=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
        )
        self._credential_store.store_session_cookie(account_id=session.accountId, session_cookie=session_cookie)
        self._upsert_saved_session(session)
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        if not remember:
            logger.debug("Variant cloud login now persists account sessions for account switching support.")

    def _upsert_saved_session(self, session: F8VariantRemoteSession) -> None:
        out: list[F8VariantRemoteSession] = []
        replaced = False
        for current in self.saved_sessions():
            if current.accountId == session.accountId:
                out.append(session)
                replaced = True
            else:
                out.append(current)
        if not replaced:
            out.append(session)
        out.sort(key=lambda item: (item.baseUrl.lower(), str(item.user.name).lower(), item.user.userId))
        self._set_value(
            self._SAVED_SESSIONS_KEY,
            [_remote_session_payload(item) for item in out],
        )

    def _saved_session_by_id(self, account_id: str) -> F8VariantRemoteSession | None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return None
        for session in self.saved_sessions():
            if session.accountId == normalized_account_id:
                return session
        return None

    def _clear_current_auth_state(self) -> None:
        self._access_token = ""
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, "")
        self._set_value("user", {})
        self._set_value("email", "")

class _HttpResponseLike(Protocol):
    status: int

    def read(self) -> bytes: ...


class _HttpResponseContext(Protocol):
    def __enter__(self) -> _HttpResponseLike: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...


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
    if entry.remoteRevision:
        payload["revision"] = str(entry.remoteRevision)
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
        remoteRevision=_payload_optional_str(payload, "revision"),
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
    if str(existing_entry.remoteRevision or "") != str(incoming_entry.remoteRevision or ""):
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


def _remote_session_from_payload(
    payload: JsonObject,
    *,
    credential_store: AssetCloudCredentialStore,
) -> F8VariantRemoteSession:
    account_id = _payload_str(payload, "accountId")
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise ValueError("Saved variant session is missing user.")
    session_cookie = credential_store.load_session_cookie(account_id=account_id)
    if not session_cookie:
        raise ValueError("Saved variant session is missing session cookie in keyring.")
    return F8VariantRemoteSession(
        accountId=account_id,
        baseUrl=_payload_str(payload, "baseUrl"),
        sessionCookie=session_cookie,
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
        lastUsedAt=_payload_str(payload, "lastUsedAt"),
    )


def _remote_session_payload(session: F8VariantRemoteSession) -> JsonObject:
    return {
        "accountId": str(session.accountId),
        "baseUrl": str(session.baseUrl),
        "user": _remote_user_payload(session.user),
        "lastUsedAt": str(session.lastUsedAt),
    }


def _session_cookie_from_headers(headers: object) -> str:
    values = _header_values(headers, "Set-Cookie")
    if not values:
        return ""
    cookie = http.cookies.SimpleCookie()
    for value in values:
        try:
            cookie.load(value)
        except http.cookies.CookieError:
            logger.warning("Ignoring invalid Set-Cookie header from variant cloud")
    parts: list[str] = []
    for morsel in cookie.values():
        parts.append(f"{morsel.key}={morsel.value}")
    return "; ".join(parts)


def _header_values(headers: object, name: str) -> list[str]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        if isinstance(values, list):
            return [str(value) for value in values if str(value).strip()]
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None and str(value).strip():
            return [str(value)]
    return []


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
                revision=_payload_str(version_payload, "revision"),
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
