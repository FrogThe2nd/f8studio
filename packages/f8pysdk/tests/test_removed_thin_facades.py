from __future__ import annotations

import importlib
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


REMOVED_THIN_FACADES = [
    "f8pysdk.service_bus.api",
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
        for module_name in REMOVED_THIN_FACADES:
            with self.subTest(module_name=module_name):
                with self.assertRaises((ImportError, ModuleNotFoundError)):
                    importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
