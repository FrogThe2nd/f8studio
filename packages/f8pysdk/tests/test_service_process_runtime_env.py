from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from f8pysdk.service_runtime_tools.deploy import ServiceProcessConfig, ServiceProcessManager


class _Catalog:
    def __init__(self, entry_path: Path) -> None:
        self.entry_path = entry_path

    def service_entry_path(self, service_class: str) -> Path | None:
        if service_class == "f8.test":
            return self.entry_path
        return None


class _FakeProcess:
    pid = 4242
    stdout = None

    def poll(self) -> int | None:
        return None


def _write_service_entry(tmp_path: Path) -> Path:
    service_dir = tmp_path / "svc"
    service_dir.mkdir()
    entry_path = service_dir / "service.yml"
    entry_path.write_text(
        "\n".join(
            [
                "schemaVersion: f8serviceEntry/1",
                "serviceClass: f8.test",
                "label: Test Service",
                "version: 0.0.1",
                "launch:",
                "  command: python",
                "  args: ['-m', 'f8_test_service']",
                "  env:",
                "    F8_ZENOH_CONNECT: tcp/launch-stale",
                "  workdir: .",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return entry_path


def test_process_manager_zenoh_env_installs_zenoh_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry_path = _write_service_entry(tmp_path)
    captured: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProcess:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    manager = ServiceProcessManager(_Catalog(entry_path))
    manager.start(
        ServiceProcessConfig(
            service_class="f8.test",
            service_id="svc1",
            bus_backend="zenoh",
            zenoh_config_path="/tmp/zenoh.json5",
            zenoh_connect=("tcp/127.0.0.1:7447",),
            zenoh_listen=("tcp/0.0.0.0:0",),
            zenoh_shm_pool_bytes=123456,
            purge_kv_bucket_on_start=False,
        )
    )

    cmd = captured["cmd"]
    env = captured["env"]
    assert "--bus-backend" in cmd
    assert cmd[cmd.index("--bus-backend") + 1] == "zenoh"
    assert env["F8_BUS_BACKEND"] == "zenoh"
    assert env["F8_ZENOH_CONFIG"] == "/tmp/zenoh.json5"
    assert env["F8_ZENOH_CONNECT"] == "tcp/127.0.0.1:7447"
    assert env["F8_ZENOH_LISTEN"] == "tcp/0.0.0.0:0"
    assert env["F8_ZENOH_SHM_POOL_BYTES"] == "123456"


def test_process_manager_rejects_invalid_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry_path = _write_service_entry(tmp_path)

    manager = ServiceProcessManager(_Catalog(entry_path))
    with pytest.raises(ValueError, match="Invalid process bus_backend"):
        manager.start(
            ServiceProcessConfig(
                service_class="f8.test",
                service_id="svc1",
                bus_backend="invalid",
                purge_kv_bucket_on_start=False,
            )
        )


def test_process_manager_mem_env_clears_transport_specific_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry_path = _write_service_entry(tmp_path)
    captured: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProcess:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _FakeProcess()

    monkeypatch.setenv("F8_ZENOH_CONFIG", "/tmp/stale.json5")
    monkeypatch.setenv("F8_ZENOH_CONNECT", "tcp/parent-stale")
    monkeypatch.setenv("F8_ZENOH_LISTEN", "tcp/parent-listen-stale")
    monkeypatch.setenv("F8_ZENOH_SHM_POOL_BYTES", "999")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    manager = ServiceProcessManager(_Catalog(entry_path))
    manager.start(
        ServiceProcessConfig(
            service_class="f8.test",
            service_id="svc1",
            bus_backend="mem",
            zenoh_config_path="/tmp/ignored.json5",
            zenoh_connect=("tcp/ignored",),
            zenoh_listen=("tcp/ignored-listen",),
            zenoh_shm_pool_bytes=123456,
            purge_kv_bucket_on_start=False,
        )
    )

    cmd = captured["cmd"]
    env = captured["env"]
    assert "--bus-backend" in cmd
    assert cmd[cmd.index("--bus-backend") + 1] == "mem"
    assert "--zenoh-config" not in cmd
    assert "--zenoh-connect" not in cmd
    assert "--zenoh-listen" not in cmd
    assert "--zenoh-shm-pool-bytes" not in cmd
    assert env["F8_BUS_BACKEND"] == "mem"
    assert "F8_ZENOH_CONFIG" not in env
    assert "F8_ZENOH_CONNECT" not in env
    assert "F8_ZENOH_LISTEN" not in env
    assert "F8_ZENOH_SHM_POOL_BYTES" not in env
