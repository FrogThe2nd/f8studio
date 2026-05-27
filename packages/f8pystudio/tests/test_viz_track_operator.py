from __future__ import annotations

import unittest
from unittest.mock import patch

from f8pysdk.specs import F8RuntimeNode

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.operators.viz_track import OPERATOR_CLASS, VizTrackRuntimeNode


def _build_runtime(initial_state: dict[str, object] | None = None) -> VizTrackRuntimeNode:
    node = F8RuntimeNode(
        nodeId="viztrack1",
        serviceId="svc_studio",
        serviceClass=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        dataInPorts=[],
        dataOutPorts=[],
        stateFields=[],
        stateValues={},
    )
    return VizTrackRuntimeNode(node_id="viztrack1", node=node, initial_state=initial_state or {})


class VizTrackOperatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_flush_includes_zenoh_video_and_flow_fields(self) -> None:
        runtime = _build_runtime()
        runtime._dirty = True

        with patch("f8pystudio.operators.viz_track.emit_ui_command") as emit:
            await runtime._flush(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["videoStreamKey"] == ""
        assert payload["flowStreamKey"] == ""

    async def test_detection_payload_emits_track_history(self) -> None:
        runtime = _build_runtime(initial_state={"throttleMs": 0})

        with patch("f8pystudio.operators.viz_track.emit_ui_command") as emit:
            await runtime.on_data(
                "detections",
                {
                    "schemaVersion": "f8visionDetections/1",
                    "tsMs": 1000,
                    "width": 640,
                    "height": 480,
                    "skeletonProtocol": "coco17",
                    "detections": [
                        {
                            "id": "42",
                            "bbox": ["1", "2", "3", "4"],
                            "keypoints": [{"x": 1.0, "y": 2.0}, "bad"],
                        }
                    ],
                },
                ts_ms=1000,
            )

        payload = dict(emit.call_args.args[2])
        assert payload["width"] == 640
        assert payload["height"] == 480
        assert payload["tracks"] == [
            {
                "id": 42,
                "history": [
                    {
                        "tsMs": 1000,
                        "bbox": [1.0, 2.0, 3.0, 4.0],
                        "keypoints": [{"x": 1.0, "y": 2.0}],
                        "kind": "det",
                        "skeletonProtocol": "coco17",
                    }
                ],
            }
        ]


if __name__ == "__main__":
    unittest.main()
