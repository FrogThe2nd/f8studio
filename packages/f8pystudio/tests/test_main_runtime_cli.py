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
    assert cfg.supervision_mode == "studio_owned"
    assert cfg.kill_managed_services_on_exit is True
    assert cfg.zenoh_config_path is None
    assert cfg.zenoh_connect == ()
    assert cfg.zenoh_listen == ()
    assert cfg.zenoh_shm_pool_bytes == DEFAULT_ZENOH_SHM_POOL_BYTES
    assert os.environ["F8_BUS_BACKEND"] == "zenoh"
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
    assert cfg.supervision_mode == "studio_owned"
    assert cfg.kill_managed_services_on_exit is True
    assert cfg.zenoh_config_path == "/tmp/zenoh.json5"
    assert cfg.zenoh_connect == ("tcp/127.0.0.1:7447", "tcp/127.0.0.1:7448", "tcp/127.0.0.1:7449")
    assert cfg.zenoh_listen == ("tcp/0.0.0.0:0",)
    assert cfg.zenoh_shm_pool_bytes == 123456
    assert os.environ["F8_ZENOH_CONFIG"] == "/tmp/zenoh.json5"
    assert os.environ["F8_ZENOH_CONNECT"] == "tcp/127.0.0.1:7447,tcp/127.0.0.1:7448,tcp/127.0.0.1:7449"
    assert os.environ["F8_ZENOH_LISTEN"] == "tcp/0.0.0.0:0"
    assert os.environ["F8_ZENOH_SHM_POOL_BYTES"] == "123456"


def test_main_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("F8_ZENOH_CONNECT", "tcp/stale")
    monkeypatch.setenv("F8_ZENOH_SHM_POOL_BYTES", "999")
    _install_fake_program(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        studio_main.main(["--bus-backend", "unknown"])

    assert exc_info.value.code == 2
    assert _FakeProgram.run_called is False
    assert "F8_BUS_BACKEND" not in os.environ


def test_main_mem_backend_clears_transport_specific_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("F8_ZENOH_CONNECT", "tcp/stale")
    monkeypatch.setenv("F8_ZENOH_SHM_POOL_BYTES", "999")
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main(["--bus-backend", "mem"])

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.bus_backend == "mem"
    assert cfg.supervision_mode == "studio_owned"
    assert os.environ["F8_BUS_BACKEND"] == "mem"
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


def test_main_no_kill_managed_services_on_exit_does_not_detach_new_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    exit_code = studio_main.main(["--no-kill-managed-services-on-exit"])

    assert exit_code == 17
    cfg = _FakeProgram.last_config
    assert cfg is not None
    assert cfg.supervision_mode == "studio_owned"
    assert cfg.kill_managed_services_on_exit is False


def test_main_force_process_exit_uses_os_exit_after_running(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExitCalled(RuntimeError):
        def __init__(self, code: int) -> None:
            super().__init__(str(code))
            self.code = int(code)

    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)

    def _fake_exit(code: int) -> None:
        raise _ExitCalled(int(code))

    monkeypatch.setattr(studio_main.os, "_exit", _fake_exit)

    with pytest.raises(_ExitCalled) as exc_info:
        studio_main.main([], force_process_exit=True)

    assert exc_info.value.code == 17
    assert _FakeProgram.run_called is True


def test_main_force_process_exit_can_be_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    _install_fake_program(monkeypatch)
    monkeypatch.setenv("F8_PYSTUDIO_FORCE_PROCESS_EXIT", "0")

    exit_code = studio_main.main([], force_process_exit=True)

    assert exit_code == 17
    assert _FakeProgram.run_called is True
