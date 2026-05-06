from __future__ import annotations

import asyncio

from f8pysdk.codec import encode_obj
from f8pysdk.testing import InMemoryTransport
from f8pysdk.zenoh_naming import zenoh_state_key
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
            bus_backend="mem",
            client_service_id="studio",
        )
    )

    assert isinstance(gateway._build_transport(), InMemoryTransport)


def test_runtime_rungraph_gateway_reuses_mem_transport_until_close() -> None:
    async def _run() -> None:
        gateway = RuntimeRungraphGateway(
            RungraphDeployConfig(
                bus_backend="mem",
                client_service_id="studio",
            )
        )

        first = await gateway.ensure_connected()
        second = await gateway.ensure_connected()
        assert first is second

        await gateway.close()
        assert gateway._transport is None

        third = await gateway.ensure_connected()
        assert third is not first
        await gateway.close()

    asyncio.run(_run())


def test_remote_state_watcher_mem_uses_in_memory_transport() -> None:
    watcher = RemoteStateWatcher(
        studio_service_id="studio",
        on_state=_noop_state,
        bus_backend="mem",
    )

    assert isinstance(watcher._tr, InMemoryTransport)


def test_remote_state_watcher_accepts_cpp_ts_field() -> None:
    calls: list[tuple[str, str, str, object, int]] = []

    async def _on_state(
        service_id: str,
        node_id: str,
        field: str,
        value: object,
        ts_ms: int,
        _meta: dict[str, object],
    ) -> None:
        calls.append((service_id, node_id, field, value, ts_ms))

    watcher = RemoteStateWatcher(
        studio_service_id="studio",
        on_state=_on_state,
        bus_backend="mem",
    )

    asyncio.run(
        watcher._on_retained_state(
            "engine",
            zenoh_state_key("engine", node_id="camera", field="statusText"),
            encode_obj({"value": "ready", "ts": 1_700_000_001_234}),
        )
    )

    assert calls == [
        (
            "engine",
            "camera",
            "statusText",
            "ready",
            1_700_000_001_234,
        )
    ]
