from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.runtime_node_registry import RuntimeNodeRegistry  # noqa: E402
from f8pysdk.service_cli import MonitorRuntimeOverrides, ServiceCliTemplate  # noqa: E402


class _CaptureProgram(ServiceCliTemplate):
    def __init__(self) -> None:
        self.last_service_id = ""
        self.last_nats_url = ""
        self.last_monitor_overrides: MonitorRuntimeOverrides | None = None

    @property
    def service_class(self) -> str:
        return "f8.tests.capture"

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        _ = registry

    async def run_forever(
        self,
        *,
        service_id: str,
        nats_url: str,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        self.last_service_id = str(service_id)
        self.last_nats_url = str(nats_url)
        self.last_monitor_overrides = monitor_overrides


def test_cli_monitor_overrides_from_args() -> None:
    program = _CaptureProgram()
    code = program.cli(
        [
            "--service-id",
            "svcA",
            "--nats-url",
            "nats://127.0.0.1:4222",
            "--monitor-enabled",
            "true",
            "--monitor-interval-ms",
            "250",
            "--monitor-window-ms",
            "2000",
            "--monitor-gpu-enabled",
            "false",
        ]
    )
    assert code == 0
    assert program.last_service_id == "svcA"
    assert program.last_nats_url == "nats://127.0.0.1:4222"
    assert program.last_monitor_overrides is not None
    assert program.last_monitor_overrides.enabled is True
    assert program.last_monitor_overrides.interval_ms == 250
    assert program.last_monitor_overrides.window_ms == 2000
    assert program.last_monitor_overrides.gpu_enabled is False


def test_cli_monitor_overrides_from_env(monkeypatch) -> None:
    monkeypatch.setenv("F8_MONITOR_ENABLED", "0")
    monkeypatch.setenv("F8_MONITOR_INTERVAL_MS", "1600")
    monkeypatch.setenv("F8_MONITOR_WINDOW_MS", "45000")
    monkeypatch.setenv("F8_MONITOR_GPU_ENABLED", "0")

    program = _CaptureProgram()
    code = program.cli(["--service-id", "svcB", "--nats-url", "nats://127.0.0.1:4222"])
    assert code == 0
    assert program.last_monitor_overrides is not None
    assert program.last_monitor_overrides.enabled is False
    assert program.last_monitor_overrides.interval_ms == 1600
    assert program.last_monitor_overrides.window_ms == 45000
    assert program.last_monitor_overrides.gpu_enabled is False


def test_cli_monitor_overrides_clamp_lower_bounds() -> None:
    program = _CaptureProgram()
    code = program.cli(
        [
            "--service-id",
            "svcC",
            "--nats-url",
            "nats://127.0.0.1:4222",
            "--monitor-interval-ms",
            "1",
            "--monitor-window-ms",
            "10",
        ]
    )
    assert code == 0
    assert program.last_monitor_overrides is not None
    assert program.last_monitor_overrides.interval_ms == 200
    assert program.last_monitor_overrides.window_ms == 1000
