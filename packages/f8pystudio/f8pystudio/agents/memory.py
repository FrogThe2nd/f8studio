from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StudioMemoryScope:
    project_id: str = ""
    graph_id: str = ""
    node_id: str = ""
    editor_id: str = ""

    def key(self, namespace: str) -> str:
        return ":".join(
            [
                str(namespace or "f8"),
                str(self.project_id or ""),
                str(self.graph_id or ""),
                str(self.node_id or ""),
                str(self.editor_id or ""),
            ]
        )


@dataclass
class StudioMemoryRecord:
    scope: StudioMemoryScope
    values: dict[str, Any] = field(default_factory=dict)


class InMemoryStudioAgentMemory:
    def __init__(self) -> None:
        self._records: dict[str, StudioMemoryRecord] = {}

    def record(self, scope: StudioMemoryScope) -> StudioMemoryRecord:
        key = scope.key("f8")
        existing = self._records.get(key)
        if existing is not None:
            return existing
        record = StudioMemoryRecord(scope=scope)
        self._records[key] = record
        return record

    def set_value(self, scope: StudioMemoryScope, key: str, value: Any) -> None:
        self.record(scope).values[str(key)] = value

    def get_value(self, scope: StudioMemoryScope, key: str, default: Any = None) -> Any:
        return self.record(scope).values.get(str(key), default)
