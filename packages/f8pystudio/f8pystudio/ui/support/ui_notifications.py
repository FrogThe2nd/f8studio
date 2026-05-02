from __future__ import annotations

import html
import logging
import math
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from qtpy import QtCore, QtGui, QtWidgets

from .qt_lifecycle import qt_object_is_valid, qt_runtime_error_is_object_deleted

logger = logging.getLogger(__name__)

_MAX_VISIBLE_TOASTS = 4
_TOAST_SPACING = 10
_TOAST_SCREEN_MARGIN = 18
_TOAST_MIN_WIDTH = 280
_TOAST_MAX_WIDTH = 520
_TOAST_DURATION_MS = 4200
_TOAST_FADE_MS = 180
_TOAST_BREAK_HINTS = ("/", "\\", "_", "-", ".", ":", "=", "?")
_MAX_VISIBLE_STICKY_DETAIL_TOASTS = 3
_ROLLUP_MAX_ITEMS = 50


class _ToastSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


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


@dataclass(frozen=True)
class _ToastPolicy:
    severity: _ToastSeverity
    style: _ToastStyle
    duration_ms: int
    sticky: bool
    copy_enabled: bool


@dataclass(frozen=True)
class _FoldedToastSummary:
    severity: _ToastSeverity
    title: str
    message: str
    repeat_count: int
    created_at_text: str

    def copy_text(self) -> str:
        lines = [
            f"Severity: {self.severity.value}",
            f"Title: {self.title}",
            f"Created: {self.created_at_text}",
        ]
        if self.repeat_count > 1:
            lines.append(f"Repeat count: {self.repeat_count}")
        lines.extend(["Message:", self.message])
        return "\n".join(lines).strip()


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

_INFO_POLICY = _ToastPolicy(
    severity=_ToastSeverity.INFO,
    style=_INFO_STYLE,
    duration_ms=_TOAST_DURATION_MS,
    sticky=False,
    copy_enabled=False,
)
_WARNING_POLICY = _ToastPolicy(
    severity=_ToastSeverity.WARNING,
    style=_WARNING_STYLE,
    duration_ms=0,
    sticky=True,
    copy_enabled=True,
)
_ERROR_POLICY = _ToastPolicy(
    severity=_ToastSeverity.ERROR,
    style=_ERROR_STYLE,
    duration_ms=0,
    sticky=True,
    copy_enabled=True,
)

_ACTIVE_TOASTS: list["_StudioToast"] = []


def _use_safe_toast_window_mode() -> bool:
    if sys.platform.startswith("win"):
        return True
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    platform_name = str(app.platformName() or "").strip().lower()
    return platform_name in {"offscreen", "minimal"}


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


def _modal_dialog_fallback_parent(parent: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        active_modal_widget = app.activeModalWidget()
        if isinstance(active_modal_widget, QtWidgets.QDialog) and active_modal_widget.isVisible():
            return active_modal_widget
    if isinstance(parent, QtWidgets.QDialog) and parent.isModal():
        return parent
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

    def set_style(self, style: _ToastStyle) -> None:
        self._style = style
        self.update()

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
        severity: _ToastSeverity = _ToastSeverity.INFO,
        sticky: bool = False,
        copy_enabled: bool = False,
        copy_text_override: str = "",
        is_rollup: bool = False,
        dedupe_key: str = "",
        repeat_count: int = 1,
    ) -> None:
        super().__init__(None)
        self._anchor = anchor.window() if anchor is not None else None
        self._anchor_stack_key = None if self._anchor is None else id(self._anchor)
        self._style = style
        self._severity = severity
        self._title_text = str(title or "").strip()
        self._message_text = str(message or "").strip()
        self._duration_ms = max(0, duration_ms)
        self._sticky = bool(sticky)
        self._copy_enabled = bool(copy_enabled)
        self._copy_text_override = str(copy_text_override or "")
        self._is_rollup = bool(is_rollup)
        self._dedupe_key = str(dedupe_key or "")
        self._repeat_count = max(1, int(repeat_count))
        self._created_at = QtCore.QDateTime.currentDateTime()
        self._folded_summaries: list[_FoldedToastSummary] = []
        self._folded_omitted_count = 0
        self._safe_window_mode = _use_safe_toast_window_mode()
        self._fade_animation: QtCore.QPropertyAnimation | None = None
        self._lifetime_animation: QtCore.QVariantAnimation | None = None
        self._is_closing = False
        self._progress_fraction = 1.0
        self._watched_anchor: QtWidgets.QWidget | None = None
        self._copy_button: QtWidgets.QToolButton | None = None
        self._logs_button: QtWidgets.QToolButton | None = None

        self.setObjectName("studio-toast")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, not self._safe_window_mode)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        window_flags = (
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        if self._safe_window_mode:
            window_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(window_flags)
        self.setWindowOpacity(1.0 if self._safe_window_mode else 0.0)
        self._apply_style_sheet()

        if not self._safe_window_mode:
            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 10)
            shadow.setColor(QtGui.QColor(0, 0, 0, 110))
            self.setGraphicsEffect(shadow)

        self._level_badge = _ToastLevelBadge(style, self)
        self._level_badge.setObjectName("studio-toast-badge")

        self._title_label = QtWidgets.QLabel(self._display_title(), self)
        self._title_label.setObjectName("studio-toast-title")
        self._title_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)

        self._message_label = _ToastContentLabel(self)
        self._message_label.setObjectName("studio-toast-message")
        self._message_label.setText(_rich_text_message(self._message_text))

        self._close_button = QtWidgets.QToolButton(self)
        self._close_button.setObjectName("studio-toast-close")
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._close_button.setFixedSize(22, 22)
        self._close_button.setIconSize(QtCore.QSize(14, 14))
        self._close_button.setIcon(self._close_icon())
        self._close_button.setToolTip("Acknowledge and close")
        self._close_button.clicked.connect(self.close_animated)

        actions_widget = QtWidgets.QWidget(self)
        actions_widget.setObjectName("studio-toast-actions")
        actions_layout = QtWidgets.QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 2, 0, 0)
        actions_layout.setSpacing(6)
        actions_layout.addStretch(1)
        if self._copy_enabled:
            self._copy_button = QtWidgets.QToolButton(actions_widget)
            self._copy_button.setObjectName("studio-toast-copy")
            self._copy_button.setAutoRaise(True)
            self._copy_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self._copy_button.setText("Copy")
            self._copy_button.setToolTip("Copy notification details")
            self._copy_button.clicked.connect(self._copy_to_clipboard)
            actions_layout.addWidget(self._copy_button)
        if self._severity is not _ToastSeverity.INFO:
            self._logs_button = QtWidgets.QToolButton(actions_widget)
            self._logs_button.setObjectName("studio-toast-logs")
            self._logs_button.setAutoRaise(True)
            self._logs_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self._logs_button.setText("Logs")
            self._logs_button.setToolTip("Open service logs")
            self._logs_button.clicked.connect(self._open_service_logs)
            actions_layout.addWidget(self._logs_button)
        self._sync_logs_button_visibility()
        actions_widget.setVisible(self._copy_button is not None or self._logs_button is not None)
        self._actions_widget = actions_widget

        body_layout = QtWidgets.QVBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)
        body_layout.addWidget(self._title_label)
        body_layout.addWidget(self._message_label)
        body_layout.addWidget(actions_widget)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._level_badge, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(body_layout, 1)
        layout.addWidget(self._close_button, 0, QtCore.Qt.AlignmentFlag.AlignTop)

    def _apply_style_sheet(self) -> None:
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
            QToolButton#studio-toast-copy {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid %(border)s;
                color: %(title)s;
                padding: 2px 7px;
            }
            QToolButton#studio-toast-copy:hover {
                background: rgba(255, 255, 255, 0.14);
            }
            QToolButton#studio-toast-logs {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid %(border)s;
                color: %(title)s;
                padding: 2px 7px;
            }
            QToolButton#studio-toast-logs:hover {
                background: rgba(255, 255, 255, 0.14);
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
                "title": self._style.title,
                "text": self._style.text,
                "border": self._style.border,
                "close_icon": self._style.close_icon,
            }
        )

    def _display_title(self) -> str:
        if self._repeat_count <= 1:
            return self._title_text
        return f"{self._title_text} x{self._repeat_count}"

    def _created_at_text(self) -> str:
        return self._created_at.toString(QtCore.Qt.DateFormat.ISODate)

    def _notification_copy_text(self) -> str:
        override = str(self._copy_text_override or "").strip()
        if override:
            return override
        lines = [
            f"Severity: {self._severity.value}",
            f"Title: {self._title_text}",
            f"Created: {self._created_at_text()}",
        ]
        if self._repeat_count > 1:
            lines.append(f"Repeat count: {self._repeat_count}")
        lines.extend(["Message:", self._message_text])
        return "\n".join(lines).strip()

    def _copy_to_clipboard(self) -> None:
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._notification_copy_text())

    def _service_logs_dock(self) -> QtWidgets.QDockWidget | None:
        anchor = self._live_anchor()
        if anchor is None:
            return None
        dock = anchor.findChild(QtWidgets.QDockWidget, "ServiceLogsDock")
        if dock is None:
            return None
        return dock

    def _sync_logs_button_visibility(self) -> None:
        button = self._logs_button
        if button is None:
            return
        button.setVisible(self._service_logs_dock() is not None)

    def _open_service_logs(self) -> None:
        dock = self._service_logs_dock()
        if dock is None:
            return
        dock.setVisible(True)
        dock.raise_()
        parent_window = dock.window()
        if parent_window is not None:
            parent_window.raise_()

    def apply_rollup_policy(self, policy: _ToastPolicy) -> None:
        if self._severity is policy.severity and self._style == policy.style:
            return
        self._severity = policy.severity
        self._style = policy.style
        self._duration_ms = max(0, policy.duration_ms)
        self._sticky = bool(policy.sticky)
        self._copy_enabled = bool(policy.copy_enabled)
        self._level_badge.set_style(policy.style)
        self._close_button.setIcon(self._close_icon())
        self._apply_style_sheet()
        self.update()

    def increment_repeat(self) -> None:
        self._repeat_count += 1
        self._title_label.setText(self._display_title())
        self._apply_width_constraints()
        self._reposition()

    def set_content(
        self,
        *,
        title: str,
        message: str,
        repeat_count: int = 1,
        copy_text_override: str = "",
    ) -> None:
        self._title_text = str(title or "").strip()
        self._message_text = str(message or "").strip()
        self._repeat_count = max(1, int(repeat_count))
        self._copy_text_override = str(copy_text_override or "")
        self._title_label.setText(self._display_title())
        self._message_label.setText(_rich_text_message(self._message_text))
        self._apply_width_constraints()
        self._reposition()

    def folded_summary(self) -> _FoldedToastSummary:
        return _FoldedToastSummary(
            severity=self._severity,
            title=self._title_text,
            message=self._message_text,
            repeat_count=self._repeat_count,
            created_at_text=self._created_at_text(),
        )

    def add_folded_summary(self, summary: _FoldedToastSummary) -> None:
        self._folded_summaries.append(summary)
        if len(self._folded_summaries) > _ROLLUP_MAX_ITEMS:
            self._folded_summaries = self._folded_summaries[-_ROLLUP_MAX_ITEMS:]
            self._folded_omitted_count += 1
        self._refresh_rollup_text()

    def _refresh_rollup_text(self) -> None:
        visible_count = len(self._folded_summaries)
        total_count = visible_count + self._folded_omitted_count
        self._title_text = f"More notifications ({total_count})"
        noun = "notification" if total_count == 1 else "notifications"
        self._message_text = f"{total_count} warning/error {noun} folded. Copy for details."
        details: list[str] = []
        if self._folded_omitted_count > 0:
            details.append(f"Earlier folded notifications omitted: {self._folded_omitted_count}")
        for index, folded in enumerate(self._folded_summaries, start=1):
            details.append(f"--- Folded notification {index} ---")
            details.append(folded.copy_text())
        self._copy_text_override = "\n".join(details).strip()
        self._title_label.setText(self._display_title())
        self._message_label.setText(_rich_text_message(self._message_text))
        self._apply_width_constraints()
        self._reposition()

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
        self._sync_logs_button_visibility()
        self._apply_width_constraints()
        self._reposition()
        self._install_anchor_filter()
        self.show()
        self.raise_()
        if not self._safe_window_mode:
            self._animate_opacity(start=0.0, end=1.0)
        if self._duration_ms > 0:
            self._start_lifetime_animation()

    def close_animated(self) -> None:
        if not self.isVisible() or self._is_closing:
            return
        self._is_closing = True
        self._stop_lifetime_animation()
        if self._safe_window_mode:
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

    def _stop_fade_animation(self) -> None:
        if self._fade_animation is None:
            return
        if self._fade_animation.state() == QtCore.QAbstractAnimation.State.Running:
            self._fade_animation.stop()
        self._fade_animation = None

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
        anchor = self._live_anchor()
        screen = _screen_for_parent(anchor)
        if anchor is not None:
            anchor_width = max(1, anchor.frameGeometry().width())
            usable_width = anchor_width - (_TOAST_SCREEN_MARGIN * 2)
            return max(_TOAST_MIN_WIDTH, min(_TOAST_MAX_WIDTH, usable_width))
        if screen is None:
            return 420
        available_width = max(1, screen.availableGeometry().width())
        return max(_TOAST_MIN_WIDTH, min(_TOAST_MAX_WIDTH, int(available_width * 0.38)))

    def _anchor_key(self) -> int | None:
        return self._anchor_stack_key

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
        anchor = self._live_anchor()
        if anchor is not None:
            return anchor.frameGeometry()
        screen = _screen_for_parent(anchor)
        if screen is None:
            return QtCore.QRect(0, 0, 1280, 720)
        return screen.availableGeometry()

    def _install_anchor_filter(self) -> None:
        anchor = self._live_anchor()
        if anchor is None or self._watched_anchor is anchor:
            return
        anchor.installEventFilter(self)
        self._watched_anchor = anchor

    def _remove_anchor_filter(self) -> None:
        if self._watched_anchor is None:
            return
        try:
            self._watched_anchor.removeEventFilter(self)
        except RuntimeError as exc:
            if not qt_runtime_error_is_object_deleted(exc):
                raise
        self._watched_anchor = None

    def _live_anchor(self) -> QtWidgets.QWidget | None:
        anchor = self._anchor
        if qt_object_is_valid(anchor):
            return anchor
        self._anchor = None
        self._remove_anchor_filter()
        return None

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
        self._stop_fade_animation()
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


def _anchor_stack_key(anchor: QtWidgets.QWidget | None) -> int | None:
    anchor_window = anchor.window() if anchor is not None else None
    if anchor_window is None:
        return None
    return id(anchor_window)


def _visible_toasts_for_anchor(anchor_key: int | None) -> list[_StudioToast]:
    return [
        toast
        for toast in _ACTIVE_TOASTS
        if toast._anchor_key() == anchor_key and toast.isVisible()
    ]


def _visible_info_toasts_for_anchor(anchor_key: int | None) -> list[_StudioToast]:
    return [
        toast
        for toast in _visible_toasts_for_anchor(anchor_key)
        if toast._severity is _ToastSeverity.INFO
    ]


def _visible_sticky_detail_toasts_for_anchor(anchor_key: int | None) -> list[_StudioToast]:
    return [
        toast
        for toast in _visible_toasts_for_anchor(anchor_key)
        if toast._sticky and not toast._is_rollup and toast._severity is not _ToastSeverity.INFO
    ]


def _visible_rollup_toast_for_anchor(anchor_key: int | None) -> _StudioToast | None:
    for toast in _visible_toasts_for_anchor(anchor_key):
        if toast._is_rollup:
            return toast
    return None


def _matching_sticky_detail_toast(
    *,
    anchor_key: int | None,
    severity: _ToastSeverity,
    title: str,
    message: str,
) -> _StudioToast | None:
    for toast in _visible_sticky_detail_toasts_for_anchor(anchor_key):
        if toast._severity is not severity:
            continue
        if toast._title_text != title:
            continue
        if toast._message_text != message:
            continue
        return toast
    return None


def _matching_keyed_toast(
    *,
    anchor_key: int | None,
    dedupe_key: str,
) -> _StudioToast | None:
    key = str(dedupe_key or "").strip()
    if not key:
        return None
    for toast in _visible_toasts_for_anchor(anchor_key):
        if toast._dedupe_key == key:
            return toast
    return None


def _rollup_policy_for_summary(summary: _FoldedToastSummary) -> _ToastPolicy:
    if summary.severity is _ToastSeverity.ERROR:
        return _ERROR_POLICY
    return _WARNING_POLICY


def _append_to_rollup(
    *,
    anchor: QtWidgets.QWidget | None,
    anchor_key: int | None,
    summary: _FoldedToastSummary,
) -> None:
    policy = _rollup_policy_for_summary(summary)
    rollup = _visible_rollup_toast_for_anchor(anchor_key)
    if rollup is None:
        rollup = _StudioToast(
            anchor=anchor,
            title="More notifications",
            message="",
            style=policy.style,
            duration_ms=policy.duration_ms,
            severity=policy.severity,
            sticky=policy.sticky,
            copy_enabled=policy.copy_enabled,
            is_rollup=True,
        )
        _ACTIVE_TOASTS.append(rollup)
        rollup.add_folded_summary(summary)
        rollup.show_with_animation()
        return
    if summary.severity is _ToastSeverity.ERROR and rollup._severity is not _ToastSeverity.ERROR:
        rollup.apply_rollup_policy(_ERROR_POLICY)
    rollup.add_folded_summary(summary)
    rollup.raise_()
    for toast in _visible_toasts_for_anchor(anchor_key):
        toast._reposition()


def _show_toast(
    *,
    parent: QtWidgets.QWidget | None,
    title: str,
    message: str,
    policy: _ToastPolicy,
    fallback: Callable[[QtWidgets.QWidget | None, str, str], None],
    dedupe_key: str = "",
    repeat_count: int = 1,
) -> None:
    target_parent = _resolve_parent(parent)
    title_text = str(title or "").strip()
    message_text = str(message or "").strip()
    if not title_text and not message_text:
        return
    modal_fallback_parent = _modal_dialog_fallback_parent(target_parent)
    if modal_fallback_parent is not None:
        fallback(modal_fallback_parent, title_text, message_text)
        return
    try:
        anchor = target_parent if target_parent is not None else parent
        anchor_key = _anchor_stack_key(anchor)
        dedupe_key_text = str(dedupe_key or "").strip()
        if dedupe_key_text:
            keyed_toast = _matching_keyed_toast(anchor_key=anchor_key, dedupe_key=dedupe_key_text)
            if keyed_toast is not None:
                if keyed_toast._severity is not policy.severity:
                    keyed_toast.apply_rollup_policy(policy)
                keyed_toast.set_content(
                    title=title_text,
                    message=message_text,
                    repeat_count=repeat_count,
                )
                keyed_toast.raise_()
                for toast in _visible_toasts_for_anchor(anchor_key):
                    toast._reposition()
                return
        if policy.sticky:
            matching_toast = _matching_sticky_detail_toast(
                anchor_key=anchor_key,
                severity=policy.severity,
                title=title_text,
                message=message_text,
            )
            if matching_toast is not None:
                matching_toast.increment_repeat()
                matching_toast.raise_()
                for toast in _visible_toasts_for_anchor(anchor_key):
                    toast._reposition()
                return

            detail_toasts = _visible_sticky_detail_toasts_for_anchor(anchor_key)
            if len(detail_toasts) >= _MAX_VISIBLE_STICKY_DETAIL_TOASTS:
                folded_toast = detail_toasts[0]
                _append_to_rollup(
                    anchor=anchor,
                    anchor_key=anchor_key,
                    summary=folded_toast.folded_summary(),
                )
                folded_toast.close_animated()
        else:
            info_toasts = _visible_info_toasts_for_anchor(anchor_key)
            if len(info_toasts) >= _MAX_VISIBLE_TOASTS:
                info_toasts[0].close_animated()

        toast = _StudioToast(
            anchor=anchor,
            title=title_text,
            message=message_text,
            style=policy.style,
            duration_ms=policy.duration_ms,
            severity=policy.severity,
            sticky=policy.sticky,
            copy_enabled=policy.copy_enabled,
            dedupe_key=dedupe_key_text,
            repeat_count=repeat_count,
        )
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
        policy=_INFO_POLICY,
        fallback=QtWidgets.QMessageBox.information,
    )


def show_warning(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        policy=_WARNING_POLICY,
        fallback=QtWidgets.QMessageBox.warning,
    )


def show_error(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        policy=_ERROR_POLICY,
        fallback=QtWidgets.QMessageBox.critical,
    )


def show_keyed_warning(
    parent: QtWidgets.QWidget | None,
    key: str,
    title: str,
    message: str,
    *,
    repeat_count: int = 1,
) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        policy=_WARNING_POLICY,
        fallback=QtWidgets.QMessageBox.warning,
        dedupe_key=key,
        repeat_count=repeat_count,
    )


def show_keyed_error(
    parent: QtWidgets.QWidget | None,
    key: str,
    title: str,
    message: str,
    *,
    repeat_count: int = 1,
) -> None:
    _show_toast(
        parent=parent,
        title=title,
        message=message,
        policy=_ERROR_POLICY,
        fallback=QtWidgets.QMessageBox.critical,
        dedupe_key=key,
        repeat_count=repeat_count,
    )
