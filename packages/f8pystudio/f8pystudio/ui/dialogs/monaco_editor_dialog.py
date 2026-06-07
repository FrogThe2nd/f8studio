from __future__ import annotations

import logging
import weakref
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from ...editor_assist.agent_context import EditorDocumentContext
from ...editor_assist.session import EditorSessionController
from ...ui.agents import AgentContextUsageButton, AgentQuickSettingsController, AgentSurfaceScope
from ...ui.support.web_asset_utils import render_prism_asset_html, resolve_monaco_base_url as resolve_web_monaco_base_url
from ...ui.support.webengine_utils import (
    configure_default_webengine_profile,
    configure_webengine_local_content_access,
    set_webengine_html,
)
from ..support.monaco_editor_host import _ask_save_before_close, open_code_editor_dialog, open_code_editor_window
from ..support.monaco_editor_page import MonacoEditorPageConfig, build_monaco_editor_html
from ..support.qt_lifecycle import qt_object_is_valid
from ..support.studio_theme import studio_dark_theme
from ..support.ui_icons import StudioIcon, icon_for

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

        self._ctx_btn = AgentContextUsageButton(
            self._controller.ai_bridge(),
            scope=AgentSurfaceScope.EDITOR,
            parent=self,
        )
        self._open_ai_sidebar_btn = QtWidgets.QToolButton(self)
        self._open_ai_sidebar_btn.setIcon(icon_for(self._open_ai_sidebar_btn, StudioIcon.MESSAGE_CHATBOT))
        self._open_ai_sidebar_btn.setText("AI Assist")
        self._open_ai_sidebar_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._open_ai_sidebar_btn.setToolTip("Open the shared AI Assist sidebar with this editor as context")
        self._open_ai_sidebar_btn.clicked.connect(self._open_shared_ai_sidebar)  # type: ignore[attr-defined]

        self._agent_settings = AgentQuickSettingsController(
            store=self._controller.ai_store(),
            bridge=self._controller.ai_bridge(),
            host=self,
            panel_parent=self,
            scope=AgentSurfaceScope.EDITOR,
        )
        self._ai_panel_btn = self._agent_settings.button
        self._ai_panel_btn.toggled.connect(self._on_ai_panel_toggle)  # type: ignore[attr-defined]
        self._ai_quick_panel = self._agent_settings.panel
        self._ai_quick_panel.open_full_config_requested.connect(self._open_full_ai_config)  # type: ignore[attr-defined]
        self._ai_quick_panel.raise_()

        editor_buttons = QtWidgets.QHBoxLayout()
        editor_buttons.setContentsMargins(0, 0, 0, 0)
        editor_buttons.setSpacing(8)
        self._save_button = QtWidgets.QPushButton("Save", self)
        self._save_button.setObjectName("monacoEditorSaveButton")
        self._save_button.clicked.connect(self._on_save_clicked)  # type: ignore[attr-defined]
        self._save_button.setEnabled(False)
        self._cancel_button = QtWidgets.QPushButton("Cancel", self)
        self._cancel_button.setObjectName("monacoEditorCancelButton")
        self._cancel_button.clicked.connect(self.close_requested.emit)  # type: ignore[attr-defined]
        editor_buttons.addWidget(self._save_button)
        editor_buttons.addWidget(self._cancel_button)

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
        bottom_bar.addWidget(self._open_ai_sidebar_btn)
        bottom_bar.addWidget(self._ai_panel_btn)
        bottom_bar.addStretch()
        bottom_bar.addLayout(editor_buttons)

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

    def _read_selection_from_page(self) -> str:
        page = self._view.page()
        if page is None:
            return ""
        result = {"value": ""}
        loop = QtCore.QEventLoop(self)
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(800)
        timer.timeout.connect(loop.quit)  # type: ignore[attr-defined]

        def _on_value(value: Any) -> None:
            result["value"] = "" if value is None else str(value)
            if loop.isRunning():
                loop.quit()

        try:
            page.runJavaScript("window._f8_getSelection && window._f8_getSelection();", _on_value)  # type: ignore[call-arg]
            timer.start()
            loop.exec()
        except (AttributeError, RuntimeError, TypeError):
            return ""
        return str(result["value"] or "")

    def _current_document_context(self) -> EditorDocumentContext:
        return EditorDocumentContext(
            code=self._read_code_from_page(),
            selection=self._read_selection_from_page(),
            language=self._controller.language(),
        )

    def _load_page(self) -> None:
        monaco_base_url = _resolve_monaco_base_url()
        html = build_monaco_editor_html(
            MonacoEditorPageConfig(
                code=self._controller.code(),
                language=self._controller.language(),
                monaco_base_url=monaco_base_url,
                python_assist_enabled=self._controller.assist_bridge() is not None,
                shared_agent_sidebar_enabled=self._controller.agent_sidebar_launcher() is not None,
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
        if checked:
            self._reposition_ai_panel()

    def _reposition_ai_panel(self) -> None:
        self._agent_settings.reposition_inside(self._view)

    def _open_full_ai_config(self) -> None:
        from .ai_provider_config_dialog import AiProviderConfigDialog

        dlg = AiProviderConfigDialog(self._controller.ai_store(), self)
        dlg.exec()

    @QtCore.Slot()
    def _open_shared_ai_sidebar(self) -> None:
        widget_ref = weakref.ref(self)
        language = self._controller.language()

        def _document_context_provider() -> EditorDocumentContext:
            widget = widget_ref()
            if widget is None or not qt_object_is_valid(widget):
                return EditorDocumentContext(code="", selection="", language=language)
            return widget._current_document_context()

        launched = self._controller.launch_agent_sidebar(_document_context_provider)
        if launched:
            return
        self._open_embedded_ai_panel()

    def _open_embedded_ai_panel(self) -> None:
        page = self._view.page()
        if page is None:
            return
        try:
            page.runJavaScript("window._f8_openEmbeddedAiPanel && window._f8_openEmbeddedAiPanel();")
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to open embedded editor AI panel")

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
