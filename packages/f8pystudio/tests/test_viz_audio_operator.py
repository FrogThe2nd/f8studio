from __future__ import annotations

import unittest
from unittest.mock import patch

from f8pysdk.specs import F8RuntimeNode
from f8pystudio.operators.viz_audio import OPERATOR_CLASS, VizAudioRuntimeNode
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS


def _build_runtime(initial_state: dict[str, object] | None = None) -> VizAudioRuntimeNode:
    node = F8RuntimeNode(
        nodeId="vizaudio1",
        serviceId="svc_studio",
        serviceClass=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        dataInPorts=[],
        dataOutPorts=[],
        stateFields=[],
        stateValues={},
    )
    return VizAudioRuntimeNode(node_id="vizaudio1", node=node, initial_state=initial_state or {})


class VizAudioOperatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_config_uses_audio_stream_key(self) -> None:
        runtime = _build_runtime()

        with patch("f8pystudio.operators.viz_audio.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["audioStreamKey"] == ""


if __name__ == "__main__":
    unittest.main()
