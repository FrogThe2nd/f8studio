from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class CommandExecutionErrorKind(enum.Enum):
    missing_target = "missing_target"
    invalid_args = "invalid_args"
    handler_failed = "handler_failed"


class CommandOutputPolicy(enum.Enum):
    hidden_state = "hidden_state"
    none = "none"


@dataclass(frozen=True)
class CommandExecutionResult:
    value: Any = None
    error_kind: CommandExecutionErrorKind | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_kind is None

__all__ = ["CommandExecutionErrorKind", "CommandExecutionResult", "CommandOutputPolicy"]
