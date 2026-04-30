import os
import sys
import unittest
from typing import Any
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk._specs.builtin_fields import (  # noqa: E402
    MONITOR_PORT_NAME,
    normalize_describe_payload_dict,
    operator_state_fields_with_builtins,
    service_data_out_ports_with_builtins,
    service_state_fields_with_builtins,
)
from f8pysdk.specs import F8DataPortSpec, F8StateAccess, F8StateSpec  # noqa: E402
from f8pysdk.nats_naming import kv_key_node_state  # noqa: E402
from f8pysdk.codec import decode_obj  # noqa: E402
from f8pysdk.specs import boolean_schema, string_schema  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402


class _LifecycleRecordingNode:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.lifecycle_calls: list[bool] = []

    def attach(self, bus: object) -> None:
        self._bus = bus

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del field, ts_ms, meta
        return value

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        del field, value, ts_ms

    async def on_lifecycle(self, active: bool, meta: dict[str, Any]) -> None:
        del meta
        self.lifecycle_calls.append(bool(active))


class BuiltinStateFieldTests(unittest.TestCase):
    def test_service_state_fields_force_override(self) -> None:
        fields = [
            F8StateSpec(
                name="active",
                label="Old Active",
                description="legacy",
                valueSchema=boolean_schema(default=False),
                access=F8StateAccess.ro,
                showOnNode=False,
            ),
            F8StateSpec(
                name="svcId",
                label="Legacy Service",
                description="legacy",
                valueSchema=string_schema(),
                access=F8StateAccess.rw,
                showOnNode=True,
            ),
            F8StateSpec(
                name="custom",
                valueSchema=string_schema(),
                access=F8StateAccess.rw,
            ),
        ]
        out = service_state_fields_with_builtins(fields)
        self.assertEqual([str(x.name) for x in out], ["custom", "active", "svcId"])
        self.assertEqual(out[-2].access, F8StateAccess.rw)
        self.assertTrue(bool(out[-2].required))
        self.assertFalse(bool(out[-2].showOnNode))
        self.assertEqual(out[-1].access, F8StateAccess.ro)
        self.assertTrue(bool(out[-1].required))
        self.assertFalse(bool(out[-1].showOnNode))

    def test_operator_state_fields_force_override(self) -> None:
        fields = [
            F8StateSpec(name="svcId", valueSchema=string_schema(), access=F8StateAccess.rw),
            F8StateSpec(name="operatorId", valueSchema=string_schema(), access=F8StateAccess.rw),
            F8StateSpec(name="mode", valueSchema=string_schema(), access=F8StateAccess.rw),
        ]
        out = operator_state_fields_with_builtins(fields)
        self.assertEqual([str(x.name) for x in out], ["mode", "svcId", "operatorId"])
        self.assertEqual(out[-2].access, F8StateAccess.ro)
        self.assertTrue(bool(out[-2].required))
        self.assertEqual(out[-1].access, F8StateAccess.ro)
        self.assertTrue(bool(out[-1].required))

    def test_service_data_out_ports_force_monitor(self) -> None:
        ports = [
            F8DataPortSpec(name="telemetry", valueSchema=string_schema()),
            F8DataPortSpec(name="out", valueSchema=string_schema()),
            F8DataPortSpec(name="monitor", valueSchema=string_schema()),
        ]
        out = service_data_out_ports_with_builtins(ports)
        names = [str(port.name) for port in out]
        self.assertEqual(names.count(MONITOR_PORT_NAME), 1)
        self.assertIn("out", names)
        self.assertIn("telemetry", names)
        monitor_ports = [port for port in out if str(port.name) == MONITOR_PORT_NAME]
        self.assertEqual(len(monitor_ports), 1)
        self.assertTrue(bool(monitor_ports[0].required))
        self.assertFalse(bool(monitor_ports[0].showOnNode))

    def test_normalize_describe_payload_dict_force_override(self) -> None:
        payload = {
            "schemaVersion": "f8describe/1",
            "service": {
                "schemaVersion": "f8service/1",
                "serviceClass": "f8.tests.svc",
                "version": "0.0.1",
                "label": "svc",
                "dataOutPorts": [
                    {"name": "telemetry", "valueSchema": {"type": "string"}},
                ],
                "stateFields": [
                    {"name": "active", "valueSchema": {"type": "boolean"}, "access": "ro", "showOnNode": False},
                    {"name": "svcId", "valueSchema": {"type": "string"}, "access": "rw", "showOnNode": True},
                    {"name": "custom", "valueSchema": {"type": "string"}, "access": "rw"},
                ],
            },
            "operators": [
                {
                    "schemaVersion": "f8operator/1",
                    "serviceClass": "f8.tests.svc",
                    "operatorClass": "f8.tests.op",
                    "version": "0.0.1",
                    "label": "op",
                    "stateFields": [
                        {"name": "svcId", "valueSchema": {"type": "string"}, "access": "rw"},
                        {"name": "operatorId", "valueSchema": {"type": "string"}, "access": "rw"},
                        {"name": "threshold", "valueSchema": {"type": "number"}, "access": "rw"},
                    ],
                }
            ],
        }
        out = normalize_describe_payload_dict(payload)
        service_fields = out["service"]["stateFields"]
        operator_fields = out["operators"][0]["stateFields"]
        self.assertEqual([x["name"] for x in service_fields], ["custom", "active", "svcId"])
        active_fields = [x for x in service_fields if str(x.get("name")) == "active"]
        self.assertEqual(len(active_fields), 1)
        self.assertTrue(bool(active_fields[0].get("required")))
        self.assertFalse(bool(active_fields[0].get("showOnNode")))
        self.assertEqual([x["name"] for x in operator_fields], ["threshold", "svcId", "operatorId"])
        svc_id_fields = [x for x in service_fields if str(x.get("name")) == "svcId"]
        self.assertEqual(len(svc_id_fields), 1)
        self.assertTrue(bool(svc_id_fields[0].get("required")))
        operator_svc_id_fields = [x for x in operator_fields if str(x.get("name")) == "svcId"]
        self.assertEqual(len(operator_svc_id_fields), 1)
        self.assertTrue(bool(operator_svc_id_fields[0].get("required")))
        operator_id_fields = [x for x in operator_fields if str(x.get("name")) == "operatorId"]
        self.assertEqual(len(operator_id_fields), 1)
        self.assertTrue(bool(operator_id_fields[0].get("required")))
        service_data_ports = out["service"]["dataOutPorts"]
        self.assertTrue(any(str(x.get("name")) == MONITOR_PORT_NAME for x in service_data_ports))
        self.assertTrue(any(str(x.get("name")) == "telemetry" for x in service_data_ports))
        monitor_ports = [x for x in service_data_ports if str(x.get("name")) == MONITOR_PORT_NAME]
        self.assertEqual(len(monitor_ports), 1)
        self.assertTrue(bool(monitor_ports[0].get("required")))
        self.assertFalse(bool(monitor_ports[0].get("showOnNode")))


class LifecycleBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_seeds_active_state(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        with patch("f8pysdk.service_bus.workflow.lifecycle._ensure_micro_endpoints_started") as ensure_micro:
            async def _noop(_bus: object) -> None:
                return None
            ensure_micro.side_effect = _noop
            await bus.start()
        state = await bus.get_state("svcA", "active")
        await bus.stop()
        self.assertTrue(state.found)
        self.assertTrue(bool(state.value))

    async def test_seeded_active_state_origin_runtime(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        with patch("f8pysdk.service_bus.workflow.lifecycle._ensure_micro_endpoints_started") as ensure_micro:
            async def _noop(_bus: object) -> None:
                return None
            ensure_micro.side_effect = _noop
            await bus.start()
        key = kv_key_node_state(node_id="svcA", field="active")
        raw = await bus._transport.kv_get(key)
        await bus.stop()
        self.assertIsNotNone(raw)
        payload = decode_obj(raw) if raw is not None else {}
        self.assertEqual(payload.get("origin"), "runtime")

    async def test_external_active_state_write_applies_lifecycle(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        node = _LifecycleRecordingNode("svcA")
        bus.register_node(node)

        await bus.publish_state_external("svcA", "active", False)

        self.assertFalse(bus.active)
        self.assertEqual(node.lifecycle_calls, [False])
        state = await bus.get_state("svcA", "active")
        self.assertTrue(state.found)
        self.assertFalse(bool(state.value))

    async def test_external_active_state_rejects_invalid_value(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")

        with self.assertRaisesRegex(ValueError, "active must be a boolean"):
            await bus.publish_state_external("svcA", "active", "maybe")
        self.assertTrue(bus.active)


if __name__ == "__main__":
    unittest.main()
