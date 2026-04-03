from __future__ import annotations

import json
import logging
import zlib
from typing import Protocol, cast
from urllib import error, parse, request

import msgspec
from qtpy import QtCore

from f8pysdk.msgspec_codec import copy_model, validate_as

from ..common import JsonObject, json_object_from_value, json_object_loads
from .variant_catalog import VariantCatalogService
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
    F8VariantSyncState,
    F8VariantVisibility,
)
from f8pysdk import F8VariantRecord

logger = logging.getLogger(__name__)


class VariantSyncClient:
    _SETTINGS_GROUP: str = "variants/remote_sync/v1"
    _DEFAULT_BASE_URL: str = "https://assetcloud.feel8.fun"
    _USER_AGENT: str = "F8Studio/1.0"
    _SAVED_SESSIONS_KEY: str = "saved_sessions"
    _CURRENT_ACCOUNT_ID_KEY: str = "current_account_id"

    def __init__(self, *, settings: QtCore.QSettings | None = None, catalog_service: VariantCatalogService | None = None) -> None:
        self._settings: QtCore.QSettings
        self._settings = QtCore.QSettings() if settings is None else settings
        self._catalog_service: VariantCatalogService
        self._catalog_service = VariantCatalogService() if catalog_service is None else catalog_service
        self._access_token: str = ""

    def base_url(self) -> str:
        saved = self._value_str("base_url").rstrip("/")
        return saved or self._DEFAULT_BASE_URL

    @classmethod
    def default_base_url(cls) -> str:
        return cls._DEFAULT_BASE_URL

    def set_base_url(self, base_url: str) -> None:
        self._set_value("base_url", str(base_url or "").strip().rstrip("/"))

    def remembered_refresh_token(self) -> str:
        return self._value_str("refresh_token")

    def remembered_username(self) -> str:
        return self._value_str("username")

    def saved_sessions(self) -> list[F8VariantRemoteSession]:
        raw = self._value_list(self._SAVED_SESSIONS_KEY)
        if not raw:
            return []
        out: list[F8VariantRemoteSession] = []
        for item in raw:
            try:
                out.append(_remote_session_from_payload(json_object_from_value(item)))
            except Exception:
                logger.exception("Failed to decode saved variant cloud session")
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
        return None

    def current_access_token(self) -> str:
        return self._access_token

    def current_user(self) -> F8VariantRemoteUser | None:
        raw = self._value_json_object("user")
        if not raw:
            return None
        try:
            return _remote_user_from_payload(raw)
        except Exception:
            logger.exception("Failed to load saved variant sync user")
            return None

    def login(self, *, base_url: str, username: str, password: str, remember: bool) -> F8VariantRemoteAuth:
        self.set_base_url(base_url)
        payload = self._post_json(
            "/v1/auth/login",
            {"username": str(username or ""), "password": str(password or "")},
            authorized=False,
        )
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def refresh_auth(self) -> F8VariantRemoteAuth:
        session = self.current_session()
        if session is None:
            refresh_token = self.remembered_refresh_token()
            base_url = self.base_url()
        else:
            refresh_token = str(session.refreshToken)
            base_url = str(session.baseUrl)
        if not refresh_token:
            raise F8VariantRemoteAuthError("No refresh token is available.")
        self.set_base_url(base_url)
        payload = self._post_json(
            "/v1/auth/refresh",
            {"refreshToken": refresh_token},
            authorized=False,
        )
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=True)
        return auth

    def logout(self) -> None:
        session = self.current_session()
        try:
            if self.current_access_token():
                _ = self._post_json("/v1/auth/logout", {}, authorized=True)
        except F8VariantRemoteRequestError:
            logger.exception("Variant remote logout failed")
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
        logger.info(
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
        logger.info(
            "Variant cloud list response scope=%s count=%d next_cursor=%s variant_ids=%s",
            normalized_scope,
            len(page.entries),
            page.nextCursor,
            [str(entry.record.variantId) for entry in page.entries[:10]],
        )
        return page

    def get_variant(self, variant_id: str) -> F8VariantEntry:
        payload = self._request_json("GET", f"/v1/variants/{parse.quote(str(variant_id))}", None, authorized=True)
        return _entry_from_asset_payload(payload)

    def create_variant(self, entry: F8VariantEntry) -> F8VariantEntry:
        payload = self._request_json("POST", "/v1/variants", _asset_write_payload(entry), authorized=True)
        result = _entry_from_asset_payload(payload)
        _ = self._catalog_service.install_remote_entry(result)
        return result

    def update_variant(self, entry: F8VariantEntry) -> F8VariantEntry:
        variant_id = str(entry.record.variantId)
        payload = self._request_json(
            "PUT",
            f"/v1/variants/{parse.quote(variant_id)}",
            _asset_write_payload(entry),
            authorized=True,
        )
        result = _entry_from_asset_payload(payload)
        _ = self._catalog_service.install_remote_entry(result)
        return result

    def delete_variant(self, variant_id: str) -> None:
        _ = self._request_json("DELETE", f"/v1/variants/{parse.quote(str(variant_id))}", None, authorized=True)

    def install_variant(self, variant_id: str) -> F8VariantEntry:
        payload = self._request_json("GET", f"/v1/variants/{parse.quote(str(variant_id))}", None, authorized=True)
        entry = _entry_from_asset_payload(payload)
        return self._catalog_service.install_remote_entry(entry)

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

    def switch_account(self, account_id: str) -> F8VariantRemoteAuth:
        session = self._saved_session_by_id(account_id)
        if session is None:
            raise F8VariantRemoteAuthError("Saved account session was not found.")
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        self.set_base_url(session.baseUrl)
        self._set_value("username", str(session.user.username or ""))
        self._access_token = ""
        return self.refresh_auth()

    def clear_saved_session(self, account_id: str) -> None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return
        remaining = [session for session in self.saved_sessions() if session.accountId != account_id]
        self._set_value(self._SAVED_SESSIONS_KEY, [_remote_session_payload(session) for session in remaining])
        if self.current_account_id() == account_id:
            self._clear_current_auth_state()

    def clear_all_saved_sessions(self) -> None:
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
        logger.info(
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
        refreshed: list[F8VariantEntry] = []
        for entry in page.entries:
            if entry.syncState == F8VariantSyncState.local_only:
                entry = copy_remote_entry_as_synced(entry)
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
        logger.info(
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
        try:
            if entry.remoteRevision:
                return self.update_variant(entry)
            return self.create_variant(entry)
        except F8VariantRemoteConflictError as exc:
            _ = self._catalog_service.mark_conflict(str(entry.record.variantId), remote_revision=exc.remote_revision)
            raise

    def _request_json(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
        try:
            return self._request_json_once(method, path, payload, authorized=authorized)
        except F8VariantRemoteAuthError:
            if not authorized:
                raise
            _ = self.refresh_auth()
            return self._request_json_once(method, path, payload, authorized=True)

    def _request_json_once(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
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
        if payload is not None:
            raw_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(raw_data) > 4096:
                # compress with wbits=31 for gzip format (not just deflate)
                data = zlib.compress(raw_data, level=6, wbits=31)
                headers["Content-Encoding"] = "gzip"
            else:
                data = raw_data

        if authorized:
            access_token = self.current_access_token()
            if not access_token:
                raise F8VariantRemoteAuthError("Not logged in.")
            headers["Authorization"] = f"Bearer {access_token}"

        req = request.Request(url=url, data=data, headers=headers, method=method)
        try:
            response_context = cast(_HttpResponseContext, request.urlopen(req, timeout=10))
            with response_context as response_like:
                content_encoding = response_like.headers.get("Content-Encoding")
                raw_bytes = response_like.read()
                if content_encoding == "gzip":
                    try:
                        raw_bytes = zlib.decompress(raw_bytes, wbits=31)
                    except Exception:
                        logger.exception("Failed to decompress variant cloud response")

                raw_body = raw_bytes.decode("utf-8")
                logger.debug("Variant cloud %s %s status=%s body=%s", method, url, response_like.status, raw_body[:1000])
                if not raw_body:
                    return {}
                return json_object_loads(raw_body)
        except error.HTTPError as exc:
            content_encoding = exc.headers.get("Content-Encoding")
            body_bytes = exc.read()
            if content_encoding == "gzip":
                try:
                    body_bytes = zlib.decompress(body_bytes, wbits=31)
                except Exception:
                    pass
            body_text = body_bytes.decode("utf-8", errors="replace")
            logger.warning("Variant cloud %s %s failed status=%s body=%s", method, url, exc.code, body_text[:1200])
            payload_obj = _try_parse_json_object(body_text)
            message = _error_message(payload_obj) or body_text or str(exc)
            if exc.code == 401:
                raise F8VariantRemoteAuthError(message) from exc
            if exc.code == 409:
                remote_revision = None
                if isinstance(payload_obj.get("remoteRevision"), str):
                    remote_revision = str(payload_obj["remoteRevision"])
                raise F8VariantRemoteConflictError(
                    message or "Variant update conflict",
                    variant_id=_conflict_variant_id(payload_obj, path),
                    remote_revision=remote_revision,
                ) from exc
            raise F8VariantRemoteRequestError(message or f"HTTP {exc.code}", status_code=exc.code) from exc
        except error.URLError as exc:
            logger.warning("Variant cloud %s %s url_error=%s", method, url, str(exc.reason or exc))
            raise F8VariantRemoteRequestError(str(exc.reason or exc)) from exc

    def _post_json(self, path: str, payload: JsonObject, *, authorized: bool) -> JsonObject:
        return self._request_json("POST", path, payload, authorized=authorized)

    def _update_cached_remote_entry(self, entry: F8VariantEntry) -> F8VariantEntry:
        current = self._catalog_service.load_remote_entries()
        existing_by_id: dict[str, F8VariantEntry] = {
            str(current_entry.record.variantId): current_entry for current_entry in current
        }
        existing = existing_by_id.get(str(entry.record.variantId))
        if existing is not None:
            entry = copy_model(
                entry,
                update={
                    "installed": bool(existing.installed or entry.installed),
                    "downloadedAt": entry.downloadedAt or existing.downloadedAt,
                },
            )
        out: list[F8VariantEntry] = []
        replaced = False
        for current_entry in current:
            if str(current_entry.record.variantId) == str(entry.record.variantId):
                out.append(entry)
                replaced = True
            else:
                out.append(current_entry)
        if not replaced:
            out.append(entry)
        self._catalog_service.replace_remote_entries(out)
        return entry

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
        self._access_token = str(auth.accessToken)
        self._set_value("user", _remote_user_payload(auth.user))
        self._set_value("username", str(auth.user.username or ""))
        refresh_token = str(auth.refreshToken)
        self._set_value("refresh_token", refresh_token)
        session = F8VariantRemoteSession(
            accountId=_account_id_for(base_url=base_url, user=auth.user),
            baseUrl=str(base_url).strip().rstrip("/"),
            refreshToken=refresh_token,
            user=auth.user,
            lastUsedAt=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
        )
        self._upsert_saved_session(session)
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        if not remember:
            logger.info("Variant cloud login now persists account sessions for account switching support.")

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
        out.sort(key=lambda item: (item.baseUrl.lower(), (item.user.displayName or "").lower(), item.user.userId))
        self._set_value(self._SAVED_SESSIONS_KEY, [_remote_session_payload(item) for item in out])

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
        self._set_value("refresh_token", "")
        self._set_value("user", {})
        self._set_value("username", "")


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
    asset_id = payload.get("assetId")
    if isinstance(asset_id, str) and asset_id.strip():
        return asset_id.strip()
    return str(path.rstrip("/").split("/")[-1])


def _account_id_for(*, base_url: str, user: F8VariantRemoteUser) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    normalized_username = str(user.username or user.userId).strip()
    return f"{normalized_base_url}::{normalized_username.lower()}"


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


def _asset_write_payload(entry: F8VariantEntry) -> JsonObject:
    payload: JsonObject = {
        "record": _record_payload(entry),
        "visibility": (None if entry.visibility is None else entry.visibility.value),
    }
    if entry.remoteRevision:
        payload["revision"] = str(entry.remoteRevision)
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
    if not isinstance(record_payload, dict):
        raise F8VariantRemoteRequestError("Variant remote payload is missing record.")
    source = _source_from_asset_payload(payload)
    visibility = _visibility_from_payload(payload)
    return F8VariantEntry(
        record=validate_as(F8VariantRecord, json_object_from_value(cast(object, record_payload))),
        source=source,
        visibility=visibility,
        ownerUserId=_payload_optional_str(payload, "ownerUserId"),
        ownerDisplayName=_payload_optional_str(payload, "ownerDisplayName"),
        librarySlug=_payload_optional_str(payload, "librarySlug") or _library_slug_from_payload(payload, source),
        remoteRevision=(
            _payload_optional_str(payload, "revision")
            or _payload_optional_str(payload, "latestRevision")
            or _payload_optional_str(payload, "remoteRevision")
        ),
        syncState=F8VariantSyncState.synced,
        downloadedAt=_payload_optional_str(payload, "downloadedAt"),
        installed=_payload_bool(payload, "installed", default=_installed_from_asset_payload(payload)),
        subscribed=_payload_bool(payload, "subscribed", default=False),
    )


def _source_from_asset_payload(payload: JsonObject) -> F8VariantSourceKind:
    source_kind = _payload_optional_str(payload, "sourceKind")
    if source_kind == F8VariantSourceKind.remote_official.value:
        return F8VariantSourceKind.remote_official
    visibility = str(payload.get("visibility") or "").strip()
    if visibility == "public":
        return F8VariantSourceKind.remote_public
    return F8VariantSourceKind.remote_private


def _installed_from_asset_payload(payload: JsonObject) -> bool:
    editable = payload.get("editable")
    if isinstance(editable, bool) and editable:
        return True
    subscribed = payload.get("subscribed")
    if isinstance(subscribed, bool):
        return subscribed
    return False


def _library_slug_from_payload(payload: JsonObject, source: F8VariantSourceKind) -> str | None:
    if source in {F8VariantSourceKind.remote_public, F8VariantSourceKind.remote_official}:
        return "community"
    owner_user_id = payload.get("ownerUserId")
    if isinstance(owner_user_id, str) and owner_user_id.strip():
        return f"user/{owner_user_id.strip()}"
    return None


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


def copy_remote_entry_as_synced(entry: F8VariantEntry) -> F8VariantEntry:
    return copy_model(entry, update={"syncState": F8VariantSyncState.synced})


def _remote_user_from_payload(payload: JsonObject) -> F8VariantRemoteUser:
    return validate_as(F8VariantRemoteUser, payload)


def _remote_user_payload(user: F8VariantRemoteUser) -> JsonObject:
    return {
        "userId": str(user.userId),
        "displayName": str(user.displayName),
        "username": None if user.username is None else str(user.username),
    }


def _remote_auth_from_payload(payload: JsonObject) -> F8VariantRemoteAuth:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise F8VariantRemoteRequestError("Variant auth response is missing user.")
    return F8VariantRemoteAuth(
        accessToken=_payload_str(payload, "accessToken"),
        refreshToken=_payload_str(payload, "refreshToken"),
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
    )


def _remote_session_from_payload(payload: JsonObject) -> F8VariantRemoteSession:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise ValueError("Saved variant session is missing user.")
    return F8VariantRemoteSession(
        accountId=_payload_str(payload, "accountId"),
        baseUrl=_payload_str(payload, "baseUrl"),
        refreshToken=_payload_str(payload, "refreshToken"),
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
        lastUsedAt=_payload_str(payload, "lastUsedAt"),
    )


def _remote_session_payload(session: F8VariantRemoteSession) -> JsonObject:
    return {
        "accountId": str(session.accountId),
        "baseUrl": str(session.baseUrl),
        "refreshToken": str(session.refreshToken),
        "user": _remote_user_payload(session.user),
        "lastUsedAt": str(session.lastUsedAt),
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
                assetId=_payload_str(version_payload, "assetId"),
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


def _payload_int(payload: JsonObject, key: str) -> int:
    return int(str(payload[key]))
