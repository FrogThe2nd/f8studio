"""Monaco page resource builder used by the hosted multi-session editor."""

from __future__ import annotations

from typing import Callable

from qtpy import QtWidgets

from ...agents.graph_context import GraphContextSnapshot
from ...editor_assist.agent_context import EditorAgentContext
from ...editor_assist.agent_scope import EditorAgentScope
from ...editor_assist.session import EditorSessionKey
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
    agent_scope: EditorAgentScope | None = None,
    agent_tools: tuple[object, ...] = (),
    agent_context_providers: tuple[object, ...] = (),
    graph_context_snapshot_provider: Callable[[], GraphContextSnapshot | None] | None = None,
    retained_agent_dependencies: tuple[object, ...] = (),
    agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
) -> str | None:
    from .monaco_editor_host import open_code_editor_dialog as open_hosted_code_editor_dialog

    return open_hosted_code_editor_dialog(
        parent,
        title=title,
        code=code,
        language=language,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        agent_scope=agent_scope,
        agent_tools=agent_tools,
        agent_context_providers=agent_context_providers,
        graph_context_snapshot_provider=graph_context_snapshot_provider,
        retained_agent_dependencies=retained_agent_dependencies,
        agent_sidebar_launcher=agent_sidebar_launcher,
    )


def open_code_editor_window(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    on_saved: Callable[[str], bool | None],
    target_exists_provider: Callable[[], bool] | None = None,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
    session_key: EditorSessionKey | None = None,
    agent_scope: EditorAgentScope | None = None,
    agent_tools: tuple[object, ...] = (),
    agent_context_providers: tuple[object, ...] = (),
    graph_context_snapshot_provider: Callable[[], GraphContextSnapshot | None] | None = None,
    retained_agent_dependencies: tuple[object, ...] = (),
    agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
) -> QtWidgets.QDialog:
    from .monaco_editor_host import open_code_editor_window as open_hosted_code_editor_window

    return open_hosted_code_editor_window(
        parent,
        title=title,
        code=code,
        language=language,
        on_saved=on_saved,
        target_exists_provider=target_exists_provider,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        session_key=session_key,
        agent_scope=agent_scope,
        agent_tools=agent_tools,
        agent_context_providers=agent_context_providers,
        graph_context_snapshot_provider=graph_context_snapshot_provider,
        retained_agent_dependencies=retained_agent_dependencies,
        agent_sidebar_launcher=agent_sidebar_launcher,
    )
