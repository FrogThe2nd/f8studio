from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorAgentScope:
    graph_id: str
    node_id: str
    field_name: str

    @classmethod
    def node_field(cls, *, graph_id: str, node_id: str, field_name: str) -> "EditorAgentScope":
        return cls(
            graph_id=str(graph_id or "").strip(),
            node_id=str(node_id or "").strip(),
            field_name=str(field_name or "").strip(),
        )

    def is_valid(self) -> bool:
        return bool(self.graph_id and self.node_id and self.field_name)

    def default_conversation_id(self) -> str:
        return f"node:{self.graph_id}:{self.node_id}:{self.field_name}"


__all__ = ["EditorAgentScope"]
