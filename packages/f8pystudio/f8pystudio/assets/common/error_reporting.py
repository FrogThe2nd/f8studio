from __future__ import annotations

import logging
from time import monotonic


class ErrorLogLimiter:
    def __init__(self) -> None:
        self._last_logged_at_by_key: dict[str, float] = {}

    def should_log(self, key: str, *, interval_seconds: float) -> bool:
        now = monotonic()
        previous = self._last_logged_at_by_key.get(key)
        if previous is not None and (now - previous) < float(interval_seconds):
            return False
        self._last_logged_at_by_key[key] = now
        return True


def log_exception_once(
    logger: logging.Logger,
    *,
    limiter: ErrorLogLimiter,
    key: str,
    message: str,
    interval_seconds: float = 30.0,
) -> None:
    if not limiter.should_log(key, interval_seconds=interval_seconds):
        return
    logger.exception(message)


__all__ = ["ErrorLogLimiter", "log_exception_once"]
