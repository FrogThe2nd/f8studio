from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk.registry import RuntimeNodeRegistry  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.pyengine_node_registry import register_pyengine_specs  # noqa: E402


class PyEngineSignalOperatorRegistryTests(unittest.TestCase):
    def test_signal_processing_operators_are_registered(self) -> None:
        reg = RuntimeNodeRegistry.instance()
        register_pyengine_specs(reg)
        desc = reg.describe(SERVICE_CLASS)
        operator_classes = {str(spec.operatorClass or "") for spec in list(desc.operators or [])}

        self.assertIn("f8.detrend", operator_classes)
        self.assertIn("f8.lowpass_filter", operator_classes)
        self.assertIn("f8.highpass_filter", operator_classes)
        self.assertIn("f8.bandpass_filter", operator_classes)
        self.assertIn("f8.periodicity_detector", operator_classes)


if __name__ == "__main__":
    unittest.main()
