from __future__ import annotations

import os
import sys
import unittest
import asyncio
from dataclasses import dataclass
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.service_bus.codec import decode_obj  # noqa: E402
from f8pysdk.service_bus.monitor_collector import MonitorCollector, MonitorCollectorConfig  # noqa: E402
from f8pysdk.time_utils import now_ms  # noqa: E402


class _FakeTransport:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((str(subject), bytes(payload)))


@dataclass
class _FakeInputBuffer:
    queue: list[int]


class _FakeBus:
    def __init__(self) -> None:
        self.service_id = "svcA"
        self._service_class = "f8.tests.service"
        self._active = True
        self._ready = False
        self._transport = _FakeTransport()
        self._data_inputs = {("n1", "p1"): _FakeInputBuffer(queue=[1, 2, 3])}


class MonitorCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_and_publish_snapshot(self) -> None:
        bus = _FakeBus()
        collector = MonitorCollector(
            bus, MonitorCollectorConfig(enabled=True, interval_ms=200, window_ms=2000, gpu_enabled=False)
        )
        ts = int(now_ms())
        collector.record_ready(True)
        collector.record_observed(port="in")
        collector.record_processed(port="out", emit_ts_ms=ts - 12, now_ts_ms=ts)
        collector.record_wait_ms(wait_ms=8.0)
        collector.record_dropped(dropped_count=2)
        collector.record_error(code="X_ERR", message="boom", ts_ms=ts)

        snapshot = collector._build_snapshot(ts_ms=ts)
        payload = snapshot.model_dump(mode="json", by_alias=True)
        self.assertEqual(payload.get("schemaVersion"), "f8monitor/1")
        self.assertEqual(str(snapshot.serviceId), "svcA")
        self.assertTrue(bool(snapshot.ready))
        self.assertEqual(int(snapshot.frame.observed), 1)
        self.assertEqual(int(snapshot.frame.processed), 1)
        self.assertEqual(int(snapshot.frame.dropped), 2)
        self.assertEqual(int(snapshot.queue.depth), 3)
        self.assertEqual(str(snapshot.error.lastCode), "X_ERR")
        self.assertFalse(bool(snapshot.gpu.available))

        await collector._publish_snapshot(snapshot)
        self.assertEqual(len(bus._transport.published), 1)
        subject, raw = bus._transport.published[0]
        self.assertIn(".monitor", subject)
        envelope = decode_obj(raw)
        self.assertEqual(envelope.get("value", {}).get("schemaVersion"), "f8monitor/1")

    async def test_monitor_loop_emits_periodically(self) -> None:
        bus = _FakeBus()
        collector = MonitorCollector(
            bus, MonitorCollectorConfig(enabled=True, interval_ms=200, window_ms=2000, gpu_enabled=False)
        )
        collector.record_ready(True)
        await collector.start()
        try:
            await asyncio.sleep(0.45)
        finally:
            await collector.stop()
        self.assertGreaterEqual(len(bus._transport.published), 1)


if __name__ == "__main__":
    unittest.main()
