from __future__ import annotations

import logging
import os

from .process_logging import FILE_HANDLER_NAME
from f8pysdk.logging_utils import (
    apply_root_log_level as _apply_root_log_level,
    resolve_env_log_level as _resolve_env_log_level,
)


def resolve_env_log_level(*, log_level_raw: str, discovery_timings_raw: str) -> int:
    return _resolve_env_log_level(log_level_raw=log_level_raw, discovery_timings_raw=discovery_timings_raw)


def apply_root_log_level(level: int) -> None:
    """
    Apply a global log level consistently across the root logger and handlers.

    Python logging only checks a child logger's effective level when the record
    is created. If a child logger is explicitly set to DEBUG, changing only the
    root logger level later does not stop that record from propagating to an
    already-installed root handler. Keeping root handlers in sync closes that
    gap for the studio's global log-level control.

    ``logging.disable`` provides the final global cutoff. This also covers
    handlers installed after the UI preference is applied, such as debug
    console handlers with a default NOTSET level.
    """
    _apply_root_log_level(level)
    for handler in list(logging.getLogger().handlers):
        if handler.name == FILE_HANDLER_NAME:
            handler.setLevel(logging.NOTSET)


def configure_root_logging_from_env() -> None:
    log_level = resolve_env_log_level(
        log_level_raw=os.environ.get("F8_LOG_LEVEL", ""),
        discovery_timings_raw=os.environ.get("F8_DISCOVERY_LOG_TIMINGS", ""),
    )
    apply_root_log_level(log_level)
