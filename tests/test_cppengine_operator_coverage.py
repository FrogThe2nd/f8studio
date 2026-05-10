from __future__ import annotations

import json
import unittest
from pathlib import Path


class CppEngineOperatorCoverageTest(unittest.TestCase):
    def test_cppengine_describes_all_pyengine_operators_except_python_script(self) -> None:
        pyengine = json.loads(Path("services/f8/engine/describe.json").read_text(encoding="utf-8"))
        cppengine = json.loads(Path("services/f8/cppengine/describe.json").read_text(encoding="utf-8"))

        py_operator_classes = {str(op["operatorClass"]) for op in pyengine["operators"]}
        cpp_operator_classes = {str(op["operatorClass"]) for op in cppengine["operators"]}

        expected_missing = {"f8.python_script"}
        self.assertEqual(py_operator_classes - cpp_operator_classes, expected_missing)
        self.assertEqual({"f8.lua_script", "f8.angelscript"} - cpp_operator_classes, set())


if __name__ == "__main__":
    unittest.main()
