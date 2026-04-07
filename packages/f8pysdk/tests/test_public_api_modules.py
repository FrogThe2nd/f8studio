from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.app import MonitorRuntimeOverrides, ServiceCliTemplate, ServiceHost, ServiceHostConfig, ServiceRuntime, ServiceRuntimeConfig  # noqa: E402
from f8pysdk.command import CommandExecutionErrorKind, CommandExecutionResult, CommandOutputPolicy  # noqa: E402
from f8pysdk.data import CrossPublishPolicy, DataDeliveryMode  # noqa: E402
from f8pysdk.monitoring import MonitorCollector, MonitorCollectorConfig  # noqa: E402
from f8pysdk.nodes import OperatorNode, RuntimeNode, ServiceNode  # noqa: E402
from f8pysdk.registry import (  # noqa: E402
    OperatorAlreadyRegistered,
    RegistryError,
    RuntimeNodeRegistry,
    ServiceNotRegistered,
)
from f8pysdk.service_bus import (  # noqa: E402
    CommandExecutionErrorKind as BusCommandExecutionErrorKind,
    CommandExecutionResult as BusCommandExecutionResult,
    CommandOutputPolicy as BusCommandOutputPolicy,
    CrossPublishPolicy as BusCrossPublishPolicy,
    DataDeliveryMode as BusDataDeliveryMode,
    ServiceBus,
    ServiceBusConfig,
    StateRead as BusStateRead,
    StateWriteContext as BusStateWriteContext,
    StateWriteError as BusStateWriteError,
    StateWriteOrigin as BusStateWriteOrigin,
    StateWriteSource as BusStateWriteSource,
)
from f8pysdk.service_bus.config import ServiceBusConfig as OwnerServiceBusConfig  # noqa: E402
from f8pysdk.service_bus.runtime import ServiceBus as OwnerServiceBus  # noqa: E402
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

    def test_registry_exports_registry_types(self) -> None:
        self.assertTrue(issubclass(OperatorAlreadyRegistered, RegistryError))
        self.assertTrue(issubclass(ServiceNotRegistered, RegistryError))
        self.assertIsNotNone(RuntimeNodeRegistry)

    def test_transport_exports_transport_types(self) -> None:
        self.assertIsNotNone(NatsTransport)
        self.assertIsNotNone(NatsTransportConfig)

    def test_monitoring_exports_monitor_types(self) -> None:
        self.assertIsNotNone(MonitorCollector)
        self.assertIsNotNone(MonitorCollectorConfig)

    def test_service_bus_package_remains_public_bus_entrypoint(self) -> None:
        self.assertIsNotNone(ServiceBus)
        self.assertIsNotNone(ServiceBusConfig)
        self.assertIs(ServiceBus, OwnerServiceBus)
        self.assertIs(ServiceBusConfig, OwnerServiceBusConfig)

    def test_public_type_barrels_share_single_objects(self) -> None:
        self.assertIs(CommandExecutionErrorKind, BusCommandExecutionErrorKind)
        self.assertIs(CommandExecutionResult, BusCommandExecutionResult)
        self.assertIs(CommandOutputPolicy, BusCommandOutputPolicy)

        self.assertIs(CrossPublishPolicy, BusCrossPublishPolicy)
        self.assertIs(DataDeliveryMode, BusDataDeliveryMode)

        self.assertIs(StateRead, BusStateRead)
        self.assertIs(StateWriteContext, BusStateWriteContext)
        self.assertIs(StateWriteError, BusStateWriteError)
        self.assertIs(StateWriteOrigin, BusStateWriteOrigin)
        self.assertIs(StateWriteSource, BusStateWriteSource)


if __name__ == "__main__":
    unittest.main()
