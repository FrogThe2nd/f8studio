"""Monaco page resource builder used by the hosted multi-session editor."""

from __future__ import annotations

from typing import Callable

from qtpy import QtWidgets

from ...editor_assist.workspace import EditorAssistContext
from .monaco_editor_page_html import MonacoEditorPageConfig, build_monaco_editor_html

__all__ = [
    "MonacoEditorPageConfig",
    "build_monaco_editor_html",
    "open_code_editor_dialog",
    "open_code_editor_window",
]


def open_code_editor_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> str | None:
    from .monaco_editor_host import open_code_editor_dialog as open_hosted_code_editor_dialog

    return open_hosted_code_editor_dialog(
        parent,
        title=title,
        code=code,
        language=language,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )


def open_code_editor_window(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    on_saved: Callable[[str], None],
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> QtWidgets.QDialog:
    from .monaco_editor_host import open_code_editor_window as open_hosted_code_editor_window

    return open_hosted_code_editor_window(
        parent,
        title=title,
        code=code,
        language=language,
        on_saved=on_saved,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )
