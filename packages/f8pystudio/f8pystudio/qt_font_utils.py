from __future__ import annotations

import logging

from qtpy import QtGui, QtWidgets

logger = logging.getLogger(__name__)


def normalize_font_point_size(font: QtGui.QFont, *, fallback_point_size: int = 10) -> QtGui.QFont:
    normalized = QtGui.QFont(font)
    if int(normalized.pointSize()) > 0:
        return normalized

    pixel_size = int(normalized.pixelSize())
    if pixel_size > 0:
        point_size = max(1, round(pixel_size * 0.75))
    else:
        point_size = max(1, int(fallback_point_size))

    normalized.setPointSize(point_size)
    return normalized


def normalize_application_font(
    app: QtWidgets.QApplication | None,
    *,
    fallback_point_size: int = 10,
) -> None:
    if app is None:
        return
    try:
        current_font = app.font()
        normalized = normalize_font_point_size(current_font, fallback_point_size=fallback_point_size)
        if int(current_font.pointSize()) <= 0:
            app.setFont(normalized)
            logger.debug(
                "Normalized QApplication font point size: oldPointSize=%s oldPixelSize=%s newPointSize=%s",
                current_font.pointSize(),
                current_font.pixelSize(),
                normalized.pointSize(),
            )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.exception("Failed to normalize QApplication font")
