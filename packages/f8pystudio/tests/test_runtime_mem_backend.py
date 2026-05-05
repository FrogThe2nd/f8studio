from __future__ import annotations

from f8pysdk.testing import InMemoryTransport
from f8pystudio.bridge.command_client import RuntimeCommandGatewayConfig
from f8pystudio.bridge.command_client import _build_runtime_transport as build_command_transport
from f8pystudio.bridge.remote_state_watcher import RemoteStateWatcher
from f8pystudio.bridge.rungraph_deployer import RungraphDeployConfig, RuntimeRungraphGateway


async def _noop_state(*_args: object) -> None:
    return None


def test_runtime_command_gateway_mem_uses_in_memory_transport() -> None:
    transport = build_command_transport(
        RuntimeCommandGatewayConfig(
            bus_backend="mem",
            client_service_id="studio",
        )
    )

    assert isinstance(transport, InMemoryTransport)


def test_runtime_rungraph_gateway_mem_uses_in_memory_transport() -> None:
    gateway = RuntimeRungraphGateway(
        RungraphDeployConfig(
            nats_url="nats://127.0.0.1:4222",
            bus_backend="mem",
            client_service_id="studio",
        )
    )

    assert isinstance(gateway._build_transport("engine"), InMemoryTransport)


def test_remote_state_watcher_mem_uses_in_memory_transport() -> None:
    watcher = RemoteStateWatcher(
        nats_url="nats://127.0.0.1:4222",
        studio_service_id="studio",
        on_state=_noop_state,
        bus_backend="mem",
    )

    assert isinstance(watcher._tr, InMemoryTransport)
