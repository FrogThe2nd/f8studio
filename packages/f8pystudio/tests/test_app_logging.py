from __future__ import annotations

import io
import logging

from f8pystudio.diagnostics.logging import apply_root_log_level, configure_root_logging_from_env, resolve_env_log_level


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
    child_logger = logging.getLogger("f8pystudio.ai_assist.llm_bridge")
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
