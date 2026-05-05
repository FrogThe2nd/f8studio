from __future__ import annotations

import importlib
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


REMOVED_THIN_FACADES = [
    "f8pysdk.command_state",
    "f8pysdk.json_unwrap",
    "f8pysdk.msgspec_codec",
    "f8pysdk.nats_transport",
    "f8pysdk.nats_naming",
    "f8pysdk.monitor_schema",
    "f8pysdk.schema_helpers",
    "f8pysdk.spec_edit_policy",
    "f8pysdk.spec_metadata",
    "f8pysdk.builtin_state_fields",
    "f8pysdk.service_ready",
    "f8pysdk.nats_server_bootstrap",
    "f8pysdk.service_runtime_tools.catalog",
    "f8pysdk.service_runtime_tools.discovery",
    "f8pysdk.service_runtime_tools.process_manager",
    "f8pysdk.service_runtime_tools.session_loader",
    "f8pysdk.service_runtime_tools.session_compiler",
    "f8pysdk.service_runtime_tools.readiness",
    "f8pysdk.service_runtime_tools.nats_bootstrap",
    "f8pysdk.transport",
    "f8pysdk.service_runtime_tools.error_reporting",
    "f8pysdk.service_bus.monitor_collector",
    "f8pysdk.service_bus.api.bus",
    "f8pysdk.service_bus.api.config",
    "f8pysdk.service_bus.api.types",
    "f8pysdk.service_bus.types",
    "f8pysdk.service_bus.internal.data",
    "f8pysdk.service_bus.internal.state",
    "f8pysdk.service_bus.state_read",
    "f8pysdk.service_bus.state_write",
    "f8pysdk.service_bus.state.read",
    "f8pysdk.service_bus.state.write",
]


class RemovedThinFacadeTests(unittest.TestCase):
    def test_removed_thin_facades_are_not_importable(self) -> None:
        # `f8pysdk.service_bus.api` remains as a namespace package directory in the
        # current layout, so importing the package name itself may succeed even
        # though the removed thin facade modules under it no longer exist.
        for module_name in REMOVED_THIN_FACADES:
            with self.subTest(module_name=module_name):
                with self.assertRaises((ImportError, ModuleNotFoundError)):
                    importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
