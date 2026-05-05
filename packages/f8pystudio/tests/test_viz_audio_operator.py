from __future__ import annotations

import unittest
from unittest.mock import patch

from f8pysdk.specs import F8RuntimeNode
from f8pysdk.zenoh_naming import zenoh_data_key

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
    async def test_push_config_omits_legacy_shm_name_for_zenoh(self) -> None:
        runtime = _build_runtime()
        runtime._service_id = "svc_audio"
        runtime._audio_transport = "zenoh"
        runtime._audio_key = ""
        runtime._shm_name = "shm.audio"

        with patch("f8pystudio.operators.viz_audio.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["audioTransport"] == "zenoh"
        assert payload["audioKey"] == zenoh_data_key("svc_audio", node_id="svc_audio", port_id="audio")
        assert "shmName" not in payload

    async def test_push_config_keeps_legacy_shm_name_when_selected(self) -> None:
        runtime = _build_runtime()
        runtime._service_id = "svc_audio"
        runtime._audio_transport = "legacy_shm"
        runtime._shm_name = "shm.audio"

        with patch("f8pystudio.operators.viz_audio.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["audioTransport"] == "legacy_shm"
        assert payload["shmName"] == "shm.audio"


if __name__ == "__main__":
    unittest.main()
