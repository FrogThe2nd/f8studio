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


def resolve_env_log_level(*, log_level_raw: str, discovery_timings_raw: str = "") -> int:
    level_name = str(log_level_raw or "").strip().upper()
    explicit_level = _LOG_LEVEL_BY_NAME.get(level_name)
    if explicit_level is not None:
        return explicit_level

    timings_value = str(discovery_timings_raw or "").strip().lower()
    if timings_value in _TRUTHY_FLAGS:
        return logging.INFO
    return logging.WARNING


def apply_root_log_level(level: int) -> None:
    normalized_level = int(level)
    logging.disable(disabled_cutoff_for_min_level(normalized_level))
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=normalized_level, format=_LOG_FORMAT)
        root_logger = logging.getLogger()

    root_logger.setLevel(normalized_level)
    for handler in list(root_logger.handlers):
        handler.setLevel(normalized_level)


def disabled_cutoff_for_min_level(level: int) -> int:
    if level <= logging.DEBUG:
        return logging.NOTSET
    return level - 1


def configure_root_logging_from_env() -> None:
    log_level = resolve_env_log_level(
        log_level_raw=os.environ.get("F8_LOG_LEVEL", ""),
        discovery_timings_raw=os.environ.get("F8_DISCOVERY_LOG_TIMINGS", ""),
    )
    apply_root_log_level(log_level)
