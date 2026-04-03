from __future__ import annotations

import json
import logging
from typing import Protocol, cast
from urllib import error, parse, request

from qtpy import QtCore

from f8pysdk.msgspec_codec import copy_model, validate_as

from .common import JsonObject, json_object_from_value, json_object_loads
from .component_catalog import ComponentCatalogService
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
        saved = self._value_str("base_url").rstrip("/")
        return saved or self._DEFAULT_BASE_URL

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
        return None

    def current_access_token(self) -> str:
        return self._access_token

    def current_user(self) -> F8ComponentRemoteUser | None:
        raw = self._value_json_object("user")
        if not raw:
            return None
        return _remote_user_from_payload(raw)

    def login(self, *, base_url: str, username: str, password: str, remember: bool) -> F8ComponentRemoteAuth:
        self.set_base_url(base_url)
        payload = self._post_json(
            "/v1/auth/login",
            {"username": str(username or ""), "password": str(password or "")},
            authorized=False,
        )
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=remember)
        return auth

    def refresh_auth(self) -> F8ComponentRemoteAuth:
        session = self.current_session()
        refresh_token = ""
        base_url = self.base_url()
        if session is not None:
            refresh_token = str(session.refreshToken)
            base_url = str(session.baseUrl)
        else:
            refresh_token = self._value_str("refresh_token")
        if not refresh_token:
            raise F8ComponentRemoteAuthError("No refresh token is available.")
        self.set_base_url(base_url)
        payload = self._post_json("/v1/auth/refresh", {"refreshToken": refresh_token}, authorized=False)
        auth = _remote_auth_from_payload(payload)
        self._set_auth(auth, base_url=base_url, remember=True)
        return auth

    def logout(self) -> None:
        session = self.current_session()
        try:
            if self.current_access_token():
                _ = self._post_json("/v1/auth/logout", {}, authorized=True)
        except F8ComponentRemoteRequestError:
            logger.exception("Component remote logout failed")
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
        refreshed: list[F8ComponentEntry] = []
        for entry in page.entries:
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
        payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}",
            None,
            authorized=bool(self.current_access_token()),
        )
        entry = _entry_from_asset_payload(payload)
        return self._catalog_service.install_remote_entry(entry)

    def create_component(self, entry: F8ComponentEntry, *, change_summary: str | None = None) -> F8ComponentEntry:
        payload = self._request_json(
            "POST",
            "/v1/components",
            _asset_write_payload(entry, change_summary=change_summary),
            authorized=True,
        )
        result = _entry_from_asset_payload(payload)
        _ = self._catalog_service.install_remote_entry(result)
        return result

    def update_component(self, entry: F8ComponentEntry, *, change_summary: str | None = None) -> F8ComponentEntry:
        component_id = str(entry.record.componentId)
        payload = self._request_json(
            "PUT",
            f"/v1/components/{parse.quote(component_id)}",
            _asset_write_payload(entry, change_summary=change_summary),
            authorized=True,
        )
        result = _entry_from_asset_payload(payload)
        _ = self._catalog_service.install_remote_entry(result)
        return result

    def upload_entry(self, entry: F8ComponentEntry) -> F8ComponentEntry:
        try:
            if entry.remoteRevision:
                return self.update_component(entry)
            return self.create_component(entry)
        except F8ComponentRemoteConflictError as exc:
            _ = self._catalog_service.mark_conflict(str(entry.record.componentId), remote_revision=exc.remote_revision)
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
        payload = self._request_json(
            "GET",
            f"/v1/components/{parse.quote(str(component_id))}/versions/{int(version_number)}",
            None,
            authorized=bool(self.current_access_token()),
        )
        return _entry_from_asset_payload(payload)

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

    def _request_json_once(self, method: str, path: str, payload: JsonObject | None, *, authorized: bool) -> JsonObject:
        base_url = self.base_url()
        if not base_url:
            raise F8ComponentRemoteRequestError("Component remote base URL is not configured.")
        url = f"{base_url}{path}"
        data: bytes | None = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self._USER_AGENT,
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if authorized:
            access_token = self.current_access_token()
            if not access_token:
                raise F8ComponentRemoteAuthError("Not logged in.")
            headers["Authorization"] = f"Bearer {access_token}"
        req = request.Request(url=url, data=data, headers=headers, method=method)
        try:
            response_context = cast(_HttpResponseContext, request.urlopen(req, timeout=10))
            with response_context as response_like:
                raw_body = response_like.read().decode("utf-8")
                if not raw_body:
                    return {}
                return json_object_loads(raw_body)
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            payload_obj = _try_parse_json_object(body_text)
            message = _error_message(payload_obj) or body_text or str(exc)
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
            raise F8ComponentRemoteRequestError(message or f"HTTP {exc.code}", status_code=exc.code) from exc
        except error.URLError as exc:
            raise F8ComponentRemoteRequestError(str(exc.reason or exc)) from exc

    def _post_json(self, path: str, payload: JsonObject, *, authorized: bool) -> JsonObject:
        return self._request_json("POST", path, payload, authorized=authorized)

    def _saved_session_by_id(self, account_id: str) -> F8ComponentRemoteSession | None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return None
        for session in self.saved_sessions():
            if session.accountId == normalized_account_id:
                return session
        return None

    def _set_auth(self, auth: F8ComponentRemoteAuth, *, base_url: str, remember: bool) -> None:
        self._access_token = str(auth.accessToken)
        self._set_value("user", _remote_user_payload(auth.user))
        self._set_value("username", str(auth.user.username or ""))
        refresh_token = str(auth.refreshToken)
        self._set_value("refresh_token", refresh_token)
        session = F8ComponentRemoteSession(
            accountId=_account_id_for(base_url=base_url, user=auth.user),
            baseUrl=str(base_url).strip().rstrip("/"),
            refreshToken=refresh_token,
            user=auth.user,
            lastUsedAt=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.DateFormat.ISODate),
        )
        self._upsert_saved_session(session)
        self._set_value(self._CURRENT_ACCOUNT_ID_KEY, session.accountId)
        if not remember:
            logger.info("Component cloud login now persists account sessions for account switching support.")

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
        for current_entry in current:
            if str(current_entry.record.componentId) == str(entry.record.componentId):
                merged_entry = copy_model(
                    entry,
                    update={
                        "installed": bool(current_entry.installed or entry.installed),
                        "downloadedAt": entry.downloadedAt or current_entry.downloadedAt,
                    },
                )
                out.append(merged_entry)
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
    asset_id = payload.get("assetId")
    if isinstance(asset_id, str) and asset_id.strip():
        return asset_id.strip()
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
    entries = [_entry_from_asset_payload(json_object_from_value(cast(object, item))) for item in raw_entry_items if isinstance(item, dict)]
    next_cursor = payload.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        next_cursor = str(next_cursor)
    return F8ComponentRemoteListPage(entries=entries, nextCursor=next_cursor)


def _entry_from_asset_payload(payload: JsonObject) -> F8ComponentEntry:
    record_payload = payload.get("record")
    if not isinstance(record_payload, dict):
        raise F8ComponentRemoteRequestError("Component remote payload is missing record.")
    source = _source_from_asset_payload(payload)
    return F8ComponentEntry(
        record=validate_as(F8ComponentRecord, json_object_from_value(cast(object, record_payload))),
        source=source,
        visibility=_visibility_from_payload(payload),
        ownerUserId=_payload_optional_str(payload, "ownerUserId"),
        ownerDisplayName=_payload_optional_str(payload, "ownerDisplayName"),
        librarySlug=_payload_optional_str(payload, "librarySlug") or _library_slug_from_payload(payload, source),
        remoteRevision=(
            _payload_optional_str(payload, "revision")
            or _payload_optional_str(payload, "latestRevision")
            or _payload_optional_str(payload, "remoteRevision")
        ),
        syncState=F8ComponentSyncState.synced,
        downloadedAt=_payload_optional_str(payload, "downloadedAt"),
        installed=_payload_bool(payload, "installed", default=_installed_from_asset_payload(payload)),
        subscribed=_payload_bool(payload, "subscribed", default=False),
    )


def _source_from_asset_payload(payload: JsonObject) -> F8ComponentSourceKind:
    source_kind = str(payload.get("sourceKind") or "").strip()
    if source_kind == F8ComponentSourceKind.remote_official.value:
        return F8ComponentSourceKind.remote_official
    visibility = str(payload.get("visibility") or "").strip()
    if visibility == "public":
        return F8ComponentSourceKind.remote_public
    return F8ComponentSourceKind.remote_private


def _installed_from_asset_payload(payload: JsonObject) -> bool:
    editable = payload.get("editable")
    if isinstance(editable, bool) and editable:
        return True
    subscribed = payload.get("subscribed")
    if isinstance(subscribed, bool):
        return subscribed
    return False


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


def _remote_auth_from_payload(payload: JsonObject) -> F8ComponentRemoteAuth:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise F8ComponentRemoteRequestError("Component auth response is missing user.")
    return F8ComponentRemoteAuth(
        accessToken=_payload_str(payload, "accessToken"),
        refreshToken=_payload_str(payload, "refreshToken"),
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
    )


def _remote_session_from_payload(payload: JsonObject) -> F8ComponentRemoteSession:
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        raise ValueError("Saved component session is missing user.")
    return F8ComponentRemoteSession(
        accountId=_payload_str(payload, "accountId"),
        baseUrl=_payload_str(payload, "baseUrl"),
        refreshToken=_payload_str(payload, "refreshToken"),
        user=_remote_user_from_payload(json_object_from_value(cast(object, user_payload))),
        lastUsedAt=_payload_str(payload, "lastUsedAt"),
    )


def _remote_session_payload(session: F8ComponentRemoteSession) -> JsonObject:
    return {
        "accountId": str(session.accountId),
        "baseUrl": str(session.baseUrl),
        "refreshToken": str(session.refreshToken),
        "user": _remote_user_payload(session.user),
        "lastUsedAt": str(session.lastUsedAt),
    }


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
                assetId=_payload_str(version_payload, "assetId"),
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
        "usageNotes": str(record.usageNotes),
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


def _payload_int(payload: JsonObject, key: str) -> int:
    return int(str(payload[key]))
