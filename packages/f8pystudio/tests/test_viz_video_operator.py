from __future__ import annotations

import unittest
from unittest.mock import patch

from f8pysdk.specs import F8RuntimeNode
from f8pysdk.zenoh_naming import zenoh_data_key

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.operators.viz_video import OPERATOR_CLASS, VizVideoRuntimeNode


def _build_runtime(initial_state: dict[str, object] | None = None) -> VizVideoRuntimeNode:
    node = F8RuntimeNode(
        nodeId="vizvideo1",
        serviceId="svc_studio",
        serviceClass=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        dataInPorts=[],
        dataOutPorts=[],
        stateFields=[],
        stateValues={},
    )
    return VizVideoRuntimeNode(node_id="vizvideo1", node=node, initial_state=initial_state or {})


class VizVideoOperatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_config_includes_scalar_fields(self) -> None:
        runtime = _build_runtime()
        runtime._service_id = "svc_scalar"
        runtime._shm_name = ""
        runtime._throttle_ms = 17
        runtime._flow_transport = "zenoh"
        runtime._flow_key = "f8/test/flow"
        runtime._flow_shm_name = "shm.flow"
        runtime._flow_display_mode = "hsv"
        runtime._flow_mag_scale = 5.0
        runtime._flow_stride = 9
        runtime._scale_mode = "fit"
        runtime._scalar_transport = "zenoh"
        runtime._scalar_key = "f8/test/scalar"
        runtime._scalar_shm_name = "shm.scalar"
        runtime._scalar_display_mode = "colormap"
        runtime._scalar_colormap = "viridis"
        runtime._scalar_range_mode = "manual"
        runtime._scalar_min = -3.0
        runtime._scalar_max = 7.0
        runtime._scalar_auto_percentile_lo = 4.0
        runtime._scalar_auto_percentile_hi = 96.0
        runtime._scalar_invert = True
        runtime._scalar_nan_mode = "max"

        with patch("f8pystudio.operators.viz_video.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["videoTransport"] == "zenoh"
        assert payload["videoKey"] == zenoh_data_key("svc_scalar", node_id="svc_scalar", port_id="video")
        assert "shmName" not in payload
        assert payload["throttleMs"] == 17
        assert payload["flowTransport"] == "zenoh"
        assert payload["flowKey"] == "f8/test/flow"
        assert "flowShmName" not in payload
        assert payload["flowDisplayMode"] == "hsv"
        assert payload["flowMagScale"] == 5.0
        assert payload["flowStride"] == 9
        assert payload["scaleMode"] == "fit"
        assert payload["scalarTransport"] == "zenoh"
        assert payload["scalarKey"] == "f8/test/scalar"
        assert "scalarShmName" not in payload
        assert payload["scalarDisplayMode"] == "colormap"
        assert payload["scalarColormap"] == "viridis"
        assert payload["scalarRangeMode"] == "manual"
        assert payload["scalarMin"] == -3.0
        assert payload["scalarMax"] == 7.0
        assert payload["scalarAutoPercentileLo"] == 4.0
        assert payload["scalarAutoPercentileHi"] == 96.0
        assert payload["scalarInvert"] is True
        assert payload["scalarNanMode"] == "max"

    async def test_push_config_normalizes_invalid_scalar_modes(self) -> None:
        runtime = _build_runtime()
        runtime._scalar_display_mode = "invalid"
        runtime._scalar_colormap = "invalid"
        runtime._scalar_range_mode = "invalid"
        runtime._scalar_nan_mode = "invalid"

        with patch("f8pystudio.operators.viz_video.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["scalarDisplayMode"] == "off"
        assert payload["scalarColormap"] == "turbo"
        assert payload["scalarRangeMode"] == "auto"
        assert payload["scalarNanMode"] == "transparent"

    async def test_push_config_keeps_legacy_shm_fields_when_selected(self) -> None:
        runtime = _build_runtime()
        runtime._service_id = "svc_legacy"
        runtime._video_transport = "legacy_shm"
        runtime._shm_name = "shm.video"
        runtime._flow_transport = "legacy_shm"
        runtime._flow_shm_name = "shm.flow"
        runtime._scalar_transport = "legacy_shm"
        runtime._scalar_shm_name = "shm.scalar"

        with patch("f8pystudio.operators.viz_video.emit_ui_command") as emit:
            await runtime._push_config_async(now_ms=123)

        payload = dict(emit.call_args.args[2])
        assert payload["videoTransport"] == "legacy_shm"
        assert payload["shmName"] == "shm.video"
        assert payload["flowTransport"] == "legacy_shm"
        assert payload["flowShmName"] == "shm.flow"
        assert payload["scalarTransport"] == "legacy_shm"
        assert payload["scalarShmName"] == "shm.scalar"


if __name__ == "__main__":
    unittest.main()
