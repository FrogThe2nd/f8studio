from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AiPanelStateStore(Protocol):
    def set_value(self, key: str, value: Any) -> None: ...
    def get_value(self, key: str, default: Any = None) -> Any: ...


class MemoryAiPanelStateStore(AiPanelStateStore):
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self._values[str(key)] = value

    def get_value(self, key: str, default: Any = None) -> Any:
        return self._values.get(str(key), default)
