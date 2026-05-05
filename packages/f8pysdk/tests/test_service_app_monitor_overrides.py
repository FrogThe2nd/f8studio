from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.app import MonitorRuntimeOverrides, ServiceApp  # noqa: E402
from f8pysdk.nodes import ServiceNode  # noqa: E402
from f8pysdk.registry import (  # noqa: E402
    create_runtime_node_registry,
    OperatorAlreadyRegistered,
    Registry,
    RuntimeNodeRegistry,
)
from f8pysdk.specs import F8ServiceSpec  # noqa: E402


class _CaptureApp(ServiceApp):
    def __init__(self) -> None:
        super().__init__(service_class="f8.tests.capture", registry=Registry())
        self.last_service_id = ""
        self.last_monitor_overrides: MonitorRuntimeOverrides | None = None

    def run(
        self,
        *,
        service_id: str,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        self.last_service_id = str(service_id)
        self.last_monitor_overrides = monitor_overrides


def _build_describe_app(*, shared: bool = False) -> ServiceApp:
    registry = ServiceApp.build_shared_registry() if shared else Registry()
    registry.register_service_spec(F8ServiceSpec(serviceClass="f8.tests.describe", label="Describe Test"))
    registry.register_service_factory(
        "f8.tests.describe",
        lambda node_id, node, initial_state: ServiceNode(node_id=node_id),
    )
    return ServiceApp(service_class="f8.tests.describe", registry=registry)


def test_cli_monitor_overrides_from_args() -> None:
    app = _CaptureApp()
    code = app.cli(
        [
            "--service-id",
            "svcA",
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
    assert app.last_service_id == "svcA"
    assert app.last_monitor_overrides is not None
    assert app.last_monitor_overrides.enabled is True
    assert app.last_monitor_overrides.interval_ms == 250
    assert app.last_monitor_overrides.window_ms == 2000
    assert app.last_monitor_overrides.gpu_enabled is False


def test_cli_monitor_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("F8_MONITOR_ENABLED", "0")
    monkeypatch.setenv("F8_MONITOR_INTERVAL_MS", "1600")
    monkeypatch.setenv("F8_MONITOR_WINDOW_MS", "45000")
    monkeypatch.setenv("F8_MONITOR_GPU_ENABLED", "0")

    app = _CaptureApp()
    code = app.cli(["--service-id", "svcB"])
    assert code == 0
    assert app.last_monitor_overrides is not None
    assert app.last_monitor_overrides.enabled is False
    assert app.last_monitor_overrides.interval_ms == 1600
    assert app.last_monitor_overrides.window_ms == 45000
    assert app.last_monitor_overrides.gpu_enabled is False


def test_cli_monitor_overrides_clamp_lower_bounds() -> None:
    app = _CaptureApp()
    code = app.cli(
        [
            "--service-id",
            "svcC",
            "--monitor-interval-ms",
            "1",
            "--monitor-window-ms",
            "10",
        ]
    )
    assert code == 0
    assert app.last_monitor_overrides is not None
    assert app.last_monitor_overrides.interval_ms == 200
    assert app.last_monitor_overrides.window_ms == 1000


def test_registry_helpers_return_fresh_runtime_registries_by_default() -> None:
    app_a = _build_describe_app()
    app_b = _build_describe_app()

    assert app_a.runtime_registry is not app_b.runtime_registry


def test_describe_json_uses_explicit_fresh_registry_owner() -> None:
    payload_a = _build_describe_app().describe_json()
    payload_b = _build_describe_app().describe_json()

    assert payload_a["service"]["serviceClass"] == "f8.tests.describe"
    assert payload_b["service"]["serviceClass"] == "f8.tests.describe"


def test_build_shared_registry_is_explicit_opt_in() -> None:
    reg_a = ServiceApp.build_shared_registry()
    reg_b = ServiceApp.build_shared_registry()

    assert reg_a.runtime_registry is reg_b.runtime_registry


def test_shared_registry_opt_in_preserves_process_global_behavior() -> None:
    original_instance = RuntimeNodeRegistry._instance
    RuntimeNodeRegistry._instance = create_runtime_node_registry()
    try:
        payload = _build_describe_app(shared=True).describe_json()
        assert payload["service"]["serviceClass"] == "f8.tests.describe"
        with pytest.raises(OperatorAlreadyRegistered):
            _build_describe_app(shared=True)
    finally:
        RuntimeNodeRegistry._instance = original_instance
