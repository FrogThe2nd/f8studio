from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from f8pysdk.specs import F8RuntimeNode

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.operators.viz_video import OPERATOR_CLASS, VizVideoRuntimeNode


def _runtime_node() -> F8RuntimeNode:
    return F8RuntimeNode(
        nodeId="vizvideo1",
        serviceId="svc_studio",
        serviceClass=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        dataInPorts=[],
        dataOutPorts=[],
        stateFields=[],
        stateValues={},
    )


def _build_runtime(initial_state: dict[str, object] | None = None) -> VizVideoRuntimeNode:
    node = _runtime_node()
    return VizVideoRuntimeNode(node_id="vizvideo1", node=node, initial_state=initial_state or {})


class _StateUnavailableVizVideoRuntimeNode(VizVideoRuntimeNode):
    async def get_state_value(self, field: str) -> Any:
        raise RuntimeError(f"state store unavailable for {field}")


class _InlineStateVizVideoRuntimeNode(VizVideoRuntimeNode):
    def __init__(
        self,
        *,
        node: F8RuntimeNode,
        runtime_state: dict[str, Any],
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(node_id="vizvideo1", node=node, initial_state=initial_state)
        self._runtime_state = dict(runtime_state)

    async def get_state_value(self, field: str) -> Any:
        return self._runtime_state.get(field)


class VizVideoOperatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_config_includes_scalar_fields(self) -> None:
        runtime = _build_runtime()
        runtime._service_id = "svc_scalar"
        runtime._throttle_ms = 17
        runtime._flow_display_mode = "hsv"
        runtime._flow_mag_scale = 5.0
        runtime._flow_stride = 9
        runtime._scale_mode = "fit"
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
        assert payload["videoStreamKey"] == ""
        assert payload["throttleMs"] == 17
        assert payload["flowStreamKey"] == ""
        assert payload["flowDisplayMode"] == "hsv"
        assert payload["flowMagScale"] == 5.0
        assert payload["flowStride"] == 9
        assert payload["scaleMode"] == "fit"
        assert payload["scalarStreamKey"] == ""
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

    async def test_config_reads_fall_back_to_initial_state_when_runtime_state_fails(self) -> None:
        runtime = _StateUnavailableVizVideoRuntimeNode(
            node_id="vizvideo1",
            node=_runtime_node(),
            initial_state={
                "throttleMs": "250",
                "flowMagScale": "999",
                "scaleMode": "fit",
                "scalarInvert": "yes",
            },
        )

        with patch("f8pystudio.operators.viz_video.log.debug") as debug_log:
            throttle_ms = await runtime._get_int_state("throttleMs", default=33, minimum=0, maximum=60000)
            flow_mag_scale = await runtime._get_float_state(
                "flowMagScale",
                default=20.0,
                minimum=0.1,
                maximum=500.0,
            )
            scale_mode = await runtime._get_str_state("scaleMode", default="native")
            scalar_invert = await runtime._get_bool_state("scalarInvert", default=False)

        self.assertEqual(throttle_ms, 250)
        self.assertEqual(flow_mag_scale, 500.0)
        self.assertEqual(scale_mode, "fit")
        self.assertTrue(scalar_invert)
        self.assertEqual(debug_log.call_count, 4)

    async def test_runtime_state_wins_over_initial_state(self) -> None:
        runtime = _InlineStateVizVideoRuntimeNode(
            node=_runtime_node(),
            runtime_state={"flowStride": "24", "scalarInvert": "off"},
            initial_state={"flowStride": "4", "scalarInvert": "yes"},
        )

        flow_stride = await runtime._get_int_state("flowStride", default=12, minimum=2, maximum=128)
        scalar_invert = await runtime._get_bool_state("scalarInvert", default=True)

        self.assertEqual(flow_stride, 24)
        self.assertFalse(scalar_invert)

    async def test_invalid_numeric_state_uses_default_bounds(self) -> None:
        runtime = _InlineStateVizVideoRuntimeNode(
            node=_runtime_node(),
            runtime_state={"throttleMs": "bad", "flowMagScale": object()},
        )

        throttle_ms = await runtime._get_int_state("throttleMs", default=33, minimum=0, maximum=60000)
        flow_mag_scale = await runtime._get_float_state("flowMagScale", default=20.0, minimum=0.1, maximum=500.0)

        self.assertEqual(throttle_ms, 33)
        self.assertEqual(flow_mag_scale, 20.0)

    async def test_close_cancels_pending_task_and_detaches_renderer(self) -> None:
        runtime = _build_runtime()
        task = asyncio.create_task(asyncio.sleep(60))
        runtime._pending_task = task

        with patch("f8pystudio.operators.viz_video.emit_ui_command") as emit:
            await runtime.close()

        self.assertIsNone(runtime._pending_task)
        self.assertTrue(task.cancelled())
        self.assertEqual(emit.call_args.args[1], "viz.video.detach")

if __name__ == "__main__":
    unittest.main()
