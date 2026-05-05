from __future__ import annotations

import asyncio
from typing import Any

from f8pysdk.bus import ServiceBus, ServiceBusConfig
from f8pysdk.nats_naming import kv_bucket_for_service, kv_key_node_state, new_id, svc_endpoint_subject
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.transport import NatsTransport, NatsTransportConfig
from f8pysdk.testing import InMemoryCluster, InMemoryTransport
from f8pysdk.zenoh_naming import subject_to_zenoh_key, zenoh_key_to_kv_key, zenoh_kv_pattern
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig


def _sid(prefix: str) -> str:
    return f"{prefix}_{new_id()[:10]}"


def test_service_bus_config_defaults_to_zenoh_backend() -> None:
    cfg = ServiceBusConfig(service_id="svc")

    assert cfg.normalized().bus_backend == "zenoh"

    bus = ServiceBus(cfg)
    assert bus.bus_backend == "zenoh"


def test_runtime_transport_protocol_is_explicitly_satisfied() -> None:
    nats_transport = NatsTransport(NatsTransportConfig(url="nats://127.0.0.1:4222", kv_bucket="svc_demo"))
    mem_transport = InMemoryTransport(cluster=InMemoryCluster(), kv_bucket="svc_demo")
    zenoh_transport = ZenohTransport(ZenohTransportConfig(service_id="svc_demo"))

    assert isinstance(nats_transport, RuntimeTransport)
    assert isinstance(mem_transport, RuntimeTransport)
    assert isinstance(zenoh_transport, RuntimeTransport)


def test_zenoh_key_mapping_preserves_dotted_state_fields() -> None:
    key = kv_key_node_state(node_id="node", field="hidden.command.output.value")
    zenoh_key = subject_to_zenoh_key("svc.demo.nodes.node.data.out")

    assert zenoh_key == "f8/svc/demo/nodes/node/data/out"
    assert zenoh_key_to_kv_key("f8/svc/demo/state/nodes/node/state/hidden/command/output/value") == key


def test_zenoh_key_mapping_preserves_wildcard_runtime_subjects() -> None:
    assert subject_to_zenoh_key("svc.*.nodes.*.data.monitor") == "f8/svc/*/nodes/*/data/monitor"
    assert subject_to_zenoh_key("svc.*.nodes.*.data.*") == "f8/svc/*/nodes/*/data/*"
    assert subject_to_zenoh_key("svc.*.status") == "f8/svc/*/endpoint/status"
    assert subject_to_zenoh_key("svc.*.cmd") == "f8/svc/*/cmd"


def test_zenoh_kv_pattern_maps_node_state_wildcards_to_state_keyspace() -> None:
    assert zenoh_kv_pattern("svcA", "nodes.>") == "f8/svc/svcA/state/nodes/**"
    assert zenoh_kv_pattern("svcA", "nodes.node.>") == "f8/svc/svcA/state/nodes/node/**"
    assert zenoh_kv_pattern("svcA", "nodes.node.state.>") == "f8/svc/svcA/state/nodes/node/state/**"


def test_zenoh_transport_puts_use_latest_drop_qos() -> None:
    import zenoh  # type: ignore[import-not-found]

    class _FakeSession:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, bytes, dict[str, Any]]] = []

        def put(self, key: str, payload: bytes, **kwargs: Any) -> None:
            self.put_calls.append((key, bytes(payload), dict(kwargs)))

    async def _run() -> None:
        session = _FakeSession()
        transport = ZenohTransport(ZenohTransportConfig(service_id="svc_demo"))
        transport._session = session

        await transport.publish("svc.svc_demo.nodes.node.data.out", b"payload")
        await transport.kv_put(kv_key_node_state(node_id="node", field="value"), b"state")

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
            (
                "f8/svc/svc_demo/state/nodes/node/state/value",
                b"state",
                {
                    "congestion_control": zenoh.CongestionControl.DROP,
                    "priority": zenoh.Priority.REAL_TIME,
                    "express": True,
                },
            ),
        ]

    asyncio.run(_run())


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

            watch = await b.kv_watch_in_bucket(
                kv_bucket_for_service(service_a),
                "nodes.node.state.>",
                cb=_on_state,
            )
            key = kv_key_node_state(node_id="node", field="hidden.output.value")
            await a.kv_put(key, b"state-bytes")

            for _ in range(1000):
                if seen:
                    break
                await asyncio.sleep(0.001)
            assert seen == [(key, b"state-bytes")]
            assert await b.kv_get_in_bucket(kv_bucket_for_service(service_a), key) == b"state-bytes"

            async def _handler(payload: bytes) -> bytes:
                return b"echo:" + bytes(payload)

            endpoint = await a.serve(svc_endpoint_subject(service_a, "status"), _handler)
            try:
                response = await b.request(
                    svc_endpoint_subject(service_a, "status"),
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
