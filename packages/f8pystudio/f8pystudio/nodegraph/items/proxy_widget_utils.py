from __future__ import annotations

import logging
import os
from qtpy import QtCore, QtWidgets

logger = logging.getLogger(__name__)


def window_trace_detail_enabled() -> bool:
    raw = str(os.getenv("F8_STUDIO_TRACE_WINDOW_FLASH_DETAIL", "0") or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def trace_widget(widget: QtWidgets.QWidget | None) -> str:
    if widget is None:
        return "None"
    try:
        class_name = str(widget.metaObject().className() or widget.__class__.__name__)
    except (AttributeError, RuntimeError, TypeError):
        class_name = widget.__class__.__name__
    try:
        title = str(widget.windowTitle() or "")
    except (AttributeError, RuntimeError, TypeError):
        title = ""
    try:
        visible = bool(widget.isVisible())
    except (AttributeError, RuntimeError, TypeError):
        visible = False
    try:
        is_window = bool(widget.isWindow())
    except (AttributeError, RuntimeError, TypeError):
        is_window = False
    try:
        parent_widget = widget.parentWidget()
    except (AttributeError, RuntimeError, TypeError):
        parent_widget = None
    parent_class = parent_widget.__class__.__name__ if parent_widget is not None else "None"
    return (
        f"{class_name}(title={title!r}, visible={visible}, isWindow={is_window}, "
        f"parent={parent_class}, object={hex(id(widget))})"
    )


def dispose_detached_proxy_widget(widget: QtWidgets.QWidget | None, *, context: str) -> None:
    """
    Dispose a QWidget detached from QGraphicsProxyWidget without exposing it
    as a transient top-level window.
    """
    if widget is None:
        return
    if window_trace_detail_enabled():
        logger.warning("[WindowTrace] %s dispose detached widget=%s", str(context or "proxy"), trace_widget(widget))
    try:
        widget.hide()
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("Failed to hide detached widget before dispose; context=%s", str(context or ""))
    try:
        widget.setWindowFlag(QtCore.Qt.WindowType.Window, False)
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("Failed to clear Window flag on detached widget; context=%s", str(context or ""))
    try:
        widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("Failed to set WA_DontShowOnScreen on detached widget; context=%s", str(context or ""))
    try:
        widget.deleteLater()
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("Failed to delete detached widget; context=%s", str(context or ""))
