from __future__ import annotations

import asyncio

from f8pysdk.codec import encode_obj
from f8pysdk.testing import InMemoryTransport
from f8pysdk.zenoh_naming import zenoh_state_key
from f8pystudio.bridge.command_client import RuntimeCommandGatewayConfig
from f8pystudio.bridge.command_client import _build_runtime_transport as build_command_transport
from f8pystudio.bridge.remote_state_watcher import RemoteStateWatcher
from f8pystudio.bridge.rungraph_deployer import RungraphDeployConfig, RuntimeRungraphGateway
from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge, PyStudioServiceBridgeConfig
from f8pystudio.bridge.studio_bridge import RUNGRAPH_ENDPOINT_READY_TIMEOUT_S


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


def test_studio_bridge_uses_longer_rungraph_endpoint_ready_timeout_for_cold_start() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig(bus_backend="mem"))
    try:
        config = bridge._build_rungraph_config()
    finally:
        bridge._process_actions.close()

    assert config.endpoint_ready_timeout_s == RUNGRAPH_ENDPOINT_READY_TIMEOUT_S


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


class _SlowConnectTransport:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        await asyncio.sleep(0.01)

    async def close(self) -> None:
        self.close_calls += 1

    async def publish(self, key: str, payload: bytes) -> None:
        _ = (key, payload)

    async def subscribe(self, key_expr: str, *, queue: str | None = None, cb: object | None = None) -> object:
        _ = (key_expr, queue, cb)
        return object()

    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        _ = (key, payload, timeout, raise_on_error)
        return None

    async def serve(self, key: str, handler: object) -> object:
        _ = (key, handler)
        return object()

    async def retained_put(self, key: str, value: bytes) -> None:
        _ = (key, value)

    async def retained_get(self, key: str) -> bytes | None:
        _ = key
        return None

    async def retained_watch(self, key_expr: str, *, cb: object, with_initial: bool = True) -> object:
        _ = (key_expr, cb, with_initial)
        return object()


class _ConnectCountingRungraphGateway(RuntimeRungraphGateway):
    def __init__(self) -> None:
        super().__init__(RungraphDeployConfig(bus_backend="mem", client_service_id="studio"))
        self.created_transports: list[_SlowConnectTransport] = []

    def _build_transport(self) -> _SlowConnectTransport:
        transport = _SlowConnectTransport()
        self.created_transports.append(transport)
        return transport


def test_runtime_rungraph_gateway_serializes_concurrent_connects() -> None:
    async def _run() -> None:
        gateway = _ConnectCountingRungraphGateway()

        first, second = await asyncio.gather(gateway.ensure_connected(), gateway.ensure_connected())

        assert first is second
        assert len(gateway.created_transports) == 1
        assert gateway.created_transports[0].connect_calls == 1
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
