from __future__ import annotations

import os
import sys
import unittest
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.monitor_schema import (  # noqa: E402
    MonitorContractError,
    monitor_snapshot_schema_dict,
    validate_describe_monitor_contract,
)


def _describe_payload_with_ports(ports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": "f8describe/1",
        "service": {
            "schemaVersion": "f8service/1",
            "serviceClass": "f8.tests.svc",
            "version": "0.0.1",
            "label": "svc",
            "dataOutPorts": ports,
        },
        "operators": [],
    }


class MonitorSchemaTests(unittest.TestCase):
    def test_validate_describe_monitor_contract_passes_with_monitor_port(self) -> None:
        payload = _describe_payload_with_ports(
            [
                {
                    "name": "monitor",
                    "valueSchema": monitor_snapshot_schema_dict(),
                    "required": False,
                    "showOnNode": True,
                    "description": "Unified runtime monitor snapshots (health/resource/perf/error).",
                }
            ]
        )
        validate_describe_monitor_contract(payload)

    def test_validate_describe_monitor_contract_rejects_missing_monitor(self) -> None:
        payload = _describe_payload_with_ports(
            [
                {
                    "name": "out",
                    "valueSchema": {"type": "string"},
                    "required": False,
                    "showOnNode": True,
                    "description": "output",
                }
            ]
        )
        with self.assertRaises(MonitorContractError):
            validate_describe_monitor_contract(payload)

    def test_validate_describe_monitor_contract_rejects_telemetry_port(self) -> None:
        payload = _describe_payload_with_ports(
            [
                {
                    "name": "monitor",
                    "valueSchema": monitor_snapshot_schema_dict(),
                    "required": False,
                    "showOnNode": True,
                    "description": "Unified runtime monitor snapshots (health/resource/perf/error).",
                },
                {
                    "name": "telemetry",
                    "valueSchema": {"type": "object"},
                    "required": False,
                    "showOnNode": True,
                    "description": "legacy stream",
                },
            ]
        )
        with self.assertRaises(MonitorContractError):
            validate_describe_monitor_contract(payload)


if __name__ == "__main__":
    unittest.main()
