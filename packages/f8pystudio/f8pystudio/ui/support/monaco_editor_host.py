from __future__ import annotations

import logging
import weakref
from typing import Callable, Protocol, TypeAlias, cast

from qtpy import QtCore, QtGui, QtWidgets

from ...editor_assist.session import (
    EditorSessionController,
    EditorSessionKey,
    assist_context_fingerprint,
    assist_context_requires_python,
    python_assist_warning,
    resolve_assist_context,
)
from ...editor_assist.workspace import EditorAssistContext
from ...ui.support.ui_notifications import show_warning

logger = logging.getLogger(__name__)

_HOST_DIALOGS: "weakref.WeakValueDictionary[str, MonacoEditorHostDialog]" = weakref.WeakValueDictionary()


class _QtSignalLike(Protocol):
    def connect(self, slot: Callable[..., object]) -> object: ...


class MonacoEditorWidgetLike(Protocol):
    close_requested: _QtSignalLike
    accept_requested: _QtSignalLike

    def controller(self) -> EditorSessionController: ...
    def is_dirty(self) -> bool: ...
    def shutdown(self) -> None: ...
    def save_current(self, *, close_after: bool) -> str: ...
    def deleteLater(self) -> None: ...


SavedCodeHandler: TypeAlias = Callable[[str], None]


def _qt_object_is_valid(obj: QtCore.QObject) -> bool:
    """
    Return True if the underlying Qt/C++ instance is still alive.

    PySide6 can keep the Python wrapper alive even after the C++ object was
    deleted (eg. with WA_DeleteOnClose). Accessing such wrappers raises
    `RuntimeError: Internal C++ object ... already deleted.`
    """
    try:
        import shiboken6  # type: ignore[import-not-found]
    except ImportError:
        shiboken6 = None

    if shiboken6 is not None:
        try:
            return bool(shiboken6.isValid(obj))
        except RuntimeError:
            return False
        except TypeError:
            return False

    try:
        obj.parent()
        return True
    except RuntimeError:
        return False


def _ask_save_before_close(
    parent: QtWidgets.QWidget,
    *,
    title: str | None = None,
) -> QtWidgets.QMessageBox.StandardButton:
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


class MonacoEditorHostDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Monaco Code Editor")
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.resize(1280, 820)

        self._sessions: dict[str, MonacoEditorWidgetLike] = {}
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
        index = self._tabs.indexOf(cast(QtWidgets.QWidget, editor_widget))
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
        on_saved: SavedCodeHandler,
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

        index = self._tabs.indexOf(cast(QtWidgets.QWidget, editor_widget))
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

    def add_session(self, controller: EditorSessionController, on_saved: SavedCodeHandler) -> MonacoEditorWidgetLike:
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
                if current_widget is None:
                    self._tabs.removeTab(self._tabs.count() - 1)
                    continue
                if not self._close_editor_widget(cast(MonacoEditorWidgetLike, current_widget), interactive=True):
                    event.ignore()
                    return
        finally:
            self._closing_all_tabs = False
        event.accept()

    def _create_editor_widget(self, controller: EditorSessionController) -> MonacoEditorWidgetLike:
        from ..dialogs.monaco_editor_dialog import F8MonacoEditorWidget

        return F8MonacoEditorWidget(controller, self)

    def _close_tab_by_index(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if widget is not None:
            self._close_editor_widget(cast(MonacoEditorWidgetLike, widget), interactive=True)

    def _close_editor_widget(self, editor_widget: MonacoEditorWidgetLike, *, interactive: bool) -> bool:
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

        index = self._tabs.indexOf(cast(QtWidgets.QWidget, editor_widget))
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
        index = self._tabs.indexOf(cast(QtWidgets.QWidget, editor_widget))
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
    from ..dialogs.monaco_editor_dialog import F8MonacoEditorDialog

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
    on_saved: SavedCodeHandler,
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
