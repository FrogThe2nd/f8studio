from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from ...ui.support.qt_font_utils import normalize_font_point_size


def set_tool_button_point_size(button: QtWidgets.QToolButton, point_size: int) -> None:
    font = normalize_font_point_size(button.font(), fallback_point_size=point_size)
    font.setPointSize(max(1, int(point_size)))
    button.setFont(font)


def usage_pie_icon(*, used_ratio: float, color: QtGui.QColor, size: int = 14) -> QtGui.QIcon:
    ratio = max(0.0, min(1.0, float(used_ratio)))
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    outer = QtCore.QRectF(1.0, 1.0, float(size - 2), float(size - 2))
    center = QtCore.QPointF(outer.center())

    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor("#4a4f57"))
    painter.drawEllipse(outer)

    if ratio > 0.0:
        painter.setBrush(color)
        start_angle = 90 * 16
        span_angle = int(-360 * 16 * ratio)
        painter.drawPie(outer, start_angle, span_angle)

    inner_diameter = max(2.0, outer.width() * 0.46)
    inner = QtCore.QRectF(
        center.x() - inner_diameter / 2.0,
        center.y() - inner_diameter / 2.0,
        inner_diameter,
        inner_diameter,
    )
    painter.setBrush(QtGui.QColor("#1f2328"))
    painter.drawEllipse(inner)

    painter.setPen(QtGui.QPen(QtGui.QColor("#6c7380"), 1.0))
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.drawEllipse(outer)
    painter.end()
    return QtGui.QIcon(pix)


def configure_icon_tool_button(
    button: QtWidgets.QToolButton,
    *,
    icon: QtGui.QIcon,
    tooltip: str,
    accent_color: str,
) -> None:
    button.setIcon(icon)
    button.setIconSize(QtCore.QSize(14, 14))
    button.setAutoRaise(True)
    button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(24, 24)
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton {"
        f" color: {accent_color};"
        " border: none;"
        " border-radius: 6px;"
        " padding: 0;"
        " background: transparent;"
        "}"
        "QToolButton:hover:enabled { background: #313244; }"
        "QToolButton:pressed:enabled { background: #45475a; }"
        "QToolButton:checked { background: #313244; }"
        "QToolButton:disabled { color: #6c7086; }"
    )


def set_status_label_text(label: QtWidgets.QLabel, text: str, *, max_width: int) -> None:
    metrics = label.fontMetrics()
    elided = metrics.elidedText(str(text or ""), QtCore.Qt.TextElideMode.ElideRight, max_width)
    label.setText(elided)
    label.setToolTip(str(text or ""))
