from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SINGLETON_GUARD_LOG_MESSAGE = "Another PyStudio instance is already running (Zenoh liveliness token exists)."
SINGLETON_GUARD_DIALOG_TITLE = "F8PyStudio Already Running"
SINGLETON_GUARD_DIALOG_MESSAGE = (
    "Another F8PyStudio instance is already running.\n"
    "Please switch to the existing window."
)


@dataclass(frozen=True)
class RuntimeSingletonGuardResult:
    should_start: bool
    connection: Any | None


__all__ = [
    "RuntimeSingletonGuardResult",
    "SINGLETON_GUARD_DIALOG_MESSAGE",
    "SINGLETON_GUARD_DIALOG_TITLE",
    "SINGLETON_GUARD_LOG_MESSAGE",
]
