from __future__ import annotations

import asyncio
from typing import Any

from f8pysdk.bus import ServiceBus, ServiceBusConfig
from f8pysdk.f8_naming import data_key, new_id, state_path_node_field, svc_endpoint_key
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.testing import InMemoryCluster, InMemoryTransport
from f8pysdk.zenoh_config import apply_zenoh_shared_memory_config
from f8pysdk.zenoh_naming import zenoh_key_to_state_path, zenoh_state_key, zenoh_state_path_pattern
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig


class _FakeZenohConfigError(Exception):
    pass


class _FakeZenohModule:
    ZError = _FakeZenohConfigError


class _FakeZenohConfig:
    def __init__(self, rejected_keys: set[str] | None = None) -> None:
        self.rejected_keys = rejected_keys or set()
        self.values: dict[str, str] = {}

    def insert_json5(self, key: str, value: str) -> None:
        if key in self.rejected_keys:
            raise _FakeZenohConfigError(key)
        self.values[key] = value


def _sid(prefix: str) -> str:
    return f"{prefix}_{new_id()[:10]}"


def test_service_bus_config_defaults_to_zenoh_backend() -> None:
    cfg = ServiceBusConfig(service_id="svc")

    assert cfg.normalized().bus_backend == "zenoh"

    bus = ServiceBus(cfg)
    assert bus.bus_backend == "zenoh"


def test_runtime_transport_protocol_is_explicitly_satisfied() -> None:
    mem_transport = InMemoryTransport(cluster=InMemoryCluster())
    zenoh_transport = ZenohTransport(ZenohTransportConfig(service_id="svc_demo"))

    assert isinstance(mem_transport, RuntimeTransport)
    assert isinstance(zenoh_transport, RuntimeTransport)


def test_zenoh_key_mapping_preserves_dotted_state_fields() -> None:
    key = state_path_node_field(node_id="node", field="hidden.command.output.value")
    zenoh_key = data_key("demo", from_node_id="node", port_id="out")

    assert zenoh_key == "f8/svc/demo/nodes/node/data/out"
    assert zenoh_key_to_state_path("f8/svc/demo/state/nodes/node/state/hidden/command/output/value") == key


def test_zenoh_state_path_pattern_maps_node_state_wildcards_to_state_keyspace() -> None:
    assert zenoh_state_path_pattern("svcA", "nodes.>") == "f8/svc/svcA/state/nodes/**"
    assert zenoh_state_path_pattern("svcA", "nodes.node.>") == "f8/svc/svcA/state/nodes/node/**"
    assert zenoh_state_path_pattern("svcA", "nodes.node.state.>") == "f8/svc/svcA/state/nodes/node/state/**"


def test_zenoh_transport_puts_use_latest_drop_qos() -> None:
    import zenoh  # type: ignore[import-not-found]

    class _FakeRetainedPublisher:
        def __init__(self) -> None:
            self.put_calls: list[bytes] = []

        def put(self, payload: bytes) -> None:
            self.put_calls.append(bytes(payload))

    class _FakeSession:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, bytes, dict[str, Any]]] = []

        def put(self, key: str, payload: bytes, **kwargs: Any) -> None:
            self.put_calls.append((key, bytes(payload), dict(kwargs)))

    async def _run() -> None:
        session = _FakeSession()
        retained_publisher = _FakeRetainedPublisher()
        transport = ZenohTransport(ZenohTransportConfig(service_id="svc_demo"))
        transport._session = session

        key = zenoh_state_key("svc_demo", node_id="node", field="value")
        transport._state_publishers[key] = retained_publisher
        await transport.publish(data_key("svc_demo", from_node_id="node", port_id="out"), b"payload")
        await transport.retained_put(key, b"state")

        assert session.put_calls == [
            (
                "f8/svc/svc_demo/nodes/node/data/out",
                b"payload",
                {
                    "congestion_control": zenoh.CongestionControl.DROP,
                    "priority": zenoh.Priority.REAL_TIME,
                    "express": True,
                },
            ),
        ]
        assert retained_publisher.put_calls == [b"state"]
        assert await transport.retained_get(key) == b"state"

    asyncio.run(_run())


def test_zenoh_shared_memory_config_writes_current_and_legacy_pool_keys() -> None:
    config = _FakeZenohConfig()

    apply_zenoh_shared_memory_config(
        config,
        zenoh_module=_FakeZenohModule,
        shm_pool_bytes=123,
        log_context="test",
    )

    assert config.values == {
        "transport/shared_memory/enabled": "true",
        "transport/shared_memory/mode": '"init"',
        "transport/shared_memory/transport_optimization/enabled": "true",
        "transport/shared_memory/transport_optimization/pool_size": "123",
        "transport/shared_memory/pool_size": "123",
    }


def test_zenoh_shared_memory_config_tolerates_missing_optional_pool_key() -> None:
    legacy_key = "transport/shared_memory/pool_size"
    config = _FakeZenohConfig(rejected_keys={legacy_key})

    apply_zenoh_shared_memory_config(
        config,
        zenoh_module=_FakeZenohModule,
        shm_pool_bytes=456,
        log_context="test",
    )

    assert config.values["transport/shared_memory/transport_optimization/pool_size"] == "456"
    assert legacy_key not in config.values


def test_zenoh_transport_state_watch_get_and_request_roundtrip() -> None:
    async def _run() -> None:
        service_a = _sid("svcA")
        service_b = _sid("svcB")
        a = ZenohTransport(ZenohTransportConfig(service_id=service_a))
        b = ZenohTransport(ZenohTransportConfig(service_id=service_b))
        await a.connect()
        await b.connect()
        try:
            seen: list[tuple[str, bytes]] = []

            async def _on_state(key: str, value: bytes) -> None:
                seen.append((key, value))

            pattern = zenoh_state_path_pattern(service_a, "nodes.node.state.>")
            watch = await b.retained_watch(pattern, cb=_on_state)
            key = zenoh_state_key(service_a, node_id="node", field="hidden.output.value")
            await a.retained_put(key, b"state-bytes")

            for _ in range(1000):
                if seen:
                    break
                await asyncio.sleep(0.001)
            assert seen == [(key, b"state-bytes")]
            assert await b.retained_get(key) is None

            late_seen: list[tuple[str, bytes]] = []

            async def _on_late_state(key: str, value: bytes) -> None:
                late_seen.append((key, value))

            late_watch = await b.retained_watch(key, cb=_on_late_state)
            for _ in range(1000):
                if late_seen:
                    break
                await asyncio.sleep(0.001)
            assert late_seen == [(key, b"state-bytes")]
            await late_watch.unsubscribe()

            async def _handler(payload: bytes) -> bytes:
                return b"echo:" + bytes(payload)

            endpoint = await a.serve(svc_endpoint_key(service_a, "status"), _handler)
            try:
                response = await b.request(
                    svc_endpoint_key(service_a, "status"),
                    b"payload",
                    timeout=1.0,
                    raise_on_error=True,
                )
                assert response == b"echo:payload"
            finally:
                await endpoint.unsubscribe()
            await watch.unsubscribe()
        finally:
            await b.close()
            await a.close()

    asyncio.run(_run())
