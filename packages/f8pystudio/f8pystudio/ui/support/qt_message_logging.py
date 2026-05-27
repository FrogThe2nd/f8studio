from __future__ import annotations

import logging
from typing import Callable, Protocol


_PREVIOUS_QT_MESSAGE_HANDLER: Callable[[object, "QtMessageLogContext", object], object] | None = None
_QT_MESSAGE_HANDLER_INSTALLED = False


class QtMessageLogContext(Protocol):
    file: str | None
    line: int
    function: str | None
    category: str | None


def _qt_message_level(mode: object) -> int:
    try:
        value = int(mode)
    except (TypeError, ValueError):
        value = -1
    if value == 0:
        return logging.DEBUG
    if value == 4:
        return logging.INFO
    if value == 1:
        return logging.WARNING
    if value == 2:
        return logging.ERROR
    if value == 3:
        return logging.CRITICAL
    return logging.WARNING


def _qt_context_text(context: QtMessageLogContext) -> str:
    file_name = ""
    function = ""
    category = ""
    raw_file = context.file
    if raw_file:
        file_name = str(raw_file)
    line = int(context.line)
    raw_function = context.function
    if raw_function:
        function = str(raw_function)
    raw_category = context.category
    if raw_category:
        category = str(raw_category)

    parts: list[str] = []
    if file_name:
        location = file_name
        if line > 0:
            location = f"{location}:{line}"
        parts.append(location)
    if function:
        parts.append(function)
    if category:
        parts.append(category)
    return " ".join(parts)


def install_qt_message_logging() -> None:
    global _PREVIOUS_QT_MESSAGE_HANDLER
    global _QT_MESSAGE_HANDLER_INSTALLED

    if _QT_MESSAGE_HANDLER_INSTALLED:
        return

    try:
        from qtpy import QtCore
    except (ImportError, RuntimeError) as exc:
        logging.getLogger(__name__).debug("Qt message logging unavailable", exc_info=exc)
        return

    logger = logging.getLogger("f8pystudio.diagnostics.qt")

    def _qt_message_handler(mode: object, context: QtMessageLogContext, message: object) -> None:
        text = str(message)
        context_text = _qt_context_text(context)
        if context_text:
            logger.log(_qt_message_level(mode), "Qt message: %s (%s)", text, context_text)
        else:
            logger.log(_qt_message_level(mode), "Qt message: %s", text)
        previous = _PREVIOUS_QT_MESSAGE_HANDLER
        if previous is not None:
            try:
                previous(mode, context, message)
            except Exception as exc:
                logger.debug("previous Qt message handler failed", exc_info=exc)

    previous_handler = QtCore.qInstallMessageHandler(_qt_message_handler)
    _PREVIOUS_QT_MESSAGE_HANDLER = previous_handler
    _QT_MESSAGE_HANDLER_INSTALLED = True


__all__ = ["install_qt_message_logging"]
