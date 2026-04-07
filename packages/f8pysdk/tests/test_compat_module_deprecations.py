from __future__ import annotations

import importlib
import os
import sys
import unittest
import warnings

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.service_bus.compat import ServiceBusCompatWarning  # noqa: E402


def _import_with_warnings(module_name: str) -> list[warnings.WarningMessage]:
    original_module = sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ServiceBusCompatWarning)
        imported_module = importlib.import_module(module_name)
    if original_module is not None:
        sys.modules[module_name] = original_module
    else:
        sys.modules.pop(module_name, None)
    del imported_module
    return caught


class CompatModuleDeprecationTests(unittest.TestCase):
    def test_legacy_service_bus_compat_modules_warn_on_import(self) -> None:
        modules = [
            ("f8pysdk.service_bus.bus", "f8pysdk.service_bus"),
            ("f8pysdk.service_bus.codec", "f8pysdk.codec"),
            ("f8pysdk.service_bus.command_runtime", "f8pysdk.service_bus.internal.command"),
            ("f8pysdk.service_bus.cross_state", "f8pysdk.service_bus.workflow.cross_state"),
            ("f8pysdk.service_bus.domain.state_pipeline", "f8pysdk.service_bus.state.pipeline"),
            ("f8pysdk.service_bus.error_utils", "f8pysdk.service_bus.internal.logging"),
            ("f8pysdk.service_bus.lifecycle", "f8pysdk.service_bus.workflow.lifecycle"),
            ("f8pysdk.service_bus.metadata", "f8pysdk.service_bus.state.helpers"),
            ("f8pysdk.service_bus.micro", "f8pysdk.service_bus.internal.micro"),
            ("f8pysdk.service_bus.payload", "f8pysdk.service_bus.state.helpers"),
            ("f8pysdk.service_bus.routing.data_emit", "f8pysdk.service_bus.data.emit"),
            ("f8pysdk.service_bus.routing.data_flow", "f8pysdk.service_bus.data.flow"),
            ("f8pysdk.service_bus.routing.data_router", "f8pysdk.service_bus.data.router"),
            ("f8pysdk.service_bus.routing_data", "f8pysdk.service_bus.data.flow"),
            ("f8pysdk.service_bus.rungraph_apply", "f8pysdk.service_bus.workflow.rungraph"),
            ("f8pysdk.service_bus.runtime_collections", "f8pysdk.service_bus.internal.cache"),
            ("f8pysdk.service_bus.state_publish", "f8pysdk.service_bus.internal.state"),
            ("f8pysdk.service_bus.state_router", "f8pysdk.service_bus.state.router"),
            ("f8pysdk.service_bus.state_store", "f8pysdk.service_bus.state.store"),
        ]

        for module_name, replacement in modules:
            with self.subTest(module_name=module_name):
                caught = _import_with_warnings(module_name)
                self.assertEqual(len(caught), 1)
                self.assertIs(caught[0].category, ServiceBusCompatWarning)
                self.assertIn(module_name, str(caught[0].message))
                self.assertIn(replacement, str(caught[0].message))


if __name__ == "__main__":
    unittest.main()
