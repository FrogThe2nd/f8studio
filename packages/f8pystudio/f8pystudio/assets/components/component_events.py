from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_SUBSCRIBERS: list[Callable[[], None]] = []


def subscribe_components_changed(callback: Callable[[], None]) -> Callable[[], None]:
    _SUBSCRIBERS.append(callback)

    def unsubscribe() -> None:
        if callback in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(callback)

    return unsubscribe


def emit_components_changed() -> None:
    for callback in list(_SUBSCRIBERS):
        try:
            callback()
        except Exception:
            logger.exception("Unhandled components_changed subscriber error")
