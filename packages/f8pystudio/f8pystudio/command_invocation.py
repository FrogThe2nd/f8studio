from __future__ import annotations

import itertools
import time
from typing import Any

_COMMAND_TRIGGER_KEY = "__f8CommandTriggerId"
_trigger_counter = itertools.count(1)


def next_command_trigger_id() -> int:
    """Return a JSON-safe trigger id for event-like command state writes."""
    return int(time.time_ns()) + next(_trigger_counter)


def command_state_payload(args: dict[str, Any] | None) -> int | dict[str, Any]:
    """
    Build the hidden command input state value sent by Studio UI buttons.

    No-param commands use a scalar trigger id, matching Control Panel button
    behavior. Param commands keep their args and add a reserved trigger field
    so repeating the same args still produces a fresh command event.
    """
    trigger_id = next_command_trigger_id()
    if not args:
        return trigger_id
    payload = dict(args)
    payload[_COMMAND_TRIGGER_KEY] = trigger_id
    return payload
