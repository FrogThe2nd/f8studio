from __future__ import annotations

from typing import Protocol, cast

import keyring
from keyring.errors import KeyringError

from .common import json_object_from_value

_ASSET_CLOUD_SESSION_SERVICE_NAME = "feel8.f8pystudio.assetcloud.session"


class AssetCloudCredentialStoreError(RuntimeError):
    pass


class AssetCloudCredentialStore(Protocol):
    def load_session_cookie(self, *, account_id: str) -> str: ...

    def store_session_cookie(self, *, account_id: str, session_cookie: str) -> None: ...

    def delete_session_cookie(self, *, account_id: str) -> None: ...


class KeyringAssetCloudCredentialStore:
    def load_session_cookie(self, *, account_id: str) -> str:
        normalized_account_id = _normalized_account_id(account_id)
        if not normalized_account_id:
            return ""
        try:
            value = keyring.get_password(_ASSET_CLOUD_SESSION_SERVICE_NAME, normalized_account_id)
        except KeyringError as exc:
            raise AssetCloudCredentialStoreError(
                f"Failed to load asset cloud session cookie from keyring for account {normalized_account_id!r}."
            ) from exc
        return str(value or "").strip()

    def store_session_cookie(self, *, account_id: str, session_cookie: str) -> None:
        normalized_account_id = _normalized_account_id(account_id)
        normalized_session_cookie = str(session_cookie or "").strip()
        if not normalized_account_id:
            raise ValueError("account_id must not be empty.")
        if not normalized_session_cookie:
            raise ValueError("session_cookie must not be empty.")
        try:
            keyring.set_password(
                _ASSET_CLOUD_SESSION_SERVICE_NAME,
                normalized_account_id,
                normalized_session_cookie,
            )
        except KeyringError as exc:
            raise AssetCloudCredentialStoreError(
                f"Failed to store asset cloud session cookie in keyring for account {normalized_account_id!r}."
            ) from exc

    def delete_session_cookie(self, *, account_id: str) -> None:
        normalized_account_id = _normalized_account_id(account_id)
        if not normalized_account_id:
            return
        existing_value = self.load_session_cookie(account_id=normalized_account_id)
        if not existing_value:
            return
        try:
            keyring.delete_password(_ASSET_CLOUD_SESSION_SERVICE_NAME, normalized_account_id)
        except KeyringError as exc:
            raise AssetCloudCredentialStoreError(
                f"Failed to delete asset cloud session cookie from keyring for account {normalized_account_id!r}."
            ) from exc


def default_asset_cloud_credential_store() -> AssetCloudCredentialStore:
    return KeyringAssetCloudCredentialStore()


def saved_session_account_ids_from_raw(raw_sessions: list[object]) -> list[str]:
    account_ids: list[str] = []
    for item in raw_sessions:
        if not isinstance(item, dict):
            continue
        payload = json_object_from_value(cast(object, item))
        raw_account_id = payload.get("accountId")
        if isinstance(raw_account_id, str) and raw_account_id.strip():
            account_ids.append(raw_account_id.strip())
    return account_ids


def _normalized_account_id(account_id: str) -> str:
    return str(account_id or "").strip()
