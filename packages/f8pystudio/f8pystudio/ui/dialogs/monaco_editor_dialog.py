from __future__ import annotations

import logging
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from ...editor_assist.session import EditorSessionController
from ...ui.support.web_asset_utils import render_prism_asset_html, resolve_monaco_base_url as resolve_web_monaco_base_url
from ...ui.support.webengine_utils import (
    configure_default_webengine_profile,
    configure_webengine_local_content_access,
    set_webengine_html,
)
from .ai_context_inspector import AiContextInspectorDialog
from ..support.ai_context_controls import set_tool_button_point_size, usage_pie_icon
from ..support.monaco_editor_host import _ask_save_before_close, open_code_editor_dialog, open_code_editor_window
from ..support.monaco_editor_page import MonacoEditorPageConfig, build_monaco_editor_html
from ..support.studio_theme import ai_context_button_qss, studio_dark_theme
from ..widgets.ai_quick_panel import AiQuickPanel

logger = logging.getLogger(__name__)


def _resolve_monaco_base_url() -> str:
    return resolve_web_monaco_base_url()


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
        theme_palette = studio_dark_theme().palette

        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        configure_default_webengine_profile()
        self._view = QtWebEngineWidgets.QWebEngineView(self)
        configure_webengine_local_content_access(self._view)
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
        self._ctx_btn.setIcon(usage_pie_icon(used_ratio=0.0, color=QtGui.QColor(theme_palette.info)))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(ai_context_button_qss(text_color=theme_palette.text_muted, include_background=False))
        self._ctx_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._ctx_btn.customContextMenuRequested.connect(self._on_ctx_menu_requested)
        self._controller.ai_bridge().context_usage_updated.connect(self._on_context_usage_updated)  # type: ignore[attr-defined]

        self._ai_panel_btn = QtWidgets.QToolButton()
        self._ai_panel_btn.setText("🤖")
        self._ai_panel_btn.setCheckable(True)
        self._ai_panel_btn.setToolTip("Toggle AI settings panel")
        set_tool_button_point_size(self._ai_panel_btn, 16)
        self._ai_panel_btn.setStyleSheet(
            "QToolButton { border: none; padding: 0 4px; }"
            f"QToolButton:checked {{ background: {theme_palette.button_hover_bg}; border-radius: 3px; }}"
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

    def save_current(self, *, close_after: bool) -> bool:
        page = self._view.page()
        if page is None:
            code = self._controller.code()
            saved = self._controller.save_code(code)
            if close_after and saved:
                self.accept_requested.emit()
            return saved
        code = self._read_code_from_page()
        saved = self._controller.save_code(code)
        if not saved:
            return False
        try:
            page.runJavaScript("window._f8_markSaved && window._f8_markSaved();")
        except (AttributeError, RuntimeError, TypeError):
            pass
        if close_after:
            self.accept_requested.emit()
        return True

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
        monaco_base_url = _resolve_monaco_base_url()
        html = build_monaco_editor_html(
            MonacoEditorPageConfig(
                code=self._controller.code(),
                language=self._controller.language(),
                monaco_base_url=monaco_base_url,
                python_assist_enabled=self._controller.assist_bridge() is not None,
                prism_asset_html=render_prism_asset_html(
                    languages=("python", "javascript", "bash", "json", "lua", "cpp", "c"),
                ),
            )
        )
        set_webengine_html(
            self._view,
            html,
            base_url=f"{monaco_base_url.rstrip('/')}/",
        )

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
        theme_palette = studio_dark_theme().palette
        if used_ratio < 0.5:
            color = theme_palette.info
        elif used_ratio < 0.8:
            color = theme_palette.warning
        else:
            color = theme_palette.error

        def _fmt(value: int) -> str:
            return f"{value / 1000:.0f}k" if value >= 1000 else str(value)

        free_pct = int(round(free_ratio * 100.0))
        self._ctx_btn.setIcon(usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
        self._ctx_btn.setText(f"{free_pct}% free")
        set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(ai_context_button_qss(text_color=color, include_background=False))
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
            if self._editor_widget.save_current(close_after=True):
                event.ignore()
                return
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
