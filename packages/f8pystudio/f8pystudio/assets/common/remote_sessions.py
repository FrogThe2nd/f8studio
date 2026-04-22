from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar

from .common import JsonObject, json_object_from_value


class RemoteSessionLike(Protocol):
    accountId: str
    baseUrl: str
    sessionCookie: str


RemoteSessionT = TypeVar("RemoteSessionT", bound=RemoteSessionLike)


def saved_session_by_id(
    sessions: Iterable[RemoteSessionT],
    *,
    account_id: str,
) -> RemoteSessionT | None:
    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
        return None
    for session in sessions:
        if str(session.accountId) == normalized_account_id:
            return session
    return None


def current_session_base_url_from_raw(
    raw_items: list[object],
    *,
    current_account_id: str,
) -> str:
    normalized_account_id = str(current_account_id or "").strip()
    if not normalized_account_id:
        return ""
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        payload = json_object_from_value(item)
        account_id = str(payload.get("accountId") or "").strip()
        if account_id != normalized_account_id:
            continue
        return str(payload.get("baseUrl") or "").strip().rstrip("/")
    return ""


def upsert_saved_sessions(
    sessions: Iterable[RemoteSessionT],
    *,
    session: RemoteSessionT,
    sort_key: Callable[[RemoteSessionT], tuple[object, ...]],
) -> list[RemoteSessionT]:
    updated: list[RemoteSessionT] = []
    replaced = False
    for current in sessions:
        if str(current.accountId) == str(session.accountId):
            updated.append(session)
            replaced = True
            continue
        updated.append(current)
    if not replaced:
        updated.append(session)
    updated.sort(key=sort_key)
    return updated


def account_id_for_session_cookie(
    *,
    session_cookie: str,
    access_token: str,
    access_token_account_id: str,
    current_session: RemoteSessionLike | None,
    saved_sessions: Iterable[RemoteSessionLike],
) -> str:
    normalized_session_cookie = str(session_cookie or "").strip()
    if not normalized_session_cookie:
        return ""
    if access_token_account_id and str(access_token).strip() == normalized_session_cookie:
        return str(access_token_account_id)
    if current_session is not None and str(current_session.sessionCookie).strip() == normalized_session_cookie:
        return str(current_session.accountId)
    for session in saved_sessions:
        if str(session.sessionCookie).strip() == normalized_session_cookie:
            return str(session.accountId)
    return ""


def session_matches_base_url(session: RemoteSessionLike, *, base_url: str) -> bool:
    return str(session.baseUrl).strip().rstrip("/") == str(base_url or "").strip().rstrip("/")


def remote_session_payload_base(
    *,
    account_id: str,
    base_url: str,
    last_used_at: str,
) -> JsonObject:
    return {
        "accountId": str(account_id),
        "baseUrl": str(base_url).strip().rstrip("/"),
        "lastUsedAt": str(last_used_at),
    }


__all__ = [
    "RemoteSessionLike",
    "account_id_for_session_cookie",
    "current_session_base_url_from_raw",
    "remote_session_payload_base",
    "saved_session_by_id",
    "session_matches_base_url",
    "upsert_saved_sessions",
]
