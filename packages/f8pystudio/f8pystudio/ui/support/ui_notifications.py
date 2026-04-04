from __future__ import annotations

import html
import logging
import math
import sys
from dataclasses import dataclass
from typing import Callable

from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)

_MAX_VISIBLE_TOASTS = 4
_TOAST_SPACING = 10
_TOAST_SCREEN_MARGIN = 18
_TOAST_MIN_WIDTH = 280
_TOAST_MAX_WIDTH = 520
_TOAST_DURATION_MS = 4200
_TOAST_FADE_MS = 180
_TOAST_BREAK_HINTS = ("/", "\\", "_", "-", ".", ":", "=", "?")
_USE_WINDOWS_SAFE_TOASTS = sys.platform.startswith("win")


@dataclass(frozen=True)
class _ToastStyle:
    background: str
    border: str
    accent: str
    title: str
    text: str
    badge_background: str
    badge_foreground: str
    glyph: str
    close_icon: str


_INFO_STYLE = _ToastStyle(
    background="#182B40",
    border="#335B87",
    accent="#58A6FF",
    title="#F5FAFF",
    text="#D7E8FB",
    badge_background="#24486B",
    badge_foreground="#EAF5FF",
    glyph="i",
    close_icon="#CFE6FF",
)
_WARNING_STYLE = _ToastStyle(
    background="#3C2B14",
    border="#8B6521",
    accent="#F2C14E",
    title="#FFF7E6",
    text="#F9E5B4",
    badge_background="#6D5118",
    badge_foreground="#FFF7DF",
    glyph="!",
    close_icon="#FCE8B2",
)
_ERROR_STYLE = _ToastStyle(
    background="#472021",
    border="#A14242",
    accent="#FF7B72",
    title="#FFF5F5",
    text="#FFD8D6",
    badge_background="#7A3132",
    badge_foreground="#FFF1F0",
    glyph="x",
    close_icon="#FFD2CF",
)

_ACTIVE_TOASTS: list["_StudioToast"] = []


def _resolve_parent(parent: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
    if parent is not None:
        return parent.window()
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    focus_widget = app.focusWidget()
    if focus_widget is not None:
        return focus_widget.window()
    active_modal_widget = app.activeModalWidget()
    if active_modal_widget is not None:
        return active_modal_widget.window()
    active_window = app.activeWindow()
    if active_window is not None:
        return active_window
    cursor_screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    visible_top_levels: list[QtWidgets.QWidget] = []
    for widget in app.topLevelWidgets():
        if widget.isVisible():
            visible_top_levels.append(widget.window())
    if cursor_screen is not None:
        for widget in visible_top_levels:
            handle = widget.windowHandle()
            if handle is not None and handle.screen() == cursor_screen:
                return widget
    if visible_top_levels:
        return visible_top_levels[0]
    return None


def _screen_for_parent(parent: QtWidgets.QWidget | None) -> QtGui.QScreen | None:
    if parent is not None:
        handle = parent.windowHandle()
        if handle is not None and handle.screen() is not None:
            return handle.screen()
        screen = parent.screen()
        if screen is not None:
            return screen
    cursor_screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    if cursor_screen is not None:
        return cursor_screen
    return QtGui.QGuiApplication.primaryScreen()


def _rich_text_message(message: str) -> str:
    escaped = html.escape(message)
    for token in _TOAST_BREAK_HINTS:
        escaped = escaped.replace(token, f"{token}<wbr/>")
    escaped = escaped.replace("\n", "<br/>")
    return f"<div style='white-space: pre-wrap;'>{escaped}</div>"


class _ToastContentLabel(QtWidgets.QLabel):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if width <= 0:
            return self.fontMetrics().lineSpacing()
        document = QtGui.QTextDocument()
        document.setDefaultFont(self.font())
        document.setDocumentMargin(0.0)
        document.setHtml(self.text())
        document.setTextWidth(float(width))
        return max(1, math.ceil(document.size().height()) + 2)

    def sizeHint(self) -> QtCore.QSize:
        base_hint = super().sizeHint()
        width = base_hint.width()
        if self.wordWrap():
            width = min(max(base_hint.width(), _TOAST_MIN_WIDTH - 96), _TOAST_MAX_WIDTH - 96)
        return QtCore.QSize(width, self.heightForWidth(width))


class _ToastLevelBadge(QtWidgets.QWidget):
    def __init__(self, style: _ToastStyle, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = style
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFixedSize(24, 24)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(self._style.badge_background))
        painter.drawEllipse(self.rect())

        font = QtGui.QFont(self.font())
        font.setBold(True)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(self._style.badge_foreground))
        painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, self._style.glyph)
        painter.end()


class _StudioToast(QtWidgets.QFrame):
    closed = QtCore.Signal(object)

    def __init__(
        self,
        *,
        anchor: QtWidgets.QWidget | None,
        title: str,
        message: str,
        style: _ToastStyle,
        duration_ms: int = _TOAST_DURATION_MS,
    ) -> None:
        super().__init__(None)
        self._anchor = anchor.window() if anchor is not None else None
        self._style = style
        self._duration_ms = max(0, duration_ms)
        self._fade_animation: QtCore.QPropertyAnimation | None = None
        self._lifetime_animation: QtCore.QVariantAnimation | None = None
        self._is_closing = False
        self._progress_fraction = 1.0
        self._watched_anchor: QtWidgets.QWidget | None = None

        self.setObjectName("studio-toast")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, not _USE_WINDOWS_SAFE_TOASTS)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        window_flags = (
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        if _USE_WINDOWS_SAFE_TOASTS:
            window_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(window_flags)
        self.setWindowOpacity(1.0 if _USE_WINDOWS_SAFE_TOASTS else 0.0)
        self.setStyleSheet(
            """
            QLabel#studio-toast-title {
                color: %(title)s;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#studio-toast-message {
                color: %(text)s;
                font-size: 12px;
            }
            QToolButton#studio-toast-close {
                background: transparent;
                border: none;
                color: %(close_icon)s;
                padding: 0px;
            }
            QToolButton#studio-toast-close:hover {
                background: rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            """
            % {
                "title": style.title,
                "text": style.text,
                "close_icon": style.close_icon,
            }
        )

        if not _USE_WINDOWS_SAFE_TOASTS:
            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 10)
            shadow.setColor(QtGui.QColor(0, 0, 0, 110))
            self.setGraphicsEffect(shadow)

        self._level_badge = _ToastLevelBadge(style, self)
        self._level_badge.setObjectName("studio-toast-badge")

        self._title_label = QtWidgets.QLabel(title, self)
        self._title_label.setObjectName("studio-toast-title")
        self._title_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)

        self._message_label = _ToastContentLabel(self)
        self._message_label.setObjectName("studio-toast-message")
        self._message_label.setText(_rich_text_message(message))

        self._close_button = QtWidgets.QToolButton(self)
        self._close_button.setObjectName("studio-toast-close")
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._close_button.setFixedSize(22, 22)
        self._close_button.setIconSize(QtCore.QSize(14, 14))
        self._close_button.setIcon(self._close_icon())
        self._close_button.clicked.connect(self.close_animated)

        body_layout = QtWidgets.QVBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)
        body_layout.addWidget(self._title_label)
        body_layout.addWidget(self._message_label)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._level_badge, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(body_layout, 1)
        layout.addWidget(self._close_button, 0, QtCore.Qt.AlignmentFlag.AlignTop)

    def _close_icon(self) -> QtGui.QIcon:
        style = QtWidgets.QApplication.style()
        if style is None:
            return QtGui.QIcon()
        icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarCloseButton)
        pixmap = icon.pixmap(14, 14)
        if pixmap.isNull():
            return icon
        tinted = QtGui.QPixmap(pixmap.size())
        tinted.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QtGui.QColor(self._style.close_icon))
        painter.end()
        return QtGui.QIcon(tinted)

    def show_with_animation(self) -> None:
        self._is_closing = False
        self._progress_fraction = 1.0
        self._apply_width_constraints()
        self._reposition()
        self._install_anchor_filter()
        self.show()
        self.raise_()
        if not _USE_WINDOWS_SAFE_TOASTS:
            self._animate_opacity(start=0.0, end=1.0)
        if self._duration_ms > 0:
            self._start_lifetime_animation()

    def close_animated(self) -> None:
        if not self.isVisible() or self._is_closing:
            return
        self._is_closing = True
        self._stop_lifetime_animation()
        if _USE_WINDOWS_SAFE_TOASTS:
            self.close()
            return
        self._animate_opacity(start=self.windowOpacity(), end=0.0, on_finished=self.close)

    def _animate_opacity(
        self,
        *,
        start: float,
        end: float,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        if self._fade_animation is not None and self._fade_animation.state() == QtCore.QAbstractAnimation.State.Running:
            self._fade_animation.stop()
        animation = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(_TOAST_FADE_MS)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutQuad)
        if on_finished is not None:
            animation.finished.connect(on_finished)
        animation.start()
        self._fade_animation = animation

    def _start_lifetime_animation(self) -> None:
        self._stop_lifetime_animation()
        animation = QtCore.QVariantAnimation(self)
        animation.setDuration(self._duration_ms)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.Linear)
        animation.valueChanged.connect(self._handle_progress_changed)
        animation.finished.connect(self._handle_lifetime_finished)
        animation.start()
        self._lifetime_animation = animation

    def _stop_lifetime_animation(self) -> None:
        if self._lifetime_animation is None:
            return
        if self._lifetime_animation.state() == QtCore.QAbstractAnimation.State.Running:
            self._lifetime_animation.stop()
        self._lifetime_animation = None

    def _handle_progress_changed(self, value: object) -> None:
        try:
            progress = float(value)
        except (TypeError, ValueError):
            progress = 0.0
        self._progress_fraction = max(0.0, min(1.0, progress))
        self.update()

    def _handle_lifetime_finished(self) -> None:
        self._progress_fraction = 0.0
        self.update()
        if not self._is_closing:
            self.close_animated()

    def _apply_width_constraints(self) -> None:
        target_width = self._target_width()
        self.setFixedWidth(target_width)
        self.layout().activate()
        self.adjustSize()
        self.resize(self.sizeHint().expandedTo(self.minimumSizeHint()))

    def _target_width(self) -> int:
        screen = _screen_for_parent(self._anchor)
        if self._anchor is not None:
            anchor_width = max(1, self._anchor.frameGeometry().width())
            usable_width = anchor_width - (_TOAST_SCREEN_MARGIN * 2)
            return max(_TOAST_MIN_WIDTH, min(_TOAST_MAX_WIDTH, usable_width))
        if screen is None:
            return 420
        available_width = max(1, screen.availableGeometry().width())
        return max(_TOAST_MIN_WIDTH, min(_TOAST_MAX_WIDTH, int(available_width * 0.38)))

    def _anchor_key(self) -> int | None:
        return None if self._anchor is None else id(self._anchor)

    def _stack_offset(self) -> int:
        offset = 0
        for toast in _ACTIVE_TOASTS:
            if toast._anchor_key() != self._anchor_key() or not toast.isVisible():
                continue
            if toast is self:
                break
            offset += toast.height() + _TOAST_SPACING
        return offset

    def _reposition(self) -> None:
        geometry = self._target_geometry()
        x = geometry.right() - self.width() - _TOAST_SCREEN_MARGIN
        y = geometry.bottom() - self.height() - _TOAST_SCREEN_MARGIN - self._stack_offset()
        min_x = geometry.left() + _TOAST_SCREEN_MARGIN
        min_y = geometry.top() + _TOAST_SCREEN_MARGIN
        self.move(max(min_x, x), max(min_y, y))

    def _target_geometry(self) -> QtCore.QRect:
        if self._anchor is not None:
            return self._anchor.frameGeometry()
        screen = _screen_for_parent(self._anchor)
        if screen is None:
            return QtCore.QRect(0, 0, 1280, 720)
        return screen.availableGeometry()

    def _install_anchor_filter(self) -> None:
        if self._anchor is None or self._watched_anchor is self._anchor:
            return
        self._anchor.installEventFilter(self)
        self._watched_anchor = self._anchor

    def _remove_anchor_filter(self) -> None:
        if self._watched_anchor is None:
            return
        try:
            self._watched_anchor.removeEventFilter(self)
        except RuntimeError:
            pass
        self._watched_anchor = None

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._anchor and event.type() in {
            QtCore.QEvent.Type.Move,
            QtCore.QEvent.Type.Resize,
            QtCore.QEvent.Type.Show,
            QtCore.QEvent.Type.WindowStateChange,
        }:
            self._apply_width_constraints()
            self._reposition()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._stop_lifetime_animation()
        self._remove_anchor_filter()
        if self in _ACTIVE_TOASTS:
            _ACTIVE_TOASTS.remove(self)
        for toast in list(_ACTIVE_TOASTS):
            if toast._anchor_key() == self._anchor_key():
                toast._reposition()
        self.closed.emit(self)
        super().closeEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QtGui.QColor(self._style.background))
        painter.setPen(QtGui.QPen(QtGui.QColor(self._style.border), 1))
        painter.drawRect(rect)
        painter.fillRect(QtCore.QRect(rect.left(), rect.top(), 5, rect.height() + 1), QtGui.QColor(self._style.accent))
        if self._duration_ms > 0:
            progress_height = 2
            progress_top = rect.bottom() - progress_height + 1
            track_color = QtGui.QColor(self._style.border)
            track_color.setAlpha(120)
            painter.fillRect(
                QtCore.QRect(rect.left() + 1, progress_top, max(0, rect.width() - 1), progress_height),
                track_color,
            )
            fill_width = max(0, int((rect.width() - 1) * self._progress_fraction))
            if fill_width > 0:
                painter.fillRect(
                    QtCore.QRect(rect.left() + 1, progress_top, fill_width, progress_height),
                    QtGui.QColor(self._style.accent),
                )
        painter.end()
        super().paintEvent(event)


def _show_toast(
    *,
    parent: QtWidgets.QWidget | None,
    title: str,
    message: str,
    style: _ToastStyle,
    fallback: Callable[[QtWidgets.QWidget | None, str, str], None],
) -> None:
    target_parent = _resolve_parent(parent)
    title_text = str(title or "").strip()
    message_text = str(message or "").strip()
    if not title_text and not message_text:
        return
    try:
        anchor = target_parent if target_parent is not None else parent
        toast = _StudioToast(anchor=anchor, title=title_text, message=message_text, style=style)
        same_anchor_toasts = [
            active_toast
            for active_toast in _ACTIVE_TOASTS
            if active_toast._anchor_key() == toast._anchor_key() and active_toast.isVisible()
        ]
        if len(same_anchor_toasts) >= _MAX_VISIBLE_TOASTS:
            same_anchor_toasts[0].close_animated()
        _ACTIVE_TOASTS.append(toast)
        toast.show_with_animation()
    except Exception:
        logger.exception("Failed to show toast notification")
        fallback(target_parent, title_text, message_text)


def show_info(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        style=_INFO_STYLE,
        fallback=QtWidgets.QMessageBox.information,
    )


def show_warning(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        style=_WARNING_STYLE,
        fallback=QtWidgets.QMessageBox.warning,
    )


def show_error(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        style=_ERROR_STYLE,
        fallback=QtWidgets.QMessageBox.critical,
    )
