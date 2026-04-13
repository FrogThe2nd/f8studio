from __future__ import annotations

import logging
import os

_LOG_FORMAT = "%(levelname)s:%(name)s:%(message)s"
_TRUTHY_FLAGS = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_LOG_LEVEL_BY_NAME: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def resolve_env_log_level(*, log_level_raw: str, discovery_timings_raw: str) -> int:
    level_name = str(log_level_raw or "").strip().upper()
    explicit_level = _LOG_LEVEL_BY_NAME.get(level_name)
    if explicit_level is not None:
        return explicit_level

    timings_value = str(discovery_timings_raw or "").strip().lower()
    if timings_value in _TRUTHY_FLAGS:
        return logging.INFO
    return logging.WARNING


def apply_root_log_level(level: int) -> None:
    """
    Apply a global log level consistently across the root logger and handlers.

    Python logging only checks a child logger's effective level when the record
    is created. If a child logger is explicitly set to DEBUG, changing only the
    root logger level later does not stop that record from propagating to an
    already-installed root handler. Keeping root handlers in sync closes that
    gap for the studio's global log-level control.
    """
    normalized_level = int(level)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=normalized_level, format=_LOG_FORMAT)
        root_logger = logging.getLogger()

    root_logger.setLevel(normalized_level)
    for handler in list(root_logger.handlers):
        handler.setLevel(normalized_level)


def configure_root_logging_from_env() -> None:
    log_level = resolve_env_log_level(
        log_level_raw=os.environ.get("F8_LOG_LEVEL", ""),
        discovery_timings_raw=os.environ.get("F8_DISCOVERY_LOG_TIMINGS", ""),
    )
    apply_root_log_level(log_level)
