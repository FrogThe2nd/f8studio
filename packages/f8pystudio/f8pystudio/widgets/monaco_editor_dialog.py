from __future__ import annotations

import logging
import os
import weakref
from typing import Any, Callable

from qtpy import QtCore, QtGui, QtWidgets

from ..editor_assist.session import (
    EditorSessionController,
    EditorSessionKey,
    assist_context_fingerprint,
    assist_context_requires_python,
    resolve_assist_context,
    python_assist_warning,
)
from ..editor_assist.workspace import EditorAssistContext
from ..qt_font_utils import normalize_font_point_size
from ..ui_notifications import show_warning
from ..webengine_utils import configure_default_webengine_profile
from .ai_context_inspector import AiContextInspectorDialog
from .ai_quick_panel import AiQuickPanel
from .monaco_editor_page import MonacoEditorPageConfig, build_monaco_editor_html

logger = logging.getLogger(__name__)

_HOST_DIALOGS: "weakref.WeakValueDictionary[str, MonacoEditorHostDialog]" = weakref.WeakValueDictionary()


def _qt_object_is_valid(obj: QtCore.QObject) -> bool:
    """
    Return True if the underlying Qt/C++ instance is still alive.

    PySide6 can keep the Python wrapper alive even after the C++ object was
    deleted (eg. with WA_DeleteOnClose). Accessing such wrappers raises
    `RuntimeError: Internal C++ object ... already deleted.`
    """
    try:
        import shiboken6  # type: ignore[import-not-found]

        try:
            return bool(shiboken6.isValid(obj))
        except Exception:
            pass
    except Exception:
        pass

    try:
        obj.parent()
        return True
    except RuntimeError:
        return False


def _set_tool_button_point_size(button: QtWidgets.QToolButton, point_size: int) -> None:
    font = normalize_font_point_size(button.font(), fallback_point_size=point_size)
    font.setPointSize(max(1, int(point_size)))
    button.setFont(font)


def _usage_pie_icon(*, used_ratio: float, color: QtGui.QColor, size: int = 14) -> QtGui.QIcon:
    ratio = max(0.0, min(1.0, float(used_ratio)))
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    outer = QtCore.QRectF(1.0, 1.0, float(size - 2), float(size - 2))
    center = QtCore.QPointF(outer.center())

    base = QtGui.QColor("#4a4f57")
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(base)
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


def _ask_save_before_close(parent: QtWidgets.QWidget, *, title: str | None = None) -> QtWidgets.QMessageBox.StandardButton:
    message = "You have unsaved changes. Save before closing?"
    if title:
        message = f"'{title}' has unsaved changes. Save before closing?"
    return QtWidgets.QMessageBox.question(
        parent,
        "Unsaved Changes",
        message,
        QtWidgets.QMessageBox.StandardButton.Yes
        | QtWidgets.QMessageBox.StandardButton.No
        | QtWidgets.QMessageBox.StandardButton.Cancel,
        QtWidgets.QMessageBox.StandardButton.Yes,
    )


def _resolve_monaco_base_url() -> str:
    value = str(os.environ.get("F8_MONACO_BASE_URL") or "").strip().rstrip("/")
    if value:
        return value
    return "https://cdn.jsdelivr.net/npm/monaco-editor/min"


def _host_registry_key(parent: QtWidgets.QWidget | None) -> str:
    if parent is None:
        return "global"
    try:
        anchor = parent.window() if parent.window() is not None else parent
    except (AttributeError, RuntimeError, TypeError):
        anchor = parent
    return f"window:{id(anchor)}"


def _center_dialog(anchor: QtWidgets.QWidget, dialog: QtWidgets.QDialog) -> None:
    center = anchor.frameGeometry().center()
    frame = dialog.frameGeometry()
    frame.moveCenter(center)
    dialog.move(frame.topLeft())


class _EditorUiBridge(QtCore.QObject):
    dirty_changed = QtCore.Signal(bool)
    save_requested = QtCore.Signal()
    close_requested = QtCore.Signal()

    @QtCore.Slot(bool)
    def notify_dirty(self, dirty: bool) -> None:
        self.dirty_changed.emit(bool(dirty))

    @QtCore.Slot()
    def request_save(self) -> None:
        self.save_requested.emit()

    @QtCore.Slot()
    def request_close(self) -> None:
        self.close_requested.emit()

    @QtCore.Slot(str)
    def log_js(self, message: str) -> None:
        logger.debug("monaco js: %s", str(message or ""))

    @QtCore.Slot(str)
    def logJs(self, message: str) -> None:
        self.log_js(message)


class F8MonacoEditorWidget(QtWidgets.QWidget):
    code_saved = QtCore.Signal(str)
    close_requested = QtCore.Signal()
    accept_requested = QtCore.Signal()

    def __init__(
        self,
        controller: EditorSessionController,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller

        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        configure_default_webengine_profile()
        self._view = QtWebEngineWidgets.QWebEngineView(self)
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self._ui_bridge = _EditorUiBridge(self)

        self._web_channel: Any = QtWebChannel.QWebChannel(self._view.page())
        self._web_channel.registerObject("f8EditorUi", self._ui_bridge)
        self._web_channel.registerObject("aiAssist", self._controller.ai_bridge())
        assist_bridge = self._controller.assist_bridge()
        if assist_bridge is not None:
            self._web_channel.registerObject("pyAssist", assist_bridge)
        self._view.page().setWebChannel(self._web_channel)

        self._ctx_btn = QtWidgets.QToolButton()
        self._ctx_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._ctx_btn.setIconSize(QtCore.QSize(14, 14))
        self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=0.0, color=QtGui.QColor("#4fc3f7")))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        _set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(
            "QToolButton { color: #9aa4b2; border: none; padding: 0 4px; }"
            "QToolButton:hover { color: #d7deea; }"
        )
        self._ctx_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._ctx_btn.customContextMenuRequested.connect(self._on_ctx_menu_requested)
        self._controller.ai_bridge().context_usage_updated.connect(self._on_context_usage_updated)  # type: ignore[attr-defined]

        self._ai_panel_btn = QtWidgets.QToolButton()
        self._ai_panel_btn.setText("🤖")
        self._ai_panel_btn.setCheckable(True)
        self._ai_panel_btn.setToolTip("Toggle AI settings panel")
        _set_tool_button_point_size(self._ai_panel_btn, 16)
        self._ai_panel_btn.setStyleSheet(
            "QToolButton { border: none; padding: 0 4px; }"
            "QToolButton:checked { background: #2d2d2d; border-radius: 3px; }"
        )
        self._ai_panel_btn.toggled.connect(self._on_ai_panel_toggle)  # type: ignore[attr-defined]

        self._ai_quick_panel = AiQuickPanel(self._controller.ai_store(), self._controller.ai_bridge(), self)
        self._ai_quick_panel.setVisible(False)
        self._ai_quick_panel.open_full_config_requested.connect(self._open_full_ai_config)  # type: ignore[attr-defined]
        self._ai_quick_panel.raise_()

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.close_requested.emit)  # type: ignore[attr-defined]
        self._save_button = buttons.button(QtWidgets.QDialogButtonBox.Save)
        self._save_button.setEnabled(False)

        self._ui_bridge.dirty_changed.connect(self._on_dirty_changed)  # type: ignore[attr-defined]
        self._ui_bridge.save_requested.connect(self._on_save_clicked)  # type: ignore[attr-defined]
        self._ui_bridge.close_requested.connect(self.close_requested.emit)  # type: ignore[attr-defined]
        self._controller.code_saved.connect(self.code_saved.emit)  # type: ignore[attr-defined]
        self._controller.dirty_changed.connect(self._on_controller_dirty_changed)  # type: ignore[attr-defined]
        self._controller.close_requested.connect(self.close_requested.emit)  # type: ignore[attr-defined]

        self._save_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self)
        self._save_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._on_save_clicked)
        self._close_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self)
        self._close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._close_shortcut.activated.connect(self.close_requested.emit)

        editor_layout = QtWidgets.QHBoxLayout()
        editor_layout.setSpacing(0)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self._view, 1)

        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.addWidget(self._ctx_btn)
        bottom_bar.addWidget(self._ai_panel_btn)
        bottom_bar.addStretch()
        bottom_bar.addWidget(buttons)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(editor_layout, 1)
        layout.addLayout(bottom_bar)

        self._load_page()

    def controller(self) -> EditorSessionController:
        return self._controller

    def code(self) -> str:
        return self._controller.code()

    def is_dirty(self) -> bool:
        return self._controller.dirty()

    def title(self) -> str:
        return self._controller.title()

    def set_close_on_save(self, close_on_save: bool) -> None:
        self._controller.set_close_on_save(close_on_save)

    def shutdown(self) -> None:
        self._controller.shutdown()

    def save_current(self, *, close_after: bool) -> str:
        page = self._view.page()
        if page is None:
            code = self._controller.code()
            self._controller.save_code(code)
            if close_after:
                self.accept_requested.emit()
            return code
        code = self._read_code_from_page()
        self._controller.save_code(code)
        try:
            page.runJavaScript("window._f8_markSaved && window._f8_markSaved();")
        except (AttributeError, RuntimeError, TypeError):
            pass
        if close_after:
            self.accept_requested.emit()
        return code

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_ai_panel()

    def _read_code_from_page(self) -> str:
        page = self._view.page()
        if page is None:
            return self._controller.code()
        result = {"value": self._controller.code()}
        loop = QtCore.QEventLoop(self)
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(1500)
        timer.timeout.connect(loop.quit)  # type: ignore[attr-defined]

        def _on_value(value: Any) -> None:
            result["value"] = "" if value is None else str(value)
            if loop.isRunning():
                loop.quit()

        try:
            page.runJavaScript("window._f8_getValue && window._f8_getValue();", _on_value)  # type: ignore[call-arg]
            timer.start()
            loop.exec()
        except (AttributeError, RuntimeError, TypeError):
            return self._controller.code()
        return str(result["value"] or "")

    def _load_page(self) -> None:
        html = build_monaco_editor_html(
            MonacoEditorPageConfig(
                code=self._controller.code(),
                language=self._controller.language(),
                monaco_base_url=_resolve_monaco_base_url(),
                python_assist_enabled=self._controller.assist_bridge() is not None,
            )
        )
        self._view.setHtml(html)

    @QtCore.Slot()
    def _on_save_clicked(self) -> None:
        if not self._controller.dirty():
            return
        self.save_current(close_after=self._controller.close_on_save())

    @QtCore.Slot(bool)
    def _on_ai_panel_toggle(self, checked: bool) -> None:
        self._ai_quick_panel.setVisible(checked)
        if checked:
            self._ai_quick_panel.raise_()
            self._reposition_ai_panel()

    def _reposition_ai_panel(self) -> None:
        rect = self._view.geometry()
        if rect.width() <= 0:
            return

        self._ai_quick_panel.adjustSize()
        panel_width = self._ai_quick_panel.width()
        panel_height = self._ai_quick_panel.height()
        margin = 10
        x = rect.x() + margin
        y = rect.y() + rect.height() - panel_height - margin
        self._ai_quick_panel.move(x, y)

    @QtCore.Slot(int, int)
    def _on_context_usage_updated(self, used: int, total: int) -> None:
        if total <= 0:
            return
        used_ratio = max(0.0, min(1.0, used / total))
        free_ratio = max(0.0, 1.0 - used_ratio)
        if used_ratio < 0.5:
            color = "#4fc3f7"
        elif used_ratio < 0.8:
            color = "#ffd54f"
        else:
            color = "#ef9a9a"

        def _fmt(value: int) -> str:
            return f"{value / 1000:.0f}k" if value >= 1000 else str(value)

        free_pct = int(round(free_ratio * 100.0))
        self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
        self._ctx_btn.setText(f"{free_pct}% free")
        _set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(
            f"QToolButton {{ color: {color}; border: none; padding: 0 4px; }}"
            "QToolButton:hover { color: white; }"
        )
        try:
            breakdown = self._controller.ai_bridge().get_context_breakdown()
            tip = (
                "AI Context Usage\n"
                f"System: {_fmt(int(breakdown['system_tokens']))} tok\n"
                f"Code: {_fmt(int(breakdown['code_tokens']))} tok\n"
                f"Chat: {_fmt(int(breakdown['chat_tokens']))} tok\n"
                f"Free: {free_pct}%\n"
                f"Used: {_fmt(int(breakdown['used_tokens']))} / {_fmt(int(breakdown['total_tokens']))} tok"
            )
            self._ctx_btn.setToolTip(tip)
        except Exception:
            logger.exception("Failed to update Monaco AI context tooltip")

    def _on_ctx_menu_requested(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        inspect_act = menu.addAction("Inspect Current Context Payload...")
        inspect_act.triggered.connect(self._inspect_context)
        menu.exec(self._ctx_btn.mapToGlobal(pos))

    def _inspect_context(self) -> None:
        report = self._controller.ai_bridge().get_context_report()
        dlg = AiContextInspectorDialog(report, self)
        dlg.exec()

    def _open_full_ai_config(self) -> None:
        from .ai_provider_config_dialog import AiProviderConfigDialog

        dlg = AiProviderConfigDialog(self._controller.ai_store(), self)
        dlg.exec()

    @QtCore.Slot(bool)
    def _on_dirty_changed(self, dirty: bool) -> None:
        self._controller.set_dirty(bool(dirty))

    @QtCore.Slot(bool)
    def _on_controller_dirty_changed(self, dirty: bool) -> None:
        self._save_button.setEnabled(bool(dirty))


class F8MonacoEditorDialog(QtWidgets.QDialog):
    code_saved = QtCore.Signal(str)

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        controller: EditorSessionController,
        owns_controller: bool = True,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._controller.setParent(self)
        self._owns_controller = owns_controller
        self._session_shutdown = False
        self.setWindowTitle(self._controller.title())

        self._editor_widget = F8MonacoEditorWidget(self._controller, self)
        self._editor_widget.code_saved.connect(self.code_saved.emit)  # type: ignore[attr-defined]
        self._editor_widget.close_requested.connect(self.close)  # type: ignore[attr-defined]
        self._editor_widget.accept_requested.connect(self.accept)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._editor_widget, 1)

        self.resize(1120, 720)

    def code(self) -> str:
        return self._controller.code()

    def set_close_on_save(self, close_on_save: bool) -> None:
        self._editor_widget.set_close_on_save(close_on_save)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if not self._editor_widget.is_dirty():
            self._shutdown_session()
            event.accept()
            return
        answer = _ask_save_before_close(self, title=self.windowTitle())
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._editor_widget.save_current(close_after=True)
            event.ignore()
            return
        if answer == QtWidgets.QMessageBox.StandardButton.No:
            self._shutdown_session()
            event.accept()
            return
        event.ignore()

    def _shutdown_session(self) -> None:
        if self._session_shutdown or not self._owns_controller:
            return
        self._session_shutdown = True
        self._editor_widget.shutdown()


class MonacoEditorHostDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Monaco Code Editor")
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.resize(1280, 820)

        self._sessions: dict[str, F8MonacoEditorWidget] = {}
        self._closing_all_tabs = False

        self._tabs = QtWidgets.QTabWidget(self)
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab_by_index)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs, 1)

    def has_session(self, session_key: EditorSessionKey | None) -> bool:
        if session_key is None:
            return False
        return session_key.as_id() in self._sessions

    def focus_session(self, session_key: EditorSessionKey | None) -> bool:
        if session_key is None:
            return False
        editor_widget = self._sessions.get(session_key.as_id())
        if editor_widget is None:
            return False
        index = self._tabs.indexOf(editor_widget)
        if index >= 0:
            self._tabs.setCurrentIndex(index)
            return True
        return False

    def refresh_session(
        self,
        *,
        session_key: EditorSessionKey,
        title: str,
        code: str,
        language: str,
        on_saved: Callable[[str], None],
        assist_context: EditorAssistContext | None,
        assist_context_provider: Callable[[], EditorAssistContext | None] | None,
    ) -> bool:
        editor_widget = self._sessions.get(session_key.as_id())
        if editor_widget is None:
            return False

        resolved_context = resolve_assist_context(
            assist_context=assist_context,
            assist_context_provider=assist_context_provider,
        )
        requested_language = str(language or "plaintext").strip().lower() or "plaintext"
        if assist_context_requires_python(resolved_context):
            requested_language = "python"

        controller = editor_widget.controller()
        requested_title = str(title or "Edit Code")
        needs_replace = (
            controller.title() != requested_title
            or controller.language() != requested_language
            or controller.code() != str(code or "")
            or assist_context_fingerprint(controller.assist_context())
            != assist_context_fingerprint(resolved_context)
        )
        if not needs_replace:
            return self.focus_session(session_key)
        if editor_widget.is_dirty():
            return self.focus_session(session_key)

        replacement = EditorSessionController(
            title=requested_title,
            code=code,
            language=requested_language,
            session_key=session_key,
            assist_context=resolved_context,
            assist_context_provider=assist_context_provider,
            close_on_save=False,
            parent=self,
        )
        replacement.code_saved.connect(on_saved)  # type: ignore[arg-type]
        replacement_widget = self._create_editor_widget(replacement)
        replacement.dirty_changed.connect(
            lambda _dirty, current_controller=replacement: self._update_tab_title(current_controller)
        )
        replacement_widget.close_requested.connect(
            lambda current_widget=replacement_widget: self._close_editor_widget(current_widget, interactive=True)
        )
        replacement_widget.accept_requested.connect(
            lambda current_widget=replacement_widget: self._close_editor_widget(current_widget, interactive=False)
        )

        index = self._tabs.indexOf(editor_widget)
        if index < 0:
            editor_widget.shutdown()
            editor_widget.deleteLater()
            return False

        self._tabs.removeTab(index)
        editor_widget.shutdown()
        editor_widget.deleteLater()
        self._tabs.insertTab(index, replacement_widget, self._tab_title(replacement))
        self._tabs.setCurrentIndex(index)
        self._sessions[session_key.as_id()] = replacement_widget
        return True

    def add_session(self, controller: EditorSessionController, on_saved: Callable[[str], None]) -> F8MonacoEditorWidget:
        controller.setParent(self)
        editor_widget = self._create_editor_widget(controller)
        controller.code_saved.connect(on_saved)  # type: ignore[arg-type]
        controller.dirty_changed.connect(
            lambda _dirty, current_controller=controller: self._update_tab_title(current_controller)
        )
        editor_widget.close_requested.connect(
            lambda current_widget=editor_widget: self._close_editor_widget(current_widget, interactive=True)
        )
        editor_widget.accept_requested.connect(
            lambda current_widget=editor_widget: self._close_editor_widget(current_widget, interactive=False)
        )

        title = self._tab_title(controller)
        index = self._tabs.addTab(editor_widget, title)
        self._tabs.setCurrentIndex(index)

        session_key = controller.session_key()
        if session_key is not None:
            self._sessions[session_key.as_id()] = editor_widget
        return editor_widget

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._closing_all_tabs = True
        try:
            while self._tabs.count() > 0:
                current_widget = self._tabs.widget(self._tabs.count() - 1)
                if not isinstance(current_widget, F8MonacoEditorWidget):
                    self._tabs.removeTab(self._tabs.count() - 1)
                    continue
                if not self._close_editor_widget(current_widget, interactive=True):
                    event.ignore()
                    return
        finally:
            self._closing_all_tabs = False
        event.accept()

    def _create_editor_widget(self, controller: EditorSessionController) -> F8MonacoEditorWidget:
        return F8MonacoEditorWidget(controller, self)

    def _close_tab_by_index(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if isinstance(widget, F8MonacoEditorWidget):
            self._close_editor_widget(widget, interactive=True)

    def _close_editor_widget(self, editor_widget: F8MonacoEditorWidget, *, interactive: bool) -> bool:
        controller = editor_widget.controller()
        if interactive and editor_widget.is_dirty():
            answer = _ask_save_before_close(self, title=controller.title())
            if answer == QtWidgets.QMessageBox.StandardButton.Cancel:
                return False
            if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                editor_widget.save_current(close_after=False)

        session_key = controller.session_key()
        if session_key is not None:
            self._sessions.pop(session_key.as_id(), None)

        index = self._tabs.indexOf(editor_widget)
        if index >= 0:
            self._tabs.removeTab(index)
        editor_widget.shutdown()
        editor_widget.deleteLater()

        if self._tabs.count() == 0 and not self._closing_all_tabs:
            self.close()
        return True

    def _tab_title(self, controller: EditorSessionController) -> str:
        title = controller.title()
        if controller.dirty():
            return f"* {title}"
        return title

    def _update_tab_title(self, controller: EditorSessionController) -> None:
        session_key = controller.session_key()
        if session_key is None:
            return
        editor_widget = self._sessions.get(session_key.as_id())
        if editor_widget is None:
            return
        index = self._tabs.indexOf(editor_widget)
        if index >= 0:
            self._tabs.setTabText(index, self._tab_title(controller))


def _host_dialog(parent: QtWidgets.QWidget | None) -> MonacoEditorHostDialog:
    key = _host_registry_key(parent)
    host = _HOST_DIALOGS.get(key)
    if host is not None:
        if _qt_object_is_valid(host):
            return host
        _HOST_DIALOGS.pop(key, None)

    anchor = None
    if parent is not None:
        try:
            anchor = parent.window() if parent.window() is not None else parent
        except (AttributeError, RuntimeError, TypeError):
            anchor = parent
    host = MonacoEditorHostDialog(anchor)
    host.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
    _HOST_DIALOGS[key] = host
    try:
        host.destroyed.connect(lambda _obj=None, current_key=key: _HOST_DIALOGS.pop(current_key, None))  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError, TypeError):
        logger.exception("Failed to wire Monaco host destroyed cleanup")
    return host


def open_code_editor_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> str | None:
    controller = EditorSessionController(
        title=title,
        code=code,
        language=language,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        close_on_save=True,
    )
    warn_text = python_assist_warning(controller.assist_context())
    if controller.language().lower() == "python" and warn_text:
        show_warning(parent, "Python Assist Warning", warn_text)
    dlg = F8MonacoEditorDialog(parent, controller=controller, owns_controller=True)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    return dlg.code()


def open_code_editor_window(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    on_saved: Callable[[str], None],
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
    session_key: EditorSessionKey | None = None,
) -> QtWidgets.QDialog:
    host = _host_dialog(parent)
    if session_key is not None and host.refresh_session(
        session_key=session_key,
        title=title,
        code=code,
        language=language,
        on_saved=on_saved,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    ):
        host.show()
        host.raise_()
        host.activateWindow()
        return host
    if host.focus_session(session_key):
        host.show()
        host.raise_()
        host.activateWindow()
        return host

    controller = EditorSessionController(
        title=title,
        code=code,
        language=language,
        session_key=session_key,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        close_on_save=False,
    )
    warn_text = python_assist_warning(controller.assist_context())
    if controller.language().lower() == "python" and warn_text:
        show_warning(parent, "Python Assist Warning", warn_text)
    host.add_session(controller, on_saved)

    if parent is not None and not host.isVisible():
        try:
            anchor = parent.window() if parent.window() is not None else parent
            _center_dialog(anchor, host)
        except (AttributeError, RuntimeError, TypeError):
            pass

    host.show()
    host.raise_()
    host.activateWindow()
    return host
