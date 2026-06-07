from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_SHARED_AGENT_SESSION_REGISTRY: StudioAgentSessionRegistry | None = None


@dataclass(frozen=True)
class StudioAgentSessionKey:
    scope: str
    graph_id: str = ""
    node_id: str = ""
    editor_id: str = ""
    conversation_id: str = ""

    @classmethod
    def sidebar(cls, *, graph_id: str = "", conversation_id: str = "") -> "StudioAgentSessionKey":
        return cls(
            scope="sidebar",
            graph_id=str(graph_id or ""),
            conversation_id=str(conversation_id or ""),
        )

    @classmethod
    def editor(
        cls,
        *,
        editor_id: str,
        graph_id: str = "",
        node_id: str = "",
        conversation_id: str = "",
    ) -> "StudioAgentSessionKey":
        return cls(
            scope="editor",
            graph_id=str(graph_id or ""),
            node_id=str(node_id or ""),
            editor_id=str(editor_id or ""),
            conversation_id=str(conversation_id or ""),
        )

    @classmethod
    def node(cls, *, graph_id: str, node_id: str, conversation_id: str = "") -> "StudioAgentSessionKey":
        return cls(
            scope="node",
            graph_id=str(graph_id or ""),
            node_id=str(node_id or ""),
            conversation_id=str(conversation_id or ""),
        )

    def as_id(self) -> str:
        parts = [self.scope, self.graph_id, self.node_id, self.editor_id]
        if self.conversation_id:
            parts.append(self.conversation_id)
        return ":".join(parts)


class StudioAgentSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, object] = {}

    def session_for(self, key: StudioAgentSessionKey) -> object | None:
        session_id = key.as_id()
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing
        try:
            from agent_framework import AgentSession
        except ModuleNotFoundError:
            return None
        else:
            session = AgentSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def restore(self, key: StudioAgentSessionKey, payload: dict[str, Any]) -> object | None:
        session_id = key.as_id()
        try:
            from agent_framework import AgentSession
        except ModuleNotFoundError:
            return None
        session = AgentSession.from_dict(dict(payload))
        self._sessions[session_id] = session
        return session

    def serialize(self, key: StudioAgentSessionKey) -> dict[str, Any] | None:
        session = self._sessions.get(key.as_id())
        if session is None:
            return None
        try:
            from agent_framework import AgentSession
        except ModuleNotFoundError:
            return None
        if not isinstance(session, AgentSession):
            return None
        payload = session.to_dict()
        return dict(payload)

    def clear(self, key: StudioAgentSessionKey) -> None:
        self._sessions.pop(key.as_id(), None)


def shared_agent_session_registry() -> StudioAgentSessionRegistry:
    global _SHARED_AGENT_SESSION_REGISTRY
    if _SHARED_AGENT_SESSION_REGISTRY is None:
        _SHARED_AGENT_SESSION_REGISTRY = StudioAgentSessionRegistry()
    return _SHARED_AGENT_SESSION_REGISTRY
