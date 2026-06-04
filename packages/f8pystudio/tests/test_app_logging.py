from __future__ import annotations

import io
import logging
import sys
import threading
from pathlib import Path

from f8pystudio.diagnostics.logging import apply_root_log_level, configure_root_logging_from_env, resolve_env_log_level
from f8pystudio.diagnostics.process_logging import (
    FILE_HANDLER_NAME,
    configure_process_file_logging,
    default_process_log_dir,
    install_uncaught_exception_logging,
)
from f8pystudio.ui.support import qt_message_logging
from f8pystudio.ui.support.qt_message_logging import install_qt_message_logging


def test_resolve_env_log_level_prefers_explicit_name() -> None:
    level = resolve_env_log_level(log_level_raw=" debug ", discovery_timings_raw="0")
    assert level == logging.DEBUG


def test_resolve_env_log_level_uses_discovery_timing_flag_when_no_explicit_level() -> None:
    level = resolve_env_log_level(log_level_raw="", discovery_timings_raw="enabled")
    assert level == logging.INFO


def test_resolve_env_log_level_falls_back_to_warning_for_unknown_level() -> None:
    level = resolve_env_log_level(log_level_raw="not-a-level", discovery_timings_raw="no")
    assert level == logging.WARNING


def test_apply_root_log_level_updates_existing_handlers() -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    child_logger = logging.getLogger("f8pystudio.agents.qt_bridge")
    original_child_level = child_logger.level
    original_disabled_level = logging.root.manager.disable
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.NOTSET)
        child_logger.setLevel(logging.DEBUG)

        apply_root_log_level(logging.WARNING)
        child_logger.debug("debug should be filtered by the root handler")

        assert root_logger.level == logging.WARNING
        assert handler.level == logging.WARNING
        assert logging.root.manager.disable == logging.WARNING - 1
        assert stream.getvalue() == ""
    finally:
        logging.disable(original_disabled_level)
        child_logger.setLevel(original_child_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_apply_root_log_level_debug_reenables_low_level_records() -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_disabled_level = logging.root.manager.disable
    child_logger = logging.getLogger("f8pystudio.assets.variants.variant_sync")
    original_child_level = child_logger.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        root_logger.addHandler(handler)
        child_logger.setLevel(logging.DEBUG)

        apply_root_log_level(logging.WARNING)
        child_logger.debug("hidden debug")
        apply_root_log_level(logging.DEBUG)
        child_logger.debug("visible debug")

        assert logging.root.manager.disable == logging.NOTSET
        assert "hidden debug" not in stream.getvalue()
        assert "visible debug" in stream.getvalue()
    finally:
        logging.disable(original_disabled_level)
        child_logger.setLevel(original_child_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_configure_root_logging_from_env_updates_existing_handlers(monkeypatch) -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_disabled_level = logging.root.manager.disable
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.DEBUG)
        monkeypatch.setenv("F8_LOG_LEVEL", "warning")
        monkeypatch.delenv("F8_DISCOVERY_LOG_TIMINGS", raising=False)

        configure_root_logging_from_env()

        assert root_logger.level == logging.WARNING
        assert handler.level == logging.WARNING
        assert logging.root.manager.disable == logging.WARNING - 1
    finally:
        logging.disable(original_disabled_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_default_process_log_dir_honors_env(monkeypatch, tmp_path) -> None:
    custom_dir = tmp_path / "logs"
    monkeypatch.setenv("F8_PYSTUDIO_LOG_DIR", str(custom_dir))

    assert default_process_log_dir() == custom_dir


def test_configure_process_file_logging_installs_single_file_handler(tmp_path) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)

        log_path = configure_process_file_logging(log_dir=tmp_path)
        second_path = configure_process_file_logging(log_dir=tmp_path)
        handlers = [handler for handler in root_logger.handlers if handler.name == FILE_HANDLER_NAME]

        assert log_path == tmp_path / "pystudio.log"
        assert second_path == log_path
        assert len(handlers) == 1
        assert handlers[0].level == logging.NOTSET
    finally:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
            existing_handler.close()
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)


def test_apply_root_log_level_keeps_process_file_handler_verbose(tmp_path) -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_disabled_level = logging.root.manager.disable

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        configure_process_file_logging(log_dir=tmp_path)

        apply_root_log_level(logging.WARNING)

        handlers = [handler for handler in root_logger.handlers if handler.name == FILE_HANDLER_NAME]
        assert len(handlers) == 1
        assert handlers[0].level == logging.NOTSET
    finally:
        logging.disable(original_disabled_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
            existing_handler.close()
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_install_uncaught_exception_logging_installs_hooks() -> None:
    from f8pystudio.diagnostics import process_logging

    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook
    original_previous_sys_hook = process_logging._PREVIOUS_SYS_EXCEPTHOOK
    original_previous_thread_hook = process_logging._PREVIOUS_THREADING_EXCEPTHOOK

    try:
        process_logging._PREVIOUS_SYS_EXCEPTHOOK = None
        process_logging._PREVIOUS_THREADING_EXCEPTHOOK = None

        install_uncaught_exception_logging()

        assert sys.excepthook is not original_sys_hook
        assert threading.excepthook is not original_thread_hook
    finally:
        sys.excepthook = original_sys_hook
        threading.excepthook = original_thread_hook
        process_logging._PREVIOUS_SYS_EXCEPTHOOK = original_previous_sys_hook
        process_logging._PREVIOUS_THREADING_EXCEPTHOOK = original_previous_thread_hook


def test_install_qt_message_logging_writes_qt_warnings_to_file(tmp_path: Path) -> None:
    from qtpy import QtCore

    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_previous_qt_handler = qt_message_logging._PREVIOUS_QT_MESSAGE_HANDLER
    original_qt_installed = qt_message_logging._QT_MESSAGE_HANDLER_INSTALLED

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        root_logger.setLevel(logging.DEBUG)
        configure_process_file_logging(log_dir=tmp_path)
        qt_message_logging._PREVIOUS_QT_MESSAGE_HANDLER = None
        qt_message_logging._QT_MESSAGE_HANDLER_INSTALLED = False

        install_qt_message_logging()
        QtCore.qWarning("f8 test qt warning")
        for handler in list(root_logger.handlers):
            handler.flush()

        log_text = (tmp_path / "pystudio.log").read_text(encoding="utf-8")
        assert "Qt message: f8 test qt warning" in log_text
    finally:
        QtCore.qInstallMessageHandler(original_previous_qt_handler)
        qt_message_logging._PREVIOUS_QT_MESSAGE_HANDLER = original_previous_qt_handler
        qt_message_logging._QT_MESSAGE_HANDLER_INSTALLED = original_qt_installed
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
            existing_handler.close()
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_qt_message_level_maps_qt_enum_values() -> None:
    from qtpy import QtCore
    from f8pystudio.ui.support.qt_message_logging import _qt_message_level

    assert _qt_message_level(QtCore.QtMsgType.QtDebugMsg) == logging.DEBUG
    assert _qt_message_level(QtCore.QtMsgType.QtInfoMsg) == logging.INFO
    assert _qt_message_level(QtCore.QtMsgType.QtWarningMsg) == logging.WARNING
    assert _qt_message_level(QtCore.QtMsgType.QtCriticalMsg) == logging.ERROR
    assert _qt_message_level(QtCore.QtMsgType.QtFatalMsg) == logging.CRITICAL
