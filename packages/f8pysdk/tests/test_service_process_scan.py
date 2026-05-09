from __future__ import annotations

from f8pysdk.service_runtime_tools.deploy.process_manager import _cmdline_matches_service_id


def test_cmdline_matches_split_service_id_arg() -> None:
    assert _cmdline_matches_service_id(("pixi", "run", "svc", "--service-id", "engine"), "engine") is True


def test_cmdline_matches_equals_service_id_arg() -> None:
    assert _cmdline_matches_service_id(("python", "-m", "svc", "--service-id=engine"), "engine") is True


def test_cmdline_does_not_match_partial_service_id() -> None:
    assert _cmdline_matches_service_id(("python", "-m", "svc", "--service-id", "engine_old"), "engine") is False
    assert _cmdline_matches_service_id(("python", "-m", "svc", "--service-id=engine_old"), "engine") is False


def test_cmdline_does_not_match_unrelated_tokens() -> None:
    assert _cmdline_matches_service_id(("python", "-m", "svc", "--not-service-id", "engine"), "engine") is False
    assert _cmdline_matches_service_id(("python", "-m", "svc", "service-id=engine"), "engine") is False
