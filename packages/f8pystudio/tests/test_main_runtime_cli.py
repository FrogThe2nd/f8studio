from __future__ import annotations

import os

import pytest

import f8pystudio.main as studio_main
from f8pysdk.service_bus.config import DEFAULT_ZENOH_SHM_POOL_BYTES
from f8pystudio.app import program as program_module


class _FakeProgram:
    last_config: object | None = None
    run_called: bool = False
    describe_called: bool = False

    def __init__(self, bridge_config: object | None = None) -> None:
        self.bridge_config = bridge_config
        _FakeProgram.last_config = bridge_config

    def run(self) -> int:
        _FakeProgram.run_called = True
        return 17

    def describe_json_text(self) -> str:
        _FakeProgram.describe_called = True
        return '{"serviceClass":"f8.pystudio"}'


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "F8_BUS_BACKEND",
        "F8_NATS_URL",
        "F8_ZENOH_CONFIG",
        "F8_ZENOH_CONNECT",
        "F8_ZENOH_LISTEN",
        "F8_ZENOH_SHM_POOL_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def _install_fake_program(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeProgram.last_config = None
    _FakeProgram.run_called = False
    _FakeProgram.describe_called = False
    monkeypatch.setattr(program_module, "PyStudioProgram", _FakeProgram)


def test_main_defaults_to_zenoh_and_installs_zenoh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main([])

    assert exit_code == 17
    assert _FakeProgram.run_called is True
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "zenoh"
    assert cfg.nats_url == "nats://127.0.0.1:4222"
    assert cfg.zenoh_config_path is None
    assert cfg.zenoh_connect == ()
    assert cfg.zenoh_listen == ()
    assert cfg.zenoh_shm_pool_bytes == DEFAULT_ZENOH_SHM_POOL_BYTES
    assert os.environ["F8_BUS_BACKEND"] == "zenoh"
    assert "F8_NATS_URL" not in os.environ
    assert os.environ["F8_ZENOH_SHM_POOL_BYTES"] == str(DEFAULT_ZENOH_SHM_POOL_BYTES)


def test_main_parses_zenoh_cli_and_repeated_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main(
        [
            "--zenoh-config",
            "/tmp/zenoh.json5",
            "--zenoh-connect",
            "tcp/127.0.0.1:7447,tcp/127.0.0.1:7448",
            "--zenoh-connect",
            "tcp/127.0.0.1:7449",
            "--zenoh-listen",
            "tcp/0.0.0.0:0",
            "--zenoh-shm-pool-bytes",
            "123456",
        ]
    )

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "zenoh"
    assert cfg.zenoh_config_path == "/tmp/zenoh.json5"
    assert cfg.zenoh_connect == ("tcp/127.0.0.1:7447", "tcp/127.0.0.1:7448", "tcp/127.0.0.1:7449")
    assert cfg.zenoh_listen == ("tcp/0.0.0.0:0",)
    assert cfg.zenoh_shm_pool_bytes == 123456
    assert os.environ["F8_ZENOH_CONFIG"] == "/tmp/zenoh.json5"
    assert os.environ["F8_ZENOH_CONNECT"] == "tcp/127.0.0.1:7447,tcp/127.0.0.1:7448,tcp/127.0.0.1:7449"
    assert os.environ["F8_ZENOH_LISTEN"] == "tcp/0.0.0.0:0"
    assert os.environ["F8_ZENOH_SHM_POOL_BYTES"] == "123456"


def test_main_requires_explicit_nats_backend_for_nats_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    with pytest.warns(DeprecationWarning, match="ignored"):
        exit_code = studio_main.main(["--nats-url", "nats://example.invalid:4222"])

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "zenoh"
    assert cfg.nats_url == "nats://example.invalid:4222"
    assert "F8_NATS_URL" not in os.environ


def test_main_nats_backend_installs_only_nats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("F8_ZENOH_CONNECT", "tcp/stale")
    monkeypatch.setenv("F8_ZENOH_SHM_POOL_BYTES", "999")
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main(["--bus-backend", "nats", "--nats-url", "nats://127.0.0.1:4333"])

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "nats"
    assert cfg.nats_url == "nats://127.0.0.1:4333"
    assert os.environ["F8_BUS_BACKEND"] == "nats"
    assert os.environ["F8_NATS_URL"] == "nats://127.0.0.1:4333"
    assert "F8_ZENOH_CONFIG" not in os.environ
    assert "F8_ZENOH_CONNECT" not in os.environ
    assert "F8_ZENOH_LISTEN" not in os.environ
    assert "F8_ZENOH_SHM_POOL_BYTES" not in os.environ


def test_main_mem_backend_clears_transport_specific_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("F8_NATS_URL", "nats://stale.invalid:4222")
    monkeypatch.setenv("F8_ZENOH_CONNECT", "tcp/stale")
    monkeypatch.setenv("F8_ZENOH_SHM_POOL_BYTES", "999")
    _install_fake_program(monkeypatch)

    with pytest.warns(DeprecationWarning, match="ignored"):
        exit_code = studio_main.main(["--bus-backend", "mem"])

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "mem"
    assert os.environ["F8_BUS_BACKEND"] == "mem"
    assert "F8_NATS_URL" not in os.environ
    assert "F8_ZENOH_CONFIG" not in os.environ
    assert "F8_ZENOH_CONNECT" not in os.environ
    assert "F8_ZENOH_LISTEN" not in os.environ
    assert "F8_ZENOH_SHM_POOL_BYTES" not in os.environ


def test_main_describe_uses_configured_program_without_running(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main(["--describe"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == '{"serviceClass":"f8.pystudio"}'
    assert _FakeProgram.describe_called is True
    assert _FakeProgram.run_called is False
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "zenoh"
