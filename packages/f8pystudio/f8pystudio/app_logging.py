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


def configure_root_logging_from_env() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    log_level = resolve_env_log_level(
        log_level_raw=os.environ.get("F8_LOG_LEVEL", ""),
        discovery_timings_raw=os.environ.get("F8_DISCOVERY_LOG_TIMINGS", ""),
    )
    logging.basicConfig(level=log_level, format=_LOG_FORMAT)
