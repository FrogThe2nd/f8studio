from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Callable

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s"
FILE_HANDLER_NAME = "f8pystudio-file-log"
_CRASH_FILE_HANDLE: object | None = None
_PREVIOUS_SYS_EXCEPTHOOK: Callable[[type[BaseException], BaseException, TracebackType | None], object] | None = None
_PREVIOUS_THREADING_EXCEPTHOOK: Callable[[threading.ExceptHookArgs], object] | None = None


def default_process_log_dir() -> Path:
    raw = str(os.environ.get("F8_PYSTUDIO_LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()

    if sys.platform.startswith("win"):
        base = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if base:
            return Path(base) / "Feel8" / "PyStudio" / "logs"
    return Path.home() / ".f8studio" / "logs"


def configure_process_file_logging(*, log_dir: Path | None = None) -> Path:
    target_dir = log_dir if log_dir is not None else default_process_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / "pystudio.log"

    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name == FILE_HANDLER_NAME:
            return log_path

    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.name = FILE_HANDLER_NAME
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(logging.NOTSET)
    root.addHandler(handler)
    return log_path


def enable_process_crash_dump(*, log_dir: Path | None = None) -> Path:
    global _CRASH_FILE_HANDLE

    target_dir = log_dir if log_dir is not None else default_process_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    crash_path = target_dir / "pystudio-crash.log"

    if _CRASH_FILE_HANDLE is None:
        _CRASH_FILE_HANDLE = crash_path.open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_CRASH_FILE_HANDLE, all_threads=True)
    return crash_path


def install_uncaught_exception_logging() -> None:
    global _PREVIOUS_SYS_EXCEPTHOOK
    global _PREVIOUS_THREADING_EXCEPTHOOK

    logger = logging.getLogger("f8pystudio.diagnostics.uncaught")
    if _PREVIOUS_SYS_EXCEPTHOOK is None:
        _PREVIOUS_SYS_EXCEPTHOOK = sys.excepthook

        def _sys_excepthook(
            exc_type: type[BaseException],
            exc: BaseException,
            tb: TracebackType | None,
        ) -> None:
            logger.critical("Uncaught exception on main thread", exc_info=(exc_type, exc, tb))
            if _PREVIOUS_SYS_EXCEPTHOOK is not None:
                _PREVIOUS_SYS_EXCEPTHOOK(exc_type, exc, tb)

        sys.excepthook = _sys_excepthook

    if _PREVIOUS_THREADING_EXCEPTHOOK is None:
        _PREVIOUS_THREADING_EXCEPTHOOK = threading.excepthook

        def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
            logger.critical(
                "Uncaught exception on thread %s",
                args.thread.name if args.thread is not None else "<unknown>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            if _PREVIOUS_THREADING_EXCEPTHOOK is not None:
                _PREVIOUS_THREADING_EXCEPTHOOK(args)

        threading.excepthook = _threading_excepthook


def install_process_diagnostics() -> tuple[Path, Path]:
    log_dir = default_process_log_dir()
    log_path = configure_process_file_logging(log_dir=log_dir)
    crash_path = enable_process_crash_dump(log_dir=log_dir)
    install_uncaught_exception_logging()
    logging.getLogger(__name__).info("PyStudio process diagnostics enabled log=%s crash=%s", log_path, crash_path)
    return log_path, crash_path


__all__ = [
    "FILE_HANDLER_NAME",
    "configure_process_file_logging",
    "default_process_log_dir",
    "enable_process_crash_dump",
    "install_process_diagnostics",
    "install_uncaught_exception_logging",
]
