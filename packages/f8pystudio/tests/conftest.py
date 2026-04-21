from __future__ import annotations

from pathlib import Path

import pytest
from qtpy import QtCore


class _MemoryAssetCloudCredentialStore:
    def __init__(self) -> None:
        self._session_cookies_by_account_id: dict[str, str] = {}

    def load_session_cookie(self, *, account_id: str) -> str:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return ""
        return self._session_cookies_by_account_id.get(normalized_account_id, "")

    def store_session_cookie(self, *, account_id: str, session_cookie: str) -> None:
        normalized_account_id = str(account_id or "").strip()
        normalized_session_cookie = str(session_cookie or "").strip()
        if not normalized_account_id:
            raise ValueError("account_id must not be empty.")
        if not normalized_session_cookie:
            raise ValueError("session_cookie must not be empty.")
        self._session_cookies_by_account_id[normalized_account_id] = normalized_session_cookie

    def delete_session_cookie(self, *, account_id: str) -> None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return
        self._session_cookies_by_account_id.pop(normalized_account_id, None)


@pytest.fixture(autouse=True)
def isolate_user_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sandbox_root = tmp_path / "user-home"
    studio_root = sandbox_root / ".f8" / "studio"
    config_root = sandbox_root / ".config"
    data_root = sandbox_root / ".local" / "share"
    cache_root = sandbox_root / ".cache"

    studio_root.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(sandbox_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, str(config_root))
    QtCore.QSettings.setPath(QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope, str(config_root))

    monkeypatch.setattr("f8pystudio.assets.db.asset_db.assets_db_path", lambda: studio_root / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.variants.variant_catalog.catalog_dir", lambda: studio_root)
    monkeypatch.setattr("f8pystudio.nodegraph.session.last_session_path", lambda: studio_root / "lastSession.json")


@pytest.fixture(autouse=True)
def isolate_asset_cloud_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_store = _MemoryAssetCloudCredentialStore()
    monkeypatch.setattr(
        "f8pystudio.assets.variants.variant_sync.default_asset_cloud_credential_store",
        lambda: credential_store,
    )
    monkeypatch.setattr(
        "f8pystudio.assets.components.component_sync.default_asset_cloud_credential_store",
        lambda: credential_store,
    )
