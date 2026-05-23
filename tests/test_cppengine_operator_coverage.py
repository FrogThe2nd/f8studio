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
        self.assertIn("f8.data_pick", cpp_operator_classes)
        self.assertIn("f8.lua_script", cpp_operator_classes)
        self.assertNotIn("f8.angelscript", cpp_operator_classes)

    def test_cppengine_data_pick_operator_shape(self) -> None:
        cppengine = json.loads(Path("services/f8/cppengine/describe.json").read_text(encoding="utf-8"))
        specs = {str(op["operatorClass"]): op for op in cppengine["operators"]}

        data_pick = specs["f8.data_pick"]
        self.assertEqual(data_pick["label"], "Data Pick")
        self.assertEqual(data_pick["paletteCategory"], "f8.cppengine.expr")
        self.assertEqual([port["name"] for port in data_pick["dataInPorts"]], ["msg"])
        self.assertEqual([port["name"] for port in data_pick["dataOutPorts"]], ["out"])

        state_fields = {str(field["name"]): field for field in data_pick["stateFields"]}
        self.assertEqual(state_fields["path"]["uiControl"], "wrapline")
        self.assertEqual(state_fields["path"]["valueSchema"]["default"], "")
        self.assertEqual(state_fields["valueType"]["valueSchema"]["enum"], ["any", "number", "string", "bool"])
        self.assertEqual(state_fields["fallback"]["uiControl"], "wrapline[json]")
        self.assertIsNone(state_fields["fallback"]["valueSchema"]["default"])

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


if __name__ == "__main__":
    unittest.main()
