from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.time_utils import now_ms  # noqa: E402
from f8pystudio.bridge.studio_bridge import (  # noqa: E402
    PyStudioServiceBridge,
    PyStudioServiceBridgeConfig,
)


def _snapshot(*, service_id: str, service_class: str, ts_ms: int) -> dict[str, object]:
    return {
        "schemaVersion": "f8monitor/1",
        "serviceId": service_id,
        "serviceClass": service_class,
        "nodeId": service_id,
        "tsMs": ts_ms,
        "alive": True,
        "ready": False,
        "active": True,
        "uptimeMs": 2000,
        "cpu": {"processPercent": 12.5, "systemPercent": 40.0},
        "memory": {"rssBytes": 32 * 1024 * 1024, "vmsBytes": 128 * 1024 * 1024},
        "gpu": {
            "vendor": "nvidia",
            "deviceIndex": 0,
            "utilPercent": 8.0,
            "memoryUsedBytes": 1024,
            "memoryTotalBytes": 4096,
            "available": True,
        },
        "frame": {"observed": 100, "processed": 99, "dropped": 1},
        "timing": {
            "processMsAvg": 1.2,
            "processMsP95": 2.0,
            "waitMsAvg": 3.0,
            "waitMsP95": 5.5,
            "latencyMsAvg": 7.0,
            "latencyMsP95": 9.5,
        },
        "queue": {"depth": 1},
        "error": {"countWindow": 2, "lastCode": "E_IO", "lastMessage": "io error", "lastTsMs": ts_ms - 10},
    }


def test_list_service_monitor_rows_includes_snapshot_and_managed_services() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())
    ts_ms = int(now_ms())
    bridge._monitor_center.ingest_snapshot(_snapshot(service_id="svcA", service_class="f8.tests.a", ts_ms=ts_ms))

    bridge._managed_service_ids.add("svcB")
    bridge._managed_service_classes["svcB"] = "f8.tests.b"
    bridge._cache_service_alive("svcB", True)
    bridge._cache_service_active("svcB", False)

    rows = bridge.list_service_monitor_rows()
    by_id = {row.service_id: row for row in rows}

    assert "svcA" in by_id
    svc_a = by_id["svcA"]
    assert svc_a.service_class == "f8.tests.a"
    assert svc_a.ready is False
    assert svc_a.cpu_process_percent == 12.5
    assert svc_a.memory_rss_bytes == 32 * 1024 * 1024
    assert svc_a.latency_ms_p95 == 9.5
    assert svc_a.wait_ms_p95 == 5.5
    assert svc_a.error_count_window == 2
    assert isinstance(svc_a.latest_snapshot, dict)

    assert "svcB" in by_id
    svc_b = by_id["svcB"]
    assert svc_b.service_class == "f8.tests.b"
    assert svc_b.latest_snapshot is None
    assert svc_b.alive is True
    assert svc_b.active is False


def test_bridge_exports_monitor_snapshot_stream() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())
    ts_ms = int(now_ms())
    bridge._monitor_center.ingest_snapshot(_snapshot(service_id="svcA", service_class="f8.tests.a", ts_ms=ts_ms - 2))
    bridge._monitor_center.ingest_snapshot(_snapshot(service_id="svcA", service_class="f8.tests.a", ts_ms=ts_ms - 1))

    stream = bridge.get_monitor_snapshot_stream("svcA", limit=1)

    assert len(stream) == 1
    assert stream[0]["serviceId"] == "svcA"
    assert stream[0]["tsMs"] == ts_ms - 1


def test_list_service_monitor_rows_always_includes_built_in_studio_service() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())

    rows = bridge.list_service_monitor_rows()
    by_id = {row.service_id: row for row in rows}

    assert bridge.studio_service_id in by_id
    studio_row = by_id[bridge.studio_service_id]
    assert studio_row.service_class == "f8.pystudio"
    assert studio_row.running is False


def test_list_service_monitor_rows_marks_built_in_studio_service_running_when_runtime_started() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())
    bridge._svc = SimpleNamespace(bus=object())
    bridge.request_service_status(bridge.studio_service_id)

    rows = bridge.list_service_monitor_rows()
    by_id = {row.service_id: row for row in rows}

    assert bridge.studio_service_id in by_id
    studio_row = by_id[bridge.studio_service_id]
    assert studio_row.service_class == "f8.pystudio"
    assert studio_row.running is True
    assert studio_row.alive is True
    assert studio_row.active is True


def test_unmanage_service_clears_monitor_cache() -> None:
    bridge = PyStudioServiceBridge(PyStudioServiceBridgeConfig())
    ts_ms = int(now_ms())
    bridge._monitor_center.ingest_snapshot(_snapshot(service_id="svcA", service_class="f8.tests.a", ts_ms=ts_ms))
    bridge._managed_service_ids.add("svcA")
    bridge._managed_service_classes["svcA"] = "f8.tests.a"

    rows_before = bridge.list_service_monitor_rows()
    assert any(row.service_id == "svcA" for row in rows_before)

    bridge.unmanage_service("svcA")

    rows_after = bridge.list_service_monitor_rows()
    assert all(row.service_id != "svcA" for row in rows_after)
