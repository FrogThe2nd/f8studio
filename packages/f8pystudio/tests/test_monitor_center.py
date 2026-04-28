from __future__ import annotations

from f8pysdk.codec import dump_json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.time_utils import now_ms  # noqa: E402
from f8pystudio.monitoring import MonitorCenter  # noqa: E402


def _snapshot(*, service_id: str, ts_ms: int, cpu: float, wait_p95: float, error_count: int) -> dict[str, object]:
    return {
        "schemaVersion": "f8monitor/1",
        "serviceId": service_id,
        "serviceClass": "f8.tests.svc",
        "nodeId": service_id,
        "tsMs": ts_ms,
        "alive": True,
        "ready": True,
        "active": True,
        "uptimeMs": 1000,
        "cpu": {"processPercent": cpu, "systemPercent": 0.0},
        "memory": {"rssBytes": 10, "vmsBytes": 20},
        "gpu": {
            "vendor": "",
            "deviceIndex": None,
            "utilPercent": None,
            "memoryUsedBytes": None,
            "memoryTotalBytes": None,
            "available": False,
        },
        "frame": {"observed": 10, "processed": 9, "dropped": 1},
        "timing": {"processMsAvg": 1.0, "processMsP95": 2.0, "waitMsAvg": 3.0, "waitMsP95": wait_p95},
        "queue": {"depth": 2},
        "error": {"countWindow": error_count, "lastCode": "E1", "lastMessage": "err", "lastTsMs": ts_ms},
    }


def test_monitor_center_build_report() -> None:
    base_ts = int(now_ms())
    center = MonitorCenter(window_ms=60_000)
    center.ingest_snapshot(_snapshot(service_id="svcA", ts_ms=base_ts - 2_000, cpu=40.0, wait_p95=6.0, error_count=2))
    center.ingest_snapshot(_snapshot(service_id="svcB", ts_ms=base_ts - 1_500, cpu=20.0, wait_p95=2.0, error_count=0))
    center.update_service_status(service_id="svcB", active=False)

    report = center.build_report()
    payload = dump_json(report, mode="json", by_alias=True)
    assert payload["schemaVersion"] == "f8monitorReport/1"
    assert len(payload["services"]) == 2
    assert payload["hotspots"]["cpuTop"][0]["serviceId"] == "svcA"
    svc_b = next(item for item in payload["services"] if item["serviceId"] == "svcB")
    assert svc_b["active"] is False
    assert len(payload["errors"]) == 1


def test_monitor_center_prunes_old_samples() -> None:
    base_ts = int(now_ms())
    center = MonitorCenter(window_ms=100)
    center.ingest_snapshot(_snapshot(service_id="svcA", ts_ms=base_ts - 300, cpu=1.0, wait_p95=1.0, error_count=0))
    center.ingest_snapshot(_snapshot(service_id="svcA", ts_ms=base_ts - 20, cpu=2.0, wait_p95=2.0, error_count=0))
    report = center.build_report()
    services = dump_json(report, mode="json", by_alias=True)["services"]
    assert services
    latest = services[0]["latest"]
    assert latest["tsMs"] >= base_ts - 20
