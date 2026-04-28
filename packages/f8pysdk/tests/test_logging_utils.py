from __future__ import annotations

import io
import logging

from f8pysdk.logging_utils import apply_root_log_level, configure_root_logging_from_env, resolve_env_log_level


def test_resolve_env_log_level_defaults_to_warning() -> None:
    assert resolve_env_log_level(log_level_raw="") == logging.WARNING


def test_apply_root_log_level_blocks_child_debug_records() -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_disabled_level = logging.root.manager.disable
    child_logger = logging.getLogger("f8pysdk.transport")
    original_child_level = child_logger.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        root_logger.addHandler(handler)
        child_logger.setLevel(logging.DEBUG)

        apply_root_log_level(logging.WARNING)
        child_logger.debug("hidden")
        child_logger.warning("visible")

        assert "hidden" not in stream.getvalue()
        assert "visible" in stream.getvalue()
        assert logging.root.manager.disable == logging.WARNING - 1
    finally:
        logging.disable(original_disabled_level)
        child_logger.setLevel(original_child_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)


def test_configure_root_logging_from_env_reenables_debug(monkeypatch) -> None:
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_disabled_level = logging.root.manager.disable

    try:
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        monkeypatch.setenv("F8_LOG_LEVEL", "DEBUG")

        configure_root_logging_from_env()

        assert root_logger.level == logging.DEBUG
        assert logging.root.manager.disable == logging.NOTSET
    finally:
        logging.disable(original_disabled_level)
        for existing_handler in list(root_logger.handlers):
            root_logger.removeHandler(existing_handler)
        for existing_handler in original_handlers:
            root_logger.addHandler(existing_handler)
        root_logger.setLevel(original_level)
