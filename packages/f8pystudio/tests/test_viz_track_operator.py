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
        runtime._video_transport = "zenoh"
        runtime._video_key = "f8/svc/camera/nodes/camera/data/video"
        runtime._video_shm_name = "shm.camera.video"
        runtime._flow_transport = "zenoh"
        runtime._flow_key = "f8/svc/flow/nodes/flow/data/flow"
        runtime._flow_shm_name = "shm.flow"

        with patch("f8pystudio.operators.viz_track.emit_ui_command") as emit:
            await runtime._flush(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["videoTransport"] == "zenoh"
        assert payload["videoKey"] == "f8/svc/camera/nodes/camera/data/video"
        assert payload["videoShmName"] == "shm.camera.video"
        assert payload["flowTransport"] == "zenoh"
        assert payload["flowKey"] == "f8/svc/flow/nodes/flow/data/flow"
        assert payload["flowShmName"] == "shm.flow"


if __name__ == "__main__":
    unittest.main()
