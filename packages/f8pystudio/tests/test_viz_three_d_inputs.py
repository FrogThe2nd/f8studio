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

from f8pystudio.constants import SERVICE_CLASS  # noqa: E402
from f8pystudio.operators.viz_three_d import OPERATOR_CLASS, VizThreeDRuntimeNode  # noqa: E402


def _skeleton_payload(model_name: str) -> dict[str, object]:
    return {
        "modelName": model_name,
        "skeletonProtocol": "none",
        "boneCount": 1,
        "bones": [{"name": "root", "pos": [0.0, 1.0, 2.0], "rot": [1.0, 0.0, 0.0, 0.0]}],
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


if __name__ == "__main__":
    unittest.main()
