from __future__ import annotations

import logging
from pathlib import Path

from qtpy import QtCore, QtGui

logger = logging.getLogger(__name__)
_WEBENGINE_PROFILE_CONFIGURED = False


def configure_default_webengine_profile() -> None:
    global _WEBENGINE_PROFILE_CONFIGURED
    if _WEBENGINE_PROFILE_CONFIGURED:
        return
    try:
        from PySide6 import QtWebEngineCore  # type: ignore[import-not-found]
    except ImportError:
        logger.exception("failed to import QtWebEngineCore for cache configuration")
        return

    app_data_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    if not app_data_dir:
        logger.error("Qt AppDataLocation is unavailable; web cache path is not configured")
        return

    cache_dir = Path(app_data_dir) / "webengine_cache"
    storage_dir = Path(app_data_dir) / "webengine_storage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    profile = QtWebEngineCore.QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QtWebEngineCore.QWebEngineProfile.DiskHttpCache)
    profile.setCachePath(str(cache_dir))
    profile.setPersistentStoragePath(str(storage_dir))
    profile.setPersistentCookiesPolicy(QtWebEngineCore.QWebEngineProfile.ForcePersistentCookies)
    _WEBENGINE_PROFILE_CONFIGURED = True


def set_webengine_view_background(view: object, color: str) -> None:
    page = None
    try:
        page = view.page()
    except (AttributeError, RuntimeError, TypeError):
        page = None
    if page is None:
        return

    qcolor = QtGui.QColor(str(color or "").strip() or "#0f0f12")
    if not qcolor.isValid():
        qcolor = QtGui.QColor("#0f0f12")
    try:
        page.setBackgroundColor(qcolor)
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("failed to set WebEngine page background color")


def webengine_termination_status_text(termination_status: object) -> str:
    try:
        status_value = int(termination_status)
    except (TypeError, ValueError):
        return str(termination_status or "unknown")
    if status_value == 0:
        return "normal"
    if status_value == 1:
        return "abnormal"
    if status_value == 2:
        return "crashed"
    if status_value == 3:
        return "killed"
    return f"unknown({status_value})"
