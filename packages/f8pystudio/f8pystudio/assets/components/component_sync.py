from __future__ import annotations

import zlib
import http.cookies
import json
import logging
import socket
from typing import Protocol, cast
from urllib import error, parse, request

from qtpy import QtCore

from f8pysdk.codec import copy_model, validate_as

from ..common import (
    JsonObject,
    decode_http_response_text,
    json_object_from_value,
    json_object_loads,
    origin_headers_for_base_url,
    resolve_asset_cloud_base_url,
)
from .component_catalog import (
    ComponentCatalogService,
    component_entry_can_hydrate,
    component_entry_has_cached_content,
)
from .component_models import (
    F8ComponentEntry,
    F8ComponentRemoteAuth,
    F8ComponentRemoteAuthError,
    F8ComponentRemoteConflictError,
    F8ComponentRemoteListPage,
    F8ComponentRemoteRequestError,
    F8ComponentRemoteSession,
    F8ComponentRemoteUser,
    F8ComponentRemoteVersionEntry,
    F8ComponentRemoteVersionList,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentSyncState,
    F8ComponentVisibility,
)

logger = logging.getLogger(__name__)


class ComponentSyncClient:
    _SETTINGS_GROUP: str = "variants/remote_sync/v1"
    _DEFAULT_BASE_URL: str = "https://assetcloud.feel8.fun"
    _USER_AGENT: str = "F8Studio/1.0"
    _SAVED_SESSIONS_KEY: str = "saved_sessions"
    _CURRENT_ACCOUNT_ID_KEY: str = "current_account_id"

    def __init__(self, *, settings: QtCore.QSettings | None = None, catalog_service: ComponentCatalogService | None = None) -> None:
        self._settings: QtCore.QSettings
        self._settings = QtCore.QSettings() if settings is None else settings
        self._catalog_service: ComponentCatalogService
        self._catalog_service = ComponentCatalogService() if catalog_service is None else catalog_service
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

    def remembered_username(self) -> str:
        return self._value_str("username")

    def saved_sessions(self) -> list[F8ComponentRemoteSession]:
        raw = self._value_list(self._SAVED_SESSIONS_KEY)
        if not raw:
            return []
        out: list[F8ComponentRemoteSession] = []
        for item in raw:
            out.append(_remote_session_from_payload(json_object_from_value(item)))
        return out

    def current_account_id(self) -> str:
        return self._value_str(self._CURRENT_ACCOUNT_ID_KEY)

    def current_session(self) -> F8ComponentRemoteSession | None:
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
        stored_cookie = self._value_str("session_cookie")
        if stored_cookie:
            self._access_token = stored_cookie
        return self._access_token

    def current_user(self) -> F8ComponentRemoteUser | None:
        raw = self._value_json_object("user")
        if not raw:
            return None
        try:
            return _remote_user_from_payload(raw)
        except Exception:
            logger.exception("Failed to load saved component cloud user; clearing incompatible settings.")
            self._set_value("user", {})
            return None

    def login(self, *, base_url: str, username: str, password: str, remember: bool) -> F8ComponentRemoteAuth:
        self.set_base_url(base_url)
        _, session_cookie = self._request_json_response(
            "POST",
            "/api/auth/sign-in/username",
            {"username": str(username or ""), "password": str(password or "")},
            authorized=False,
        )
        if not session_cookie:
            raise F8ComponentRemoteAuthError("Component sign-in succeeded but no session cookie was returned.")
        user = self._fetch_current_user(session_cookie)
        auth = F8ComponentRemoteAuth(sessionCookie=session_cookie, user=user)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def refresh_auth(self) -> F8ComponentRemoteAuth:
        session = self.current_session()
        session_cookie = ""
        base_url = self.base_url()
        if session is not None:
            session_cookie = str(session.sessionCookie)
            base_url = str(session.baseUrl)
        else:
            session_cookie = self._value_str("session_cookie")
        if not session_cookie:
            raise F8ComponentRemoteAuthError("No saved cloud session is available.")
        self.set_base_url(base_url)
        user = self._fetch_current_user(session_cookie)
        auth = F8ComponentRemoteAuth(sessionCookie=self._access_token, user=user)
        self._set_auth(auth, base_url=base_url, remember=True)
        return auth

    def logout(self) -> None:
        session = self.current_session()
        try:
            if self.current_access_token():
                _ = self._post_json("/api/auth/sign-out", {}, authorized=True)
        except (F8ComponentRemoteAuthError, F8ComponentRemoteRequestError) as exc:
            logger.warning(
                "Component remote sign-out failed; cleared local session anyway: %s",
                str(exc),
            )
        self._access_token = ""
        if session is not None:
            self.clear_saved_session(session.accountId)
        else:
            self._clear_current_auth_state()

    def list_components(self, *, scope: str, query: str = "", cursor: str = "") -> F8ComponentRemoteListPage:
        params = _list_params_for_scope(scope=scope, query=query, cursor=cursor)
        encoded = parse.urlencode({key: value for key, value in params.items() if value})
        suffix = f"?{encoded}" if encoded else ""
        authorized = str(scope or "").strip() != "community" or bool(self.current_access_token())
        payload = self._request_json("GET", f"/v1/components{suffix}", None, authorized=authorized)
        return _page_from_asset_payload(payload)

    def refresh_scope(self, *, scope: str, query: str = "") -> list[F8ComponentEntry]:
        page = self.refresh_scope_page(scope=scope, query=query, cursor="", append=False)
        return page.entries

    def refresh_scope_page(self, *, scope: str, query: str = "", cursor: str = "", append: bool = False) -> F8ComponentRemoteListPage:
        page = self.list_components(scope=scope, query=query, cursor=cursor)
        current = self._catalog_service.load_remote_entries()
        preserved = [entry for entry in current if not _entry_matches_scope(entry, scope=scope, user=self.current_user())]
        existing_scope_entries = [entry for entry in current if _entry_matches_scope(entry, scope=scope, user=self.current_user())]
        existing_scope_by_id: dict[str, F8ComponentEntry] = {
            str(entry.record.componentId): entry for entry in existing_scope_entries if str(entry.record.componentId).strip()
        }
        refreshed: list[F8ComponentEntry] = []
        for entry in page.entries:
            existing_entry = existing_scope_by_id.get(str(entry.record.componentId))
            if existing_entry is not None:
                entry = _merge_component_entries(existing_entry, entry)
            if entry.syncState == F8ComponentSyncState.local_only:
                entry = copy_remote_entry_as_synced(entry)
            refreshed.append(entry)
        if append:
            merged_scope_entries: dict[str, F8ComponentEntry] = {
                str(entry.record.componentId): entry for entry in existing_scope_entries
            }
            for entry in refreshed:
                merged_scope_entries[str(entry.record.componentId)] = entry
            combined_scope_entries = list(merged_scope_entries.values())
        else:
            combined_scope_entries = refreshed
        self._catalog_service.replace_remote_entries(preserved + combined_scope_entries)
        return page

    def install_component(self, component_id: str) -> F8ComponentEntry:
        return self.hydrate_component(component_id)

    def get_component(self, component_id: str) -> F8ComponentEntry:
        payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}",
            None,
            authorized=bool(self.current_access_token()),
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def get_component_content(self, component_id: str) -> F8ComponentRecord:
        payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}/content",
            None,
            authorized=bool(self.current_access_token()),
        )
        return _component_record_from_content_payload(payload, component_id=component_id)

    def hydrate_component(self, component_id: str) -> F8ComponentEntry:
        detail_entry = self.get_component(component_id)
        record = self.get_component_content(component_id)
        hydrated_entry = _hydrate_component_entry(detail_entry, record)
        return self._catalog_service.install_remote_entry(hydrated_entry)

    def create_component(self, entry: F8ComponentEntry, *, change_summary: str | None = None) -> F8ComponentEntry:
        _require_component_record_for_upload(entry.record)
        payload = self._request_json(
            "POST",
            "/v1/components",
            _asset_write_payload(entry, change_summary=change_summary),
            authorized=True,
        )
        result = _hydrate_component_entry(_entry_from_asset_payload(payload), entry.record)
        return self._catalog_service.install_remote_entry(result)

    def update_component(self, entry: F8ComponentEntry, *, change_summary: str | None = None) -> F8ComponentEntry:
        _require_component_record_for_upload(entry.record)
        component_id = str(entry.record.componentId)
        payload = self._request_json(
            "PUT",
            f"/v1/components/{parse.quote(component_id)}",
            _asset_write_payload(entry, change_summary=change_summary),
            authorized=True,
        )
        result = _hydrate_component_entry(_entry_from_asset_payload(payload), entry.record)
        return self._catalog_service.install_remote_entry(result)

    def delete_component(self, component_id: str) -> None:
        _ = self._request_json(
            "DELETE",
            f"/v1/components/{parse.quote(str(component_id))}",
            None,
            authorized=True,
        )
        _ = self._catalog_service.delete_remote_entry(str(component_id))

    def update_component_visibility(
        self,
        component_id: str,
        *,
        visibility: F8ComponentVisibility,
        revision: str | None,
    ) -> F8ComponentEntry:
        payload = self._request_json(
            "PUT",
            f"/v1/components/{parse.quote(str(component_id))}/visibility",
            {
                "visibility": visibility.value,
                "revision": revision,
            },
            authorized=True,
        )
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def upload_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        _require_component_record_for_upload(entry.record)
        try:
            if entry.remoteRevision:
                return self.update_component(entry)
            return self.create_component(entry)
        except F8ComponentRemoteConflictError as exc:
            _ = self._catalog_service.mark_conflict(str(entry.record.componentId), remote_revision=exc.remote_revision)
            raise
        except Exception as exc:
            recovered_entry = self._recover_uploaded_entry(entry)
            if recovered_entry is not None:
                logger.warning(
                    "Component upload raised after remote write; recovered via follow-up fetch component_id=%s error=%s",
                    str(entry.record.componentId),
                    str(exc),
                )
                return recovered_entry
            raise

    def list_component_versions(self, component_id: str) -> F8ComponentRemoteVersionList:
        payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}/versions",
            None,
            authorized=bool(self.current_access_token()),
        )
        return _remote_version_list_from_payload(payload)

    def get_component_version(self, component_id: str, version_number: int) -> F8ComponentEntry:
        detail_payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}/versions/{int(version_number)}",
            None,
            authorized=bool(self.current_access_token()),
        )
        content_payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}/versions/{int(version_number)}/content",
            None,
            authorized=bool(self.current_access_token()),
        )
        detail_entry = _entry_from_asset_payload(detail_payload)
        record = _component_record_from_content_payload(
            content_payload,
            component_id=component_id,
            version_number=int(version_number),
        )
        return _hydrate_component_entry(detail_entry, record)

    def fork_component(
        self,
        *,
        source_component_id: str,
        forked_entry: F8ComponentEntry,
        visibility: F8ComponentVisibility,
        version_number: int | None = None,
    ) -> F8ComponentEntry:
        source_kind = (
            F8ComponentSourceKind.remote_public
            if visibility == F8ComponentVisibility.public
            else F8ComponentSourceKind.remote_private
        )
        entry_to_create = copy_model(
            forked_entry,
            update={
                "source": source_kind,
                "visibility": visibility,
                "remoteRevision": None,
                "syncState": F8ComponentSyncState.local_only,
                "installed": True,
                "subscribed": False,
            },
        )
        change_summary = f"Forked from {source_component_id}"
        if version_number is not None:
            change_summary = f"{change_summary} v{int(version_number)}"
        return self.create_component(entry_to_create, change_summary=change_summary)

    def subscribe_component(self, component_id: str) -> F8ComponentEntry:
        payload = self._request_json("POST", f"/v1/components/{parse.quote(str(component_id))}/subscribe", {}, authorized=True)
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def unsubscribe_component(self, component_id: str) -> F8ComponentEntry:
        payload = self._request_json("DELETE", f"/v1/components/{parse.quote(str(component_id))}/subscribe", None, authorized=True)
        return self._update_cached_remote_entry(_entry_from_asset_payload(payload))

    def switch_account(self, account_id: str) -> F8ComponentRemoteAuth:
        session = self._saved_session_by_id(account_id)
        if session is None:
            raise F8ComponentRemoteAuthError("Saved account session was not found.")
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        self.set_base_url(session.baseUrl)
        self._set_value("username", str(session.user.username or ""))
        self._access_token = ""
        return self.refresh_auth()

    def clear_saved_session(self, account_id: str) -> None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return
        remaining = [session for session in self.saved_sessions() if session.accountId != normalized_account_id]
        self._set_value(self._SAVED_SESSIONS_KEY, [_remote_session_payload(session) for session in remaining])
        if self.current_account_id() == normalized_account_id:
            self._clear_current_auth_state()

    def clear_all_saved_sessions(self) -> None:
        self._set_value(self._SAVED_SESSIONS_KEY, [])
        self._clear_current_auth_state()

    def _request_json(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
        try:
            return self._request_json_once(method, path, payload, authorized=authorized)
        except F8ComponentRemoteAuthError:
            if not authorized:
                raise
            _ = self.refresh_auth()
            return self._request_json_once(method, path, payload, authorized=True)

    def _fetch_current_user(self, session_cookie: str) -> F8ComponentRemoteUser:
        payload, _ = self._request_json_response("GET", "/v1/me", None, authorized=False, session_cookie_override=session_cookie)
        user = _remote_user_from_payload(payload)
        self._set_value("user", _remote_user_payload(user))
        return user

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
            raise F8ComponentRemoteRequestError("Component remote base URL is not configured.")
        url = f"{base_url}{path}"
        data: bytes | None = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self._USER_AGENT,
        }
        if path.startswith("/api/auth/"):
            headers.update(origin_headers_for_base_url(base_url))
        if payload is not None:
            raw_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(raw_json) > 4096:
                data = zlib.compress(raw_json, level=6, wbits=31)
                headers["Content-Encoding"] = "gzip"
            else:
                data = raw_json

        if authorized:
            session_cookie = self.current_access_token()
            if not session_cookie:
                raise F8ComponentRemoteAuthError("Not logged in.")
            headers["Cookie"] = session_cookie
        elif session_cookie_override:
            headers["Cookie"] = str(session_cookie_override)
        req = request.Request(url=url, data=data, headers=headers, method=method)
        timeout_seconds = _request_timeout_seconds(method=method, path=path)
        try:
            response_context = cast(_HttpResponseContext, request.urlopen(req, timeout=timeout_seconds))
            with response_context as response_like:
                response_data = response_like.read()
                content_encoding = response_like.headers.get("Content-Encoding", "").lower()
                raw_body = decode_http_response_text(response_data, content_encoding=content_encoding)

                session_cookie = _session_cookie_from_headers(response_like.headers)
                if session_cookie:
                    self._access_token = session_cookie
                if not raw_body:
                    return {}, session_cookie
                try:
                    return json_object_loads(raw_body), session_cookie
                except (ValueError, json.JSONDecodeError) as exc:
                    raise F8ComponentRemoteRequestError(
                        f"{method} {path} returned non-JSON response",
                        status_code=response_like.status,
                    ) from exc
        except error.HTTPError as exc:
            try:
                response_bytes = exc.read()
                content_encoding = exc.headers.get("Content-Encoding", "").lower()
                try:
                    body_text = decode_http_response_text(response_bytes, content_encoding=content_encoding)
                except Exception:
                    logger.exception("Failed to decode component cloud error response")
                    body_text = response_bytes.decode("utf-8", errors="replace")
                payload_obj = _try_parse_json_object(body_text)
                message = _error_message(payload_obj) or body_text or f"{method} {path} failed with HTTP {exc.code}"
                if exc.code == 401:
                    raise F8ComponentRemoteAuthError(message) from exc
                if exc.code == 409:
                    remote_revision = None
                    if isinstance(payload_obj.get("remoteRevision"), str):
                        remote_revision = str(payload_obj["remoteRevision"])
                    raise F8ComponentRemoteConflictError(
                        message or "Component update conflict",
                        component_id=_conflict_component_id(payload_obj, path),
                        remote_revision=remote_revision,
                    ) from exc
                raise F8ComponentRemoteRequestError(
                    message or f"{method} {path} failed with HTTP {exc.code}",
                    status_code=exc.code,
                ) from exc
            finally:
                exc.close()
        except (TimeoutError, socket.timeout) as exc:
            raise F8ComponentRemoteRequestError(
                f"{method} {path} timed out after {timeout_seconds}s",
            ) from exc
        except error.URLError as exc:
            raise F8ComponentRemoteRequestError(f"{method} {path} failed: {str(exc.reason or exc)}") from exc

    def _post_json(self, path: str, payload: JsonObject, *, authorized: bool) -> JsonObject:
        return self._request_json("POST", path, payload, authorized=authorized)

    def _recover_uploaded_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry | None:
        component_id = str(entry.record.componentId or "").strip()
        if not component_id:
            return None
        try:
            return self.install_component(component_id)
        except Exception:
            logger.exception("Component upload recovery fetch failed component_id=%s", component_id)
            return None

    def _saved_session_by_id(self, account_id: str) -> F8ComponentRemoteSession | None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return None
        for session in self.saved_sessions():
            if session.accountId == normalized_account_id:
                return session
        return None

    def _set_auth(self, auth: F8ComponentRemoteAuth, *, base_url: str, remember: bool) -> None:
        self._access_token = str(auth.sessionCookie)
        self._set_value("user", _remote_user_payload(auth.user))
        self._set_value("username", str(auth.user.username or ""))
        session_cookie = str(auth.sessionCookie)
        self._set_value("session_cookie", session_cookie)
        session = F8ComponentRemoteSession(
            accountId=_account_id_for(base_url=base_url, user=auth.user),
            baseUrl=str(base_url).strip().rstrip("/"),
            sessionCookie=session_cookie,
            user=auth.user,
            lastUsedAt=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
        )
        self._upsert_saved_session(session)
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        if not remember:
            logger.debug("Component cloud login now persists account sessions for account switching support.")

    def _upsert_saved_session(self, session: F8ComponentRemoteSession) -> None:
        out: list[F8ComponentRemoteSession] = []
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

    def _update_cached_remote_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        current = self._catalog_service.load_remote_entries()
        out: list[F8ComponentEntry] = []
        replaced = False
        updated_entry = entry
        for current_entry in current:
            if str(current_entry.record.componentId) == str(entry.record.componentId):
                merged_entry = _merge_component_entries(current_entry, entry)
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

    def _clear_current_auth_state(self) -> None:
        self._access_token = ""
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, "")
        self._set_value("session_cookie", "")
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
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    error_message = payload.get("error")
    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip()
    return ""


def _conflict_component_id(payload: JsonObject, path: str) -> str:
    component_id = payload.get("componentId")
    if isinstance(component_id, str) and component_id.strip():
        return component_id.strip()
    return str(path.rstrip("/").split("/")[-1])


def _account_id_for(*, base_url: str, user: F8ComponentRemoteUser) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    normalized_username = str(user.username or user.userId).strip()
    return f"{normalized_base_url}::{normalized_username.lower()}"


def _list_params_for_scope(*, scope: str, query: str, cursor: str) -> dict[str, str]:
    params = {"q": str(query or "").strip(), "cursor": str(cursor or "").strip()}
    if scope == "community":
        params["owner"] = "public"
        params["visibility"] = "public"
    elif scope == "mine":
        params["owner"] = "me"
    return params


def _asset_write_payload(entry: F8ComponentEntry, *, change_summary: str | None = None) -> JsonObject:
    payload: JsonObject = {
        "record": _record_payload(entry.record),
        "visibility": None if entry.visibility is None else entry.visibility.value,
    }
    if entry.remoteRevision:
        payload["revision"] = str(entry.remoteRevision)
    if change_summary:
        payload["changeSummary"] = str(change_summary)
    return payload


def _page_from_asset_payload(payload: JsonObject) -> F8ComponentRemoteListPage:
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise F8ComponentRemoteRequestError("Component remote list response is missing entries.")
    raw_entry_items = cast(list[object], raw_entries)
    entries: list[F8ComponentEntry] = []
    for item in raw_entry_items:
        if not isinstance(item, dict):
            continue
        try:
            entry = _entry_from_asset_payload(json_object_from_value(cast(object, item)))
        except Exception:
            logger.exception("Ignoring invalid component entry in list response")
            continue
        if not str(entry.record.componentId).strip():
            logger.warning("Ignoring component list entry with empty componentId")
            continue
        entries.append(entry)
    next_cursor = payload.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        next_cursor = str(next_cursor)
    return F8ComponentRemoteListPage(entries=entries, nextCursor=next_cursor)


def _entry_from_asset_payload(payload: JsonObject) -> F8ComponentEntry:
    record = _component_record_from_asset_payload(payload)
    source = _source_from_asset_payload(payload)
    return F8ComponentEntry(
        record=record,
        source=source,
        visibility=_visibility_from_payload(payload),
        ownerUserId=_payload_optional_str(payload, "ownerUserId"),
        ownerDisplayName=_payload_optional_str(payload, "ownerDisplayName"),
        librarySlug=_payload_optional_str(payload, "librarySlug") or _library_slug_from_payload(payload, source),
        remoteRevision=(
            _payload_optional_str(payload, "revision")
            or _payload_optional_str(payload, "latestRevision")
        ),
        syncState=F8ComponentSyncState.synced,
        downloadedAt=_payload_optional_str(payload, "downloadedAt"),
        installed=_payload_bool(payload, "installed", default=_installed_from_asset_payload(payload, record)),
        hasCachedContent=_payload_bool(payload, "hasCachedContent", default=_component_record_has_full_content(record)),
        subscribed=_payload_bool(payload, "subscribed", default=False),
        remoteVersionNumber=_payload_optional_int(payload, "latestVersionNumber"),
    )


def _component_record_from_content_payload(
    payload: JsonObject,
    *,
    component_id: str,
    version_number: int | None = None,
) -> F8ComponentRecord:
    record_payload = payload.get("record")
    if isinstance(record_payload, dict):
        return validate_as(F8ComponentRecord, json_object_from_value(cast(object, record_payload)))
    if _looks_like_component_record_payload(payload):
        return validate_as(F8ComponentRecord, payload)
    version_suffix = "" if version_number is None else f" v{int(version_number)}"
    raise F8ComponentRemoteRequestError(
        f"Component content payload is missing record for {component_id}{version_suffix}."
    )


def _looks_like_component_record_payload(payload: JsonObject) -> bool:
    if "componentId" not in payload:
        return False
    if "content" not in payload:
        return False
    return True


def _source_from_asset_payload(payload: JsonObject) -> F8ComponentSourceKind:
    visibility = str(payload.get("visibility") or "").strip()
    if visibility == "public":
        return F8ComponentSourceKind.remote_public
    return F8ComponentSourceKind.remote_private


def _installed_from_asset_payload(payload: JsonObject, record: F8ComponentRecord) -> bool:
    del payload
    return _component_record_has_full_content(record)


def _component_record_from_asset_payload(payload: JsonObject) -> F8ComponentRecord:
    record_payload = payload.get("record")
    if isinstance(record_payload, dict):
        return validate_as(F8ComponentRecord, json_object_from_value(cast(object, record_payload)))
    return _summary_component_record_from_payload(payload)


def _summary_component_record_from_payload(payload: JsonObject) -> F8ComponentRecord:
    component_id = _payload_str(payload, "componentId")
    return F8ComponentRecord(
        componentId=component_id,
        name=_payload_str(payload, "name"),
        description=_payload_optional_str(payload, "description") or "",
        tags=_payload_string_list(payload, "tags"),
        schemaVersion=_payload_str(payload, "schemaVersion"),
        content={},
        createdAt=_payload_str(payload, "createdAt"),
        updatedAt=_payload_str(payload, "updatedAt"),
    )


def _component_record_has_full_content(record: F8ComponentRecord) -> bool:
    content = record.content
    layout_value = content.get("layout")
    schema_version_value = content.get("schemaVersion")
    return isinstance(layout_value, dict) and isinstance(schema_version_value, str) and bool(schema_version_value.strip())


def _require_component_record_for_upload(record: F8ComponentRecord) -> None:
    if _component_record_has_full_content(record):
        return
    raise ValueError(
        f"Component {record.componentId} is missing full content and cannot be uploaded. "
        "Load or install the component content before uploading."
    )


def _request_timeout_seconds(*, method: str, path: str) -> int:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    if normalized_method == "GET" and normalized_path.endswith("/content"):
        return 45
    if normalized_method == "GET" and "/versions/" in normalized_path:
        return 30
    return 10


def _merge_component_entries(existing_entry: F8ComponentEntry, incoming_entry: F8ComponentEntry) -> F8ComponentEntry:
    incoming_has_content = component_entry_has_cached_content(incoming_entry)
    existing_has_content = component_entry_has_cached_content(existing_entry)
    if incoming_has_content:
        if existing_has_content and not incoming_entry.installed:
            return copy_model(
                incoming_entry,
                update={
                    "installed": True,
                    "hasCachedContent": True,
                    "downloadedAt": incoming_entry.downloadedAt or existing_entry.downloadedAt,
                },
            )
        return incoming_entry
    if not existing_has_content:
        return incoming_entry
    if str(existing_entry.remoteRevision or "") != str(incoming_entry.remoteRevision or ""):
        return incoming_entry
    merged_record = F8ComponentRecord(
        componentId=str(incoming_entry.record.componentId),
        name=str(incoming_entry.record.name),
        description=str(incoming_entry.record.description),
        tags=list(incoming_entry.record.tags),
        schemaVersion=str(incoming_entry.record.schemaVersion),
        content=existing_entry.record.content,
        createdAt=str(incoming_entry.record.createdAt),
        updatedAt=str(incoming_entry.record.updatedAt),
    )
    return copy_model(
        incoming_entry,
        update={
            "record": merged_record,
            "installed": True,
            "hasCachedContent": True,
            "downloadedAt": incoming_entry.downloadedAt or existing_entry.downloadedAt,
        },
    )


def _hydrate_component_entry(entry: F8ComponentEntry, record: F8ComponentRecord) -> F8ComponentEntry:
    hydrated_record = F8ComponentRecord(
        componentId=str(entry.record.componentId),
        name=str(entry.record.name or record.name),
        description=str(entry.record.description or record.description),
        tags=list(entry.record.tags or record.tags),
        schemaVersion=str(entry.record.schemaVersion or record.schemaVersion),
        content=record.content,
        createdAt=str(entry.record.createdAt or record.createdAt),
        updatedAt=str(entry.record.updatedAt or record.updatedAt),
    )
    return copy_model(
        entry,
        update={
            "record": hydrated_record,
            "installed": True,
            "hasCachedContent": True,
            "downloadedAt": entry.downloadedAt or QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
        },
    )


def _library_slug_from_payload(payload: JsonObject, source: F8ComponentSourceKind) -> str | None:
    if source in {F8ComponentSourceKind.remote_public, F8ComponentSourceKind.remote_official}:
        return "community"
    owner_user_id = payload.get("ownerUserId")
    if isinstance(owner_user_id, str) and owner_user_id.strip():
        return f"user/{owner_user_id.strip()}"
    return None


def _entry_matches_scope(entry: F8ComponentEntry, *, scope: str, user: F8ComponentRemoteUser | None) -> bool:
    if scope == "community":
        return entry.source == F8ComponentSourceKind.remote_public
    if scope == "mine":
        if user is None:
            return False
        return entry.source == F8ComponentSourceKind.remote_private or str(entry.ownerUserId or "") == str(user.userId)
    return False


def copy_remote_entry_as_synced(entry: F8ComponentEntry) -> F8ComponentEntry:
    return copy_model(entry, update={"syncState": F8ComponentSyncState.synced})


def _remote_user_from_payload(payload: JsonObject) -> F8ComponentRemoteUser:
    return validate_as(F8ComponentRemoteUser, payload)


def _remote_user_payload(user: F8ComponentRemoteUser) -> JsonObject:
    return {
        "userId": str(user.userId),
        "displayName": str(user.displayName),
        "username": None if user.username is None else str(user.username),
    }


def _remote_session_from_payload(payload: JsonObject) -> F8ComponentRemoteSession:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise ValueError("Saved component session is missing user.")
    session_cookie = _saved_session_cookie_from_payload(payload)
    if not session_cookie:
        raise ValueError("Saved component session is missing sessionCookie.")
    return F8ComponentRemoteSession(
        accountId=_payload_str(payload, "accountId"),
        baseUrl=_payload_str(payload, "baseUrl"),
        sessionCookie=session_cookie,
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
        lastUsedAt=_payload_str(payload, "lastUsedAt"),
    )


def _remote_session_payload(session: F8ComponentRemoteSession) -> JsonObject:
    return {
        "accountId": str(session.accountId),
        "baseUrl": str(session.baseUrl),
        "sessionCookie": str(session.sessionCookie),
        "user": _remote_user_payload(session.user),
        "lastUsedAt": str(session.lastUsedAt),
    }


def _saved_session_cookie_from_payload(payload: JsonObject) -> str:
    value = payload.get("sessionCookie")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _session_cookie_from_headers(headers: object) -> str:
    values = _header_values(headers, "Set-Cookie")
    if not values:
        return ""
    cookie = http.cookies.SimpleCookie()
    for value in values:
        try:
            cookie.load(value)
        except http.cookies.CookieError:
            logger.warning("Ignoring invalid Set-Cookie header from component cloud")
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


def _remote_version_list_from_payload(payload: JsonObject) -> F8ComponentRemoteVersionList:
    raw_versions = payload.get("versions")
    if not isinstance(raw_versions, list):
        raise F8ComponentRemoteRequestError("Component version response is missing versions.")
    versions: list[F8ComponentRemoteVersionEntry] = []
    for item in cast(list[object], raw_versions):
        if not isinstance(item, dict):
            continue
        version_payload = json_object_from_value(cast(object, item))
        versions.append(
            F8ComponentRemoteVersionEntry(
                componentId=_payload_str(version_payload, "componentId"),
                assetType=_payload_str(version_payload, "assetType"),
                versionNumber=_payload_int(version_payload, "versionNumber"),
                revision=_payload_str(version_payload, "revision"),
                createdAt=_payload_str(version_payload, "createdAt"),
                createdByUserId=_payload_str(version_payload, "createdByUserId"),
                changeSummary=_payload_optional_str(version_payload, "changeSummary"),
            )
        )
    return F8ComponentRemoteVersionList(versions=versions)


def _record_payload(record: F8ComponentRecord) -> JsonObject:
    return {
        "componentId": str(record.componentId),
        "name": str(record.name),
        "description": str(record.description),
        "tags": [str(tag) for tag in list(record.tags or []) if str(tag).strip()],
        "schemaVersion": str(record.schemaVersion),
        "content": record.content,
        "createdAt": str(record.createdAt),
        "updatedAt": str(record.updatedAt),
    }


def _visibility_from_payload(payload: JsonObject) -> F8ComponentVisibility | None:
    raw_visibility = _payload_optional_str(payload, "visibility")
    if raw_visibility is None or not raw_visibility.strip():
        return None
    return F8ComponentVisibility(raw_visibility)


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


def _payload_int(payload: JsonObject, key: str) -> int:
    return int(str(payload[key]))


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
