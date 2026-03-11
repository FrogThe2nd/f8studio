from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pysdk import F8DataPortSpec, any_schema  # noqa: E402
from f8pysdk.generated import F8RuntimeNode  # noqa: E402
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry  # noqa: E402

from f8pystudio.constants import SERVICE_CLASS  # noqa: E402
from f8pystudio.operators.viz_three_d import OPERATOR_CLASS, VizThreeDRuntimeNode, register_operator  # noqa: E402


def _skeleton_payload(model_name: str) -> dict[str, object]:
    return {
        "modelName": model_name,
        "skeletonProtocol": "none",
        "boneCount": 1,
        "bones": [{"name": "root", "pos": [0.0, 1.0, 2.0], "rot": [1.0, 0.0, 0.0, 0.0]}],
    }


def _large_skeleton_payload(model_name: str, *, bone_count: int) -> dict[str, object]:
    bones: list[dict[str, object]] = []
    for index in range(bone_count):
        bones.append(
            {
                "name": f"bone_{index}",
                "pos": [float(index), float(index % 11), float(index % 7)],
                "rot": [1.0, 0.0, 0.0, 0.0],
            }
        )
    return {
        "modelName": model_name,
        "skeletonProtocol": "none",
        "boneCount": bone_count,
        "bones": bones,
    }


class VizThreeDInputTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_node(port_names: list[str]) -> VizThreeDRuntimeNode:
        node = F8RuntimeNode(
            nodeId="viz3d1",
            serviceId="svc_studio",
            serviceClass=SERVICE_CLASS,
            operatorClass=OPERATOR_CLASS,
            dataInPorts=[
                F8DataPortSpec(name=port_name, description="", valueSchema=any_schema()) for port_name in port_names
            ],
            dataOutPorts=[],
            stateFields=[],
            stateValues={},
        )
        return VizThreeDRuntimeNode(node_id="viz3d1", node=node, initial_state={"throttleMs": 0})

    async def test_multi_port_overlay_respects_port_order_and_prefixes_name(self) -> None:
        runtime = self._build_node(["skeletons", "camA", "camB"])
        with patch("f8pystudio.operators.viz_three_d.emit_ui_command") as emit:
            await runtime.on_data("camB", _skeleton_payload("Avatar"), ts_ms=100)
            await runtime.on_data("camA", _skeleton_payload("Avatar"), ts_ms=101)

        self.assertGreaterEqual(len(emit.call_args_list), 2)
        payload = dict(emit.call_args_list[-1].args[2])
        people = list(payload.get("people") or [])
        names = [str(person.get("name")) for person in people]
        self.assertEqual(names, ["camA:Avatar", "camB:Avatar"])

    async def test_single_bone_is_auto_wrapped_and_rendered(self) -> None:
        runtime = self._build_node(["smoothed"])
        with patch("f8pystudio.operators.viz_three_d.emit_ui_command") as emit:
            await runtime.on_data(
                "smoothed",
                {"name": "Head", "pos": [1.0, 2.0, 3.0], "rot": [1.0, 0.0, 0.0, 0.0]},
                ts_ms=120,
            )

        payload = dict(emit.call_args_list[-1].args[2])
        people = list(payload.get("people") or [])
        self.assertEqual(len(people), 1)
        person = dict(people[0])
        self.assertEqual(person.get("name"), "smoothed:smoothed")
        nodes = list(person.get("nodes") or [])
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].get("name"), "Head")
        self.assertEqual(nodes[0].get("pos"), [1.0, 2.0, 3.0])

    async def test_mixed_inputs_keep_valid_people_when_invalid_payload_arrives(self) -> None:
        runtime = self._build_node(["main", "bone"])
        with patch("f8pystudio.operators.viz_three_d.emit_ui_command") as emit:
            await runtime.on_data("main", [_skeleton_payload("MainRig"), {"invalid": 1}, 7], ts_ms=130)
            await runtime.on_data("bone", {"name": "Wrist", "pos": [4.0, 5.0, 6.0], "rot": [1.0, 0.0, 0.0, 0.0]})
            await runtime.on_data("junk", "oops", ts_ms=131)

        payload = dict(emit.call_args_list[-1].args[2])
        people = list(payload.get("people") or [])
        names = [str(person.get("name")) for person in people]
        self.assertEqual(names, ["main:MainRig", "bone:bone"])

    async def test_large_skeleton_payload_emits_stability_hints(self) -> None:
        runtime = self._build_node(["skeletons"])
        runtime._initial_state["maxBonesPerPerson"] = 512
        runtime._initial_state["uiFpsCap"] = 60

        with patch("f8pystudio.operators.viz_three_d.emit_ui_command") as emit:
            await runtime.on_data("skeletons", _large_skeleton_payload("DenseRig", bone_count=400), ts_ms=140)

        payload = dict(emit.call_args_list[-1].args[2])
        performance_hints = dict(payload.get("performanceHints") or {})
        self.assertEqual(performance_hints.get("totalNodes"), 400)
        self.assertTrue(performance_hints.get("largeSkeletonMode"))
        self.assertFalse(performance_hints.get("suppressBoneAxes"))
        self.assertFalse(performance_hints.get("suppressBoneNames"))
        self.assertFalse(performance_hints.get("suppressAxisTree"))
        self.assertTrue(performance_hints.get("suppressPersonBoxes"))
        self.assertEqual(performance_hints.get("maxVisibleBoneLabels"), 256)
        self.assertEqual(performance_hints.get("recommendedFpsCap"), 30)

    async def test_medium_skeleton_payload_limits_bone_label_budget(self) -> None:
        runtime = self._build_node(["skeletons"])
        runtime._initial_state["maxBonesPerPerson"] = 128

        with patch("f8pystudio.operators.viz_three_d.emit_ui_command") as emit:
            await runtime.on_data("skeletons", _large_skeleton_payload("DenseRig", bone_count=80), ts_ms=150)

        payload = dict(emit.call_args_list[-1].args[2])
        performance_hints = dict(payload.get("performanceHints") or {})
        self.assertEqual(performance_hints.get("totalNodes"), 80)
        self.assertFalse(performance_hints.get("suppressBoneNames"))
        self.assertEqual(performance_hints.get("maxVisibleBoneLabels"), 32)


class VizThreeDOperatorSpecDefaultsTests(unittest.TestCase):
    def test_register_operator_uses_safe_heavy_render_defaults(self) -> None:
        registry = RuntimeNodeRegistry()
        register_operator(registry)

        spec = next(
            operator_spec
            for operator_spec in registry.operator_specs(SERVICE_CLASS)
            if operator_spec.operatorClass == OPERATOR_CLASS
        )
        state_by_name = {state_field.name: state_field for state_field in list(spec.stateFields or [])}

        self.assertTrue(state_by_name["showBonePoints"].valueSchema.default)
        self.assertFalse(state_by_name["showBoneAxes"].valueSchema.default)
        self.assertFalse(state_by_name["autoZoomOnNewPeople"].valueSchema.default)


if __name__ == "__main__":
    unittest.main()
