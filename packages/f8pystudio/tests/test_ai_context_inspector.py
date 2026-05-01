from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.ui.dialogs.ai_context_inspector import AiContextInspectorDialog
from f8pystudio.ui.support.studio_theme import apply_studio_theme, studio_dark_theme


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def test_ai_context_inspector_constructs_with_theme_qss() -> None:
    app = _ensure_app()
    apply_studio_theme(app, studio_dark_theme())

    dialog = AiContextInspectorDialog("# Context\n\n```json\n{}\n```")

    assert dialog.windowTitle() == "AI Context Inspector"
    assert dialog.text_viewer.toPlainText()
