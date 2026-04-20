from __future__ import annotations

from pathlib import Path

import pytest
from qtpy import QtCore


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
