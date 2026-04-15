from __future__ import annotations

import asyncio
import time
from typing import Any

from f8pysdk.specs import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    boolean_schema,
    integer_schema,
    number_schema,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.nodes import OperatorNode, RuntimeNode
from f8pysdk.registry import RuntimeNodeRegistry
from f8pysdk.shm import video_shm_name

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.contracts.ui_commands import emit_ui_command
from .categories import PALETTE_CATEGORY_VIZ


OPERATOR_CLASS = "f8.viz.video"
RENDERER_CLASS = "viz_video"


def _default_video_shm_name(service_id: str) -> str:
    s = str(service_id or "").strip()
    return video_shm_name(s) if s else ""


class VizVideoRuntimeNode(OperatorNode):
    """
    Studio-only visualization node: view a Video SHM (BGRA32) in a Qt widget.

    This runtime node only pushes config to the UI layer; the Qt widget reads
    shared memory directly (avoids pushing frame payloads through UiCommand).
    """

    SPEC = F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass=SERVICE_CLASS,
        paletteCategory=PALETTE_CATEGORY_VIZ,
        operatorClass=OPERATOR_CLASS,
        version="0.0.1",
        label="Video Viz",
        description="Display frames from a VideoSHM region (BGRA32).",
        tags=["ui", "shm", "video", "viewer"],
        dataInPorts=[],
        dataOutPorts=[],
        rendererClass=RENDERER_CLASS,
        stateFields=[
            F8StateSpec(
                name="uiUpdate",
                label="UI Update",
                description="Pause/resume embedded viewer updates in the editor.",
                valueSchema=boolean_schema(default=True),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="serviceId",
                label="Service Id",
                description="If set and shmName is empty, uses shm.<serviceId>.video",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="shmName",
                label="SHM Name",
                description="Video SHM mapping name (e.g. shm.implayer.video). Overrides serviceId.",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=True,
            ),
            F8StateSpec(
                name="throttleMs",
                label="Refresh (ms)",
                description="UI refresh interval in milliseconds (0 = as fast as possible).",
                valueSchema=integer_schema(default=33, minimum=0, maximum=60000),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="flowShmName",
                label="Flow SHM Name",
                description="Optional flow SHM mapping name (format flow2_f16).",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=True,
            ),
            F8StateSpec(
                name="flowDisplayMode",
                label="Flow Display",
                description="Flow rendering mode: off, hsv, or arrows.",
                valueSchema=string_schema(default="off", enum=["off", "hsv", "arrows"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="flowMagScale",
                label="Flow Mag Scale",
                description="Reference max magnitude for HSV/value and arrow scaling.",
                valueSchema=number_schema(default=20.0, minimum=0.1, maximum=500.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="flowStride",
                label="Flow Stride",
                description="Sampling stride for arrow rendering.",
                valueSchema=integer_schema(default=12, minimum=2, maximum=128),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scaleMode",
                label="Scale Mode",
                description="Video scaling mode: native (1:1) or fit.",
                valueSchema=string_schema(default="fit", enum=["native", "fit"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarShmName",
                label="Scalar SHM Name",
                description="Optional scalar field SHM mapping name (format scalar1_f32).",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=True,
            ),
            F8StateSpec(
                name="scalarDisplayMode",
                label="Scalar Display",
                description="Scalar rendering mode: off or colormap.",
                valueSchema=string_schema(default="off", enum=["off", "colormap"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarColormap",
                label="Scalar Colormap",
                description="Colormap for scalar rendering.",
                valueSchema=string_schema(default="turbo", enum=["gray", "turbo", "viridis", "magma"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarRangeMode",
                label="Scalar Range Mode",
                description="Scalar normalization mode: auto or manual.",
                valueSchema=string_schema(default="auto", enum=["auto", "manual"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarMin",
                label="Scalar Min",
                description="Manual min for scalar normalization.",
                valueSchema=number_schema(default=-1.0, minimum=-1_000_000_000.0, maximum=1_000_000_000.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarMax",
                label="Scalar Max",
                description="Manual max for scalar normalization.",
                valueSchema=number_schema(default=1.0, minimum=-1_000_000_000.0, maximum=1_000_000_000.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarAutoPercentileLo",
                label="Scalar Auto Lo %",
                description="Lower percentile for auto scalar normalization.",
                valueSchema=number_schema(default=2.0, minimum=0.0, maximum=100.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarAutoPercentileHi",
                label="Scalar Auto Hi %",
                description="Upper percentile for auto scalar normalization.",
                valueSchema=number_schema(default=98.0, minimum=0.0, maximum=100.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarInvert",
                label="Scalar Invert",
                description="Invert normalized scalar values before colormap.",
                valueSchema=boolean_schema(default=False),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="scalarNanMode",
                label="Scalar NaN Mode",
                description="NaN/Inf handling for scalar values.",
                valueSchema=string_schema(default="transparent", enum=["transparent", "zero", "min", "max"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
        ],
    )

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[],
            data_out_ports=[],
            state_fields=[s.name for s in (node.stateFields or [])],
        )
        self._initial_state = dict(initial_state or {})
        self._config_loaded = False
        self._service_id = ""
        self._shm_name = ""
        self._throttle_ms = 33
        self._flow_shm_name = ""
        self._flow_display_mode = "off"
        self._flow_mag_scale = 20.0
        self._flow_stride = 12
        self._scale_mode = "native"
        self._scalar_shm_name = ""
        self._scalar_display_mode = "off"
        self._scalar_colormap = "turbo"
        self._scalar_range_mode = "auto"
        self._scalar_min = -1.0
        self._scalar_max = 1.0
        self._scalar_auto_percentile_lo = 2.0
        self._scalar_auto_percentile_hi = 98.0
        self._scalar_invert = False
        self._scalar_nan_mode = "transparent"
        self._pending_task: asyncio.Task[object] | None = None

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._ensure_config_loaded(), name=f"pystudio:videoshm:init:{self.node_id}")
        except RuntimeError:
            pass

    async def close(self) -> None:
        try:
            t = self._pending_task
            self._pending_task = None
            if t is not None:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
        except (RuntimeError, TypeError):
            pass
        emit_ui_command(self.node_id, "viz.video.detach", {}, ts_ms=int(time.time() * 1000))

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        f = str(field or "").strip()
        if f not in (
            "serviceId",
            "shmName",
            "throttleMs",
            "flowShmName",
            "flowDisplayMode",
            "flowMagScale",
            "flowStride",
            "scaleMode",
            "scalarShmName",
            "scalarDisplayMode",
            "scalarColormap",
            "scalarRangeMode",
            "scalarMin",
            "scalarMax",
            "scalarAutoPercentileLo",
            "scalarAutoPercentileHi",
            "scalarInvert",
            "scalarNanMode",
        ):
            return
        await self._ensure_config_loaded()
        if f == "serviceId":
            self._service_id = str(await self._get_str_state("serviceId", default=self._service_id)).strip()
        elif f == "shmName":
            self._shm_name = str(await self._get_str_state("shmName", default=self._shm_name)).strip()
        elif f == "throttleMs":
            self._throttle_ms = await self._get_int_state("throttleMs", default=self._throttle_ms, minimum=0, maximum=60000)
        elif f == "flowShmName":
            self._flow_shm_name = str(await self._get_str_state("flowShmName", default=self._flow_shm_name)).strip()
        elif f == "flowDisplayMode":
            mode = str(await self._get_str_state("flowDisplayMode", default=self._flow_display_mode)).strip().lower()
            self._flow_display_mode = mode if mode in ("off", "hsv", "arrows") else "off"
        elif f == "flowMagScale":
            self._flow_mag_scale = await self._get_float_state("flowMagScale", default=self._flow_mag_scale, minimum=0.1, maximum=500.0)
        elif f == "flowStride":
            self._flow_stride = await self._get_int_state("flowStride", default=self._flow_stride, minimum=2, maximum=128)
        elif f == "scaleMode":
            mode = str(await self._get_str_state("scaleMode", default=self._scale_mode)).strip().lower()
            self._scale_mode = mode if mode in ("native", "fit") else "native"
        elif f == "scalarShmName":
            self._scalar_shm_name = str(await self._get_str_state("scalarShmName", default=self._scalar_shm_name)).strip()
        elif f == "scalarDisplayMode":
            mode = str(await self._get_str_state("scalarDisplayMode", default=self._scalar_display_mode)).strip().lower()
            self._scalar_display_mode = self._normalize_scalar_display_mode(mode)
        elif f == "scalarColormap":
            cmap = str(await self._get_str_state("scalarColormap", default=self._scalar_colormap)).strip().lower()
            self._scalar_colormap = self._normalize_scalar_colormap(cmap)
        elif f == "scalarRangeMode":
            mode = str(await self._get_str_state("scalarRangeMode", default=self._scalar_range_mode)).strip().lower()
            self._scalar_range_mode = self._normalize_scalar_range_mode(mode)
        elif f == "scalarMin":
            self._scalar_min = await self._get_float_state(
                "scalarMin",
                default=self._scalar_min,
                minimum=-1_000_000_000.0,
                maximum=1_000_000_000.0,
            )
        elif f == "scalarMax":
            self._scalar_max = await self._get_float_state(
                "scalarMax",
                default=self._scalar_max,
                minimum=-1_000_000_000.0,
                maximum=1_000_000_000.0,
            )
        elif f == "scalarAutoPercentileLo":
            self._scalar_auto_percentile_lo = await self._get_float_state(
                "scalarAutoPercentileLo",
                default=self._scalar_auto_percentile_lo,
                minimum=0.0,
                maximum=100.0,
            )
        elif f == "scalarAutoPercentileHi":
            self._scalar_auto_percentile_hi = await self._get_float_state(
                "scalarAutoPercentileHi",
                default=self._scalar_auto_percentile_hi,
                minimum=0.0,
                maximum=100.0,
            )
        elif f == "scalarInvert":
            self._scalar_invert = await self._get_bool_state("scalarInvert", default=self._scalar_invert)
        elif f == "scalarNanMode":
            mode = str(await self._get_str_state("scalarNanMode", default=self._scalar_nan_mode)).strip().lower()
            self._scalar_nan_mode = self._normalize_scalar_nan_mode(mode)
        await self._push_config(now_ms=int(ts_ms) if ts_ms is not None else int(time.time() * 1000))

    async def _ensure_config_loaded(self) -> None:
        if self._config_loaded:
            return
        self._service_id = str(await self._get_str_state("serviceId", default=str(self._initial_state.get("serviceId", "")))).strip()
        self._shm_name = str(await self._get_str_state("shmName", default=str(self._initial_state.get("shmName", "")))).strip()
        self._throttle_ms = await self._get_int_state("throttleMs", default=33, minimum=0, maximum=60000)
        self._flow_shm_name = str(await self._get_str_state("flowShmName", default=str(self._initial_state.get("flowShmName", "")))).strip()
        flow_mode = str(await self._get_str_state("flowDisplayMode", default=str(self._initial_state.get("flowDisplayMode", "off")))).strip().lower()
        self._flow_display_mode = flow_mode if flow_mode in ("off", "hsv", "arrows") else "off"
        self._flow_mag_scale = await self._get_float_state("flowMagScale", default=20.0, minimum=0.1, maximum=500.0)
        self._flow_stride = await self._get_int_state("flowStride", default=12, minimum=2, maximum=128)
        mode = str(await self._get_str_state("scaleMode", default=str(self._initial_state.get("scaleMode", "native")))).strip().lower()
        self._scale_mode = mode if mode in ("native", "fit") else "native"
        self._scalar_shm_name = str(
            await self._get_str_state("scalarShmName", default=str(self._initial_state.get("scalarShmName", "")))
        ).strip()
        scalar_display = str(
            await self._get_str_state("scalarDisplayMode", default=str(self._initial_state.get("scalarDisplayMode", "off")))
        ).strip().lower()
        self._scalar_display_mode = self._normalize_scalar_display_mode(scalar_display)
        scalar_colormap = str(
            await self._get_str_state("scalarColormap", default=str(self._initial_state.get("scalarColormap", "turbo")))
        ).strip().lower()
        self._scalar_colormap = self._normalize_scalar_colormap(scalar_colormap)
        scalar_range_mode = str(
            await self._get_str_state("scalarRangeMode", default=str(self._initial_state.get("scalarRangeMode", "auto")))
        ).strip().lower()
        self._scalar_range_mode = self._normalize_scalar_range_mode(scalar_range_mode)
        self._scalar_min = await self._get_float_state("scalarMin", default=-1.0, minimum=-1_000_000_000.0, maximum=1_000_000_000.0)
        self._scalar_max = await self._get_float_state("scalarMax", default=1.0, minimum=-1_000_000_000.0, maximum=1_000_000_000.0)
        self._scalar_auto_percentile_lo = await self._get_float_state(
            "scalarAutoPercentileLo",
            default=2.0,
            minimum=0.0,
            maximum=100.0,
        )
        self._scalar_auto_percentile_hi = await self._get_float_state(
            "scalarAutoPercentileHi",
            default=98.0,
            minimum=0.0,
            maximum=100.0,
        )
        self._scalar_invert = await self._get_bool_state(
            "scalarInvert",
            default=bool(self._initial_state.get("scalarInvert", False)),
        )
        scalar_nan_mode = str(
            await self._get_str_state("scalarNanMode", default=str(self._initial_state.get("scalarNanMode", "transparent")))
        ).strip().lower()
        self._scalar_nan_mode = self._normalize_scalar_nan_mode(scalar_nan_mode)
        self._config_loaded = True
        await self._push_config(now_ms=int(time.time() * 1000))

    async def _push_config(self, *, now_ms: int) -> None:
        if self._pending_task is not None and not self._pending_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_task = loop.create_task(self._push_config_async(now_ms), name=f"pystudio:videoshm:cfg:{self.node_id}")

    async def _push_config_async(self, now_ms: int) -> None:
        shm_name = str(self._shm_name or "").strip()
        if not shm_name:
            shm_name = _default_video_shm_name(self._service_id)
        emit_ui_command(
            self.node_id,
            "viz.video.set",
            {
                "shmName": shm_name,
                "serviceId": str(self._service_id or "").strip(),
                "throttleMs": int(self._throttle_ms),
                "flowShmName": str(self._flow_shm_name or "").strip(),
                "flowDisplayMode": str(self._flow_display_mode or "off"),
                "flowMagScale": float(self._flow_mag_scale),
                "flowStride": int(self._flow_stride),
                "scaleMode": str(self._scale_mode or "native"),
                "scalarShmName": str(self._scalar_shm_name or "").strip(),
                "scalarDisplayMode": self._normalize_scalar_display_mode(self._scalar_display_mode),
                "scalarColormap": self._normalize_scalar_colormap(self._scalar_colormap),
                "scalarRangeMode": self._normalize_scalar_range_mode(self._scalar_range_mode),
                "scalarMin": float(self._scalar_min),
                "scalarMax": float(self._scalar_max),
                "scalarAutoPercentileLo": float(self._scalar_auto_percentile_lo),
                "scalarAutoPercentileHi": float(self._scalar_auto_percentile_hi),
                "scalarInvert": bool(self._scalar_invert),
                "scalarNanMode": self._normalize_scalar_nan_mode(self._scalar_nan_mode),
            },
            ts_ms=int(now_ms),
        )

    async def _get_int_state(self, name: str, *, default: int, minimum: int, maximum: int) -> int:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name)
        try:
            out = int(v) if v is not None else int(default)
        except Exception:
            out = int(default)
        if out < minimum:
            out = minimum
        if out > maximum:
            out = maximum
        return out

    async def _get_str_state(self, name: str, *, default: str) -> str:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name)
        try:
            s = str(v) if v is not None else str(default)
        except Exception:
            s = str(default)
        return s

    async def _get_bool_state(self, name: str, *, default: bool) -> bool:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        text = str(v or "").strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        return bool(default)

    async def _get_float_state(self, name: str, *, default: float, minimum: float, maximum: float) -> float:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name)
        try:
            out = float(v) if v is not None else float(default)
        except Exception:
            out = float(default)
        if out < minimum:
            out = minimum
        if out > maximum:
            out = maximum
        return out

    @staticmethod
    def _normalize_scalar_display_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in ("off", "colormap"):
            return normalized
        return "off"

    @staticmethod
    def _normalize_scalar_colormap(colormap: str) -> str:
        normalized = str(colormap or "").strip().lower()
        if normalized in ("gray", "turbo", "viridis", "magma"):
            return normalized
        return "turbo"

    @staticmethod
    def _normalize_scalar_range_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in ("auto", "manual"):
            return normalized
        return "auto"

    @staticmethod
    def _normalize_scalar_nan_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in ("transparent", "zero", "min", "max"):
            return normalized
        return "transparent"


def register_operator(registry: RuntimeNodeRegistry) -> RuntimeNodeRegistry:

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
        return VizVideoRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    registry.register_operator_factory(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    registry.register_operator_spec(VizVideoRuntimeNode.SPEC, overwrite=True)
    return registry
