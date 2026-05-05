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


if __name__ == "__main__":
    unittest.main()
