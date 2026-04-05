from __future__ import annotations

import logging

from f8pystudio.diagnostics.logging import resolve_env_log_level


def test_resolve_env_log_level_prefers_explicit_name() -> None:
    level = resolve_env_log_level(log_level_raw=" debug ", discovery_timings_raw="0")
    assert level == logging.DEBUG


def test_resolve_env_log_level_uses_discovery_timing_flag_when_no_explicit_level() -> None:
    level = resolve_env_log_level(log_level_raw="", discovery_timings_raw="enabled")
    assert level == logging.INFO


def test_resolve_env_log_level_falls_back_to_warning_for_unknown_level() -> None:
    level = resolve_env_log_level(log_level_raw="not-a-level", discovery_timings_raw="no")
    assert level == logging.WARNING
