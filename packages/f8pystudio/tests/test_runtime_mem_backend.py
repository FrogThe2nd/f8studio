from __future__ import annotations

import asyncio

from f8pysdk.codec import encode_obj
from f8pysdk.f8_naming import kv_key_node_state
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
            bus_backend="mem",
            client_service_id="studio",
        )
    )

    assert isinstance(gateway._build_transport("engine"), InMemoryTransport)


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
        watcher._on_kv(
            "engine",
            kv_key_node_state(node_id="camera", field="videoKey"),
            encode_obj({"value": "f8/svc/engine/nodes/camera/data/video", "ts": 1_700_000_001_234}),
        )
    )

    assert calls == [
        (
            "engine",
            "camera",
            "videoKey",
            "f8/svc/engine/nodes/camera/data/video",
            1_700_000_001_234,
        )
    ]
