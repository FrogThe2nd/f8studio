from __future__ import annotations

import importlib
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.app import MonitorRuntimeOverrides, ServiceCliTemplate, ServiceHost, ServiceHostConfig, ServiceRuntime, ServiceRuntimeConfig  # noqa: E402
from f8pysdk.bus import ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.command import CommandExecutionErrorKind, CommandExecutionResult, CommandOutputPolicy  # noqa: E402
from f8pysdk.data import CrossPublishPolicy, DataDeliveryMode  # noqa: E402
from f8pysdk.monitoring import MonitorCollector, MonitorCollectorConfig  # noqa: E402
from f8pysdk.nodes import OperatorNode, RuntimeNode, ServiceNode  # noqa: E402
from f8pysdk.registry import (  # noqa: E402
    OperatorFactoryNotRegistered,
    OperatorAlreadyRegistered,
    RegistryError,
    RuntimeNodeRegistry,
    ServiceFactoryNotRegistered,
    ServiceNotRegistered,
)
from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, F8StateAccess, F8StateSpec, any_schema, schema_type  # noqa: E402
from f8pysdk.state import StateRead, StateWriteContext, StateWriteError, StateWriteOrigin, StateWriteSource  # noqa: E402
from f8pysdk.transport import NatsTransport, NatsTransportConfig  # noqa: E402


class PublicApiModuleTests(unittest.TestCase):
    def test_app_exports_runtime_facades(self) -> None:
        self.assertIsNotNone(MonitorRuntimeOverrides)
        self.assertIsNotNone(ServiceCliTemplate)
        self.assertIsNotNone(ServiceHost)
        self.assertIsNotNone(ServiceHostConfig)
        self.assertIsNotNone(ServiceRuntime)
        self.assertIsNotNone(ServiceRuntimeConfig)

    def test_command_exports_match_public_command_types(self) -> None:
        self.assertEqual(CommandOutputPolicy.none.value, "none")
        self.assertEqual(CommandExecutionErrorKind.missing_target.value, "missing_target")
        self.assertIsNotNone(CommandExecutionResult)

    def test_data_exports_policy_literals(self) -> None:
        routed: CrossPublishPolicy = "routed"
        callback: DataDeliveryMode = "callback"
        self.assertEqual(routed, "routed")
        self.assertEqual(callback, "callback")

    def test_nodes_exports_runtime_node_types(self) -> None:
        self.assertTrue(issubclass(ServiceNode, RuntimeNode))
        self.assertTrue(issubclass(OperatorNode, RuntimeNode))

    def test_specs_exports_generated_types_and_helpers(self) -> None:
        self.assertIsNotNone(F8OperatorSpec)
        self.assertIsNotNone(F8ServiceSpec)
        self.assertEqual(F8StateAccess.rw.value, "rw")
        self.assertIsNotNone(F8StateSpec)
        self.assertEqual(schema_type(any_schema()), "any")

    def test_registry_exports_registry_types(self) -> None:
        self.assertTrue(issubclass(OperatorFactoryNotRegistered, RegistryError))
        self.assertTrue(issubclass(OperatorAlreadyRegistered, RegistryError))
        self.assertTrue(issubclass(ServiceFactoryNotRegistered, RegistryError))
        self.assertTrue(issubclass(ServiceNotRegistered, RegistryError))
        self.assertIsNotNone(RuntimeNodeRegistry)

    def test_transport_exports_transport_types(self) -> None:
        self.assertIsNotNone(NatsTransport)
        self.assertIsNotNone(NatsTransportConfig)

    def test_monitoring_exports_monitor_types(self) -> None:
        self.assertIsNotNone(MonitorCollector)
        self.assertIsNotNone(MonitorCollectorConfig)

    def test_bus_exports_public_bus_entrypoint(self) -> None:
        self.assertIsNotNone(ServiceBus)
        self.assertIsNotNone(ServiceBusConfig)
        self.assertIs(ServiceBus, importlib.import_module("f8pysdk.service_bus.runtime").ServiceBus)
        self.assertIs(ServiceBusConfig, importlib.import_module("f8pysdk.service_bus.config").ServiceBusConfig)

    def test_service_bus_root_is_not_a_public_barrel(self) -> None:
        service_bus_module = importlib.import_module("f8pysdk.service_bus")
        self.assertFalse(hasattr(service_bus_module, "ServiceBus"))
        self.assertFalse(hasattr(service_bus_module, "ServiceBusConfig"))
        self.assertFalse(hasattr(service_bus_module, "CrossPublishPolicy"))
        self.assertFalse(hasattr(service_bus_module, "StateRead"))


if __name__ == "__main__":
    unittest.main()
