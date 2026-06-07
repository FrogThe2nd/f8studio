from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from f8pystudio.agents.graph_context import GraphContextSnapshot
from f8pystudio.editor_assist.agent_scope import EditorAgentScope
from f8pystudio.editor_assist.workspace import EditorAssistContext

logger = logging.getLogger(__name__)

_EDITOR_AGENT_CONTEXT_PROVIDER_ERRORS = (Exception,)


@dataclass(frozen=True)
class EditorDocumentContext:
    code: str
    selection: str = ""
    language: str = ""


@dataclass(frozen=True)
class EditorAgentContext:
    title: str
    language: str
    assist_context: EditorAssistContext | None
    assist_context_provider: Callable[[], EditorAssistContext | None] | None
    agent_scope: EditorAgentScope | None
    document_context_provider: Callable[[], EditorDocumentContext]
    graph_context_snapshot_provider: Callable[[], GraphContextSnapshot | None] | None
    agent_tools: tuple[object, ...] = ()
    agent_context_providers: tuple[object, ...] = ()
    retained_agent_dependencies: tuple[object, ...] = ()

    def current_assist_context(self) -> EditorAssistContext | None:
        provider = self.assist_context_provider
        if provider is None:
            return self.assist_context
        try:
            return provider()
        except _EDITOR_AGENT_CONTEXT_PROVIDER_ERRORS:
            logger.exception("Failed to build editor agent assist context title=%s", self.title)
            return self.assist_context

    def current_document_context(self) -> EditorDocumentContext:
        try:
            context = self.document_context_provider()
        except _EDITOR_AGENT_CONTEXT_PROVIDER_ERRORS:
            logger.exception("Failed to read editor agent document context title=%s", self.title)
            return EditorDocumentContext(code="", selection="", language=self.language)
        return EditorDocumentContext(
            code=str(context.code or ""),
            selection=str(context.selection or ""),
            language=str(context.language or self.language or "").strip(),
        )

    def display_label(self) -> str:
        title = str(self.title or "").strip()
        if title:
            return title
        scope = self.agent_scope
        if scope is not None and scope.field_name:
            return scope.field_name
        return "Editor"

    def tooltip_text(self) -> str:
        lines = [self.display_label()]
        language = str(self.language or "").strip()
        if language:
            lines.append(f"Language: {language}")
        scope = self.agent_scope
        if scope is not None:
            if scope.node_id:
                lines.append(f"Node: {scope.node_id}")
            if scope.field_name:
                lines.append(f"Field: {scope.field_name}")
        return "\n".join(lines)


__all__ = ["EditorAgentContext", "EditorDocumentContext"]
