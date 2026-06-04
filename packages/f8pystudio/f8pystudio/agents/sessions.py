from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StudioAgentSessionKey:
    scope: str
    graph_id: str = ""
    node_id: str = ""
    editor_id: str = ""

    @classmethod
    def sidebar(cls, *, graph_id: str = "") -> "StudioAgentSessionKey":
        return cls(scope="sidebar", graph_id=str(graph_id or ""))

    @classmethod
    def editor(cls, *, editor_id: str, graph_id: str = "", node_id: str = "") -> "StudioAgentSessionKey":
        return cls(
            scope="editor",
            graph_id=str(graph_id or ""),
            node_id=str(node_id or ""),
            editor_id=str(editor_id or ""),
        )

    @classmethod
    def node(cls, *, graph_id: str, node_id: str) -> "StudioAgentSessionKey":
        return cls(scope="node", graph_id=str(graph_id or ""), node_id=str(node_id or ""))

    def as_id(self) -> str:
        return ":".join([self.scope, self.graph_id, self.node_id, self.editor_id])


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

    def clear(self, key: StudioAgentSessionKey) -> None:
        self._sessions.pop(key.as_id(), None)
