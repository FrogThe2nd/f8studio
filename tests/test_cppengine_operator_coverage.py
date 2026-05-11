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

    def test_cppengine_script_operators_ship_starter_templates(self) -> None:
        cppengine = json.loads(Path("services/f8/cppengine/describe.json").read_text(encoding="utf-8"))
        specs = {str(op["operatorClass"]): op for op in cppengine["operators"]}

        lua_code_field = specs["f8.lua_script"]["stateFields"][0]
        lua_code = lua_code_field["valueSchema"]["default"]
        self.assertEqual(lua_code_field["uiControl"], "code[lua]")
        self.assertEqual(lua_code_field["editorAssist"], {"version": 1, "language": "lua"})
        self.assertIn("on_exec(ctx, exec_in, inputs)", lua_code)
        self.assertIn("ctx:pull(\"msg\")", lua_code)
        self.assertIn("on_pull(ctx, port, inputs)", lua_code)

        angelscript_code_field = specs["f8.angelscript"]["stateFields"][0]
        angelscript_code = angelscript_code_field["valueSchema"]["default"]
        self.assertEqual(angelscript_code_field["uiControl"], "code[angelscript]")
        self.assertEqual(angelscript_code_field["editorAssist"], {"version": 1, "language": "angelscript"})
        self.assertIn("string on_exec_json", angelscript_code)
        self.assertIn("inputs_json", angelscript_code)
        self.assertIn("json_get(inputs_json, \"msg\")", angelscript_code)
        self.assertIn("json_output_exec", angelscript_code)


if __name__ == "__main__":
    unittest.main()
