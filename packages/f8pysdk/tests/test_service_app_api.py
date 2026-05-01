from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.app import ServiceApp, ServiceAppDefaults  # noqa: E402
from f8pysdk.nodes import OperatorNode, ServiceNode  # noqa: E402
from f8pysdk.registry import Registry  # noqa: E402
from f8pysdk.specs import F8OperatorSpec, F8RuntimeNode, F8ServiceSpec  # noqa: E402


class _DemoServiceNode(ServiceNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(node_id=node_id)
        self.snapshot = node
        self.initial_state = dict(initial_state or {})


class _DemoOperatorNode(OperatorNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(node_id=node_id)
        self.snapshot = node
        self.initial_state = dict(initial_state or {})

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id
        _ = in_port
        return []


def _build_registry() -> Registry:
    registry = Registry()
    registry.register_service(
        F8ServiceSpec(serviceClass="svc.demo", label="Demo Service"),
        _DemoServiceNode,
    )
    registry.register_operator(
        F8OperatorSpec(serviceClass="svc.demo", operatorClass="svc.demo.operator", label="Demo Operator"),
        _DemoOperatorNode,
    )
    return registry


def test_registry_registers_specs_and_runtime_types_together() -> None:
    registry = _build_registry()

    describe = registry.describe("svc.demo")
    assert describe.service.serviceClass == "svc.demo"
    assert [str(item.operatorClass) for item in describe.operators] == ["svc.demo.operator"]

    service_node = registry.create_service_node(service_class="svc.demo", node_id="svc-a", initial_state={"x": 1})
    assert isinstance(service_node, _DemoServiceNode)
    assert service_node.initial_state == {"x": 1}

    operator_node = registry.create_operator_node(
        node_id="op-1",
        node=F8RuntimeNode(
            nodeId="op-1",
            serviceId="svc-a",
            serviceClass="svc.demo",
            operatorClass="svc.demo.operator",
        ),
        initial_state={"enabled": True},
    )
    assert isinstance(operator_node, _DemoOperatorNode)
    assert operator_node.initial_state == {"enabled": True}


def test_service_app_describe_and_build_runtime_use_explicit_registry_owner() -> None:
    registry = _build_registry()
    app = ServiceApp(
        service_class="svc.demo",
        registry=registry,
        defaults=ServiceAppDefaults(data_delivery="callback"),
    )

    payload = app.describe_json()
    assert payload["service"]["serviceClass"] == "svc.demo"

    runtime_config = app.build_runtime_config(service_id="svc-a")
    assert runtime_config.service_id == "svc-a"
    assert runtime_config.service_class == "svc.demo"
    assert runtime_config.bus.data_delivery == "callback"

    runtime = app.build_runtime(service_id="svc-a")
    assert runtime._registry is registry.runtime_registry


def test_service_app_run_async_calls_setup_and_teardown_hooks() -> None:
    events: list[str] = []

    class _FakeBus:
        async def wait_terminate(self) -> None:
            events.append("wait")

    class _FakeRuntime:
        def __init__(self) -> None:
            self.bus = _FakeBus()

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    async def _setup(_runtime: object) -> None:
        events.append("setup")

    async def _teardown(_runtime: object) -> None:
        events.append("teardown")

    app = ServiceApp(service_class="svc.demo", setup=_setup, teardown=_teardown)
    fake_runtime = _FakeRuntime()
    app.build_runtime = lambda **kwargs: fake_runtime  # type: ignore[method-assign]

    asyncio.run(app.run_async(service_id="svc-a"))

    assert events == ["setup", "start", "wait", "teardown", "stop"]


def test_service_app_cli_describe_uses_single_explicit_owner(capsys: pytest.CaptureFixture[str]) -> None:
    app = ServiceApp(service_class="svc.demo", registry=_build_registry())

    code = app.cli(["--describe"])

    captured = capsys.readouterr()
    assert code == 0
    assert '"serviceClass": "svc.demo"' in captured.out
