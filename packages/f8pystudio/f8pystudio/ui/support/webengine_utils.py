from __future__ import annotations

import logging
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)
_WEBENGINE_PROFILE_CONFIGURED = False
_WEBENGINE_VIEW_PREWARMED = False
_WEBENGINE_PREWARM_VIEW: QtWidgets.QWidget | None = None


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


def configure_webengine_local_content_access(view: object) -> None:
    try:
        from PySide6 import QtWebEngineCore  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("QtWebEngineCore is unavailable; skipped local content access configuration")
        return

    settings = None
    try:
        settings = view.settings()
    except (AttributeError, RuntimeError, TypeError):
        settings = None
    if settings is None:
        return

    try:
        settings.setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            True,
        )
        settings.setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            True,
        )
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("failed to configure local content access for WebEngine view")


def set_webengine_html(view: object, html: str, *, base_url: str | None = None) -> None:
    normalized_base_url = str(base_url or "").strip()
    if normalized_base_url:
        view.setHtml(str(html), QtCore.QUrl(normalized_base_url))
        return
    view.setHtml(str(html))


def prewarm_webengine_view() -> bool:
    global _WEBENGINE_VIEW_PREWARMED, _WEBENGINE_PREWARM_VIEW
    if _WEBENGINE_VIEW_PREWARMED:
        return True

    configure_default_webengine_profile()
    try:
        from PySide6 import QtWebEngineWidgets  # type: ignore[import-not-found]
    except ImportError:
        logger.exception("failed to import QtWebEngineWidgets for prewarm")
        return False

    try:
        view = QtWebEngineWidgets.QWebEngineView()
        view.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.resize(1, 1)
        view.hide()
        view.setHtml("<html><body></body></html>")
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.processEvents()
        _WEBENGINE_PREWARM_VIEW = view
        _WEBENGINE_VIEW_PREWARMED = True
        return True
    except Exception:
        logger.exception("failed to prewarm QtWebEngine view")
        _WEBENGINE_PREWARM_VIEW = None
        return False


def take_prewarmed_webengine_view(*, parent: QtWidgets.QWidget | None = None) -> QtWidgets.QWidget | None:
    global _WEBENGINE_PREWARM_VIEW
    view = _WEBENGINE_PREWARM_VIEW
    if view is None:
        return None

    _WEBENGINE_PREWARM_VIEW = None
    try:
        if parent is not None:
            view.setParent(parent)
        view.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("failed to attach prewarmed QtWebEngine view")
        try:
            view.deleteLater()
        except (AttributeError, RuntimeError, TypeError):
            pass
        return None
    return view


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
