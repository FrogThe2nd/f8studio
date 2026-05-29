from __future__ import annotations

import threading
import time

from f8pystudio.automation.observation_store import RuntimeObservationStore


def test_wait_state_observes_future_update() -> None:
    store = RuntimeObservationStore()

    def write_later() -> None:
        time.sleep(0.02)
        store.put_state(service_id="svc", node_id="node", field="value", value=42, ts_ms=100)

    thread = threading.Thread(target=write_later)
    thread.start()
    try:
        value = store.wait_state(
            service_id="svc",
            node_id="node",
            field="value",
            after_ts_ms=50,
            timeout_s=1.0,
        )
    finally:
        thread.join(timeout=1.0)

    assert value is not None
    assert value.value == 42
    assert value.ts_ms == 100


def test_wait_port_samples_returns_bounded_latest_samples() -> None:
    store = RuntimeObservationStore()
    store.put_port_sample(service_id="svc", node_id="node", port="out", sample={"value": 1, "observedAtMs": 10})
    store.put_port_sample(service_id="svc", node_id="node", port="out", sample={"value": 2, "observedAtMs": 20})

    samples = store.wait_port_samples(
        service_id="svc",
        node_id="node",
        port="out",
        min_count=1,
        limit=1,
        timeout_s=0.01,
    )

    assert samples == [{"value": 2, "observedAtMs": 20}]
