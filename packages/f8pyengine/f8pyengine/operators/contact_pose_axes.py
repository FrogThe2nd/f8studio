from __future__ import annotations

import math
from typing import Any

from f8pysdk.f8_naming import ensure_token
from f8pysdk.nodes import OperatorNode
from f8pysdk.registry import Registry
from f8pysdk.specs import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    array_schema,
    boolean_schema,
    complex_object_schema,
    number_schema,
    string_schema,
)

from ..constants import SERVICE_CLASS
from .contact_geometry import (
    AXIS_NAMES,
    LOCAL_AXIS_NAMES,
    ContactGeometryConfig,
    ContactResult,
    calculate_contact_axes,
)

OPERATOR_CLASS = "f8.contact_pose_axes"


def _bone_schema():
    return complex_object_schema(
        properties={
            "name": string_schema(),
            "pos": array_schema(items=number_schema()),
            "rot": array_schema(items=number_schema()),
            "basis": complex_object_schema(
                properties={
                    "up": string_schema(enum=list(LOCAL_AXIS_NAMES)),
                    "right": string_schema(enum=list(LOCAL_AXIS_NAMES)),
                }
            ),
        }
    )


def _skeleton_schema():
    return complex_object_schema(properties={"bones": array_schema(items=_bone_schema())})


def _status_schema():
    return complex_object_schema(
        properties={
            "valid": boolean_schema(),
            "contactValid": boolean_schema(),
            "reason": string_schema(),
            "referenceLength": number_schema(),
            "referenceRadius": number_schema(),
            "axialMeters": number_schema(),
            "radialMeters": number_schema(),
            "lateralForwardMeters": number_schema(),
            "lateralRightMeters": number_schema(),
            "twistDegrees": number_schema(),
            "rollDegrees": number_schema(),
            "pitchDegrees": number_schema(),
            **{axis: number_schema() for axis in AXIS_NAMES},
        }
    )


def _frame_schema():
    return complex_object_schema(
        properties={
            "axes": complex_object_schema(properties={axis: number_schema() for axis in AXIS_NAMES}),
            "status": _status_schema(),
        }
    )


class ContactPoseAxesRuntimeNode(OperatorNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[port.name for port in (node.dataInPorts or [])],
            data_out_ports=[port.name for port in (node.dataOutPorts or [])],
            state_fields=[state.name for state in (node.stateFields or [])],
        )
        state = dict(initial_state or {})
        self._origin_bone = _text_or_default(state.get("originBone"), "Penis01")
        self._direction_bone = _text_or_default(state.get("directionBone"), "Penis02")
        self._tip_bone = _text_or_default(state.get("tipBone"), "Penis09")
        self._support_bone = _text_or_default(state.get("supportBone"), "M_Hips")
        self._support_right_axis = _local_axis_or_default(state.get("supportRightAxis"), "-local_x")
        self._support_up_axis = _local_axis_or_default(state.get("supportUpAxis"), "+local_y")
        self._target_up_axis = _local_axis_or_default(state.get("targetUpAxis"), "-local_y")
        self._target_right_axis = _local_axis_or_default(state.get("targetRightAxis"), "+local_z")
        self._l0_min = _positive_or_default(state.get("l0MinMeters"), 0.08, allow_zero=True)
        self._l0_max = _positive_or_default(state.get("l0MaxMeters"), 0.27)
        self._lateral_range = _positive_or_default(state.get("lateralRangeMeters"), 0.15)
        self._twist_range = _positive_or_default(state.get("twistRangeDegrees"), 90.0)
        self._tilt_range = _positive_or_default(state.get("tiltRangeDegrees"), 30.0)
        self._radius_scale = _positive_or_default(state.get("radiusScale"), 0.22)
        self._invert_l0 = _boolean_or_default(state.get("invertL0"), False)
        self._require_contact = _boolean_or_default(state.get("requireContact"), False)
        self._cached_ctx_id: str | int | None = None
        self._cached_result: ContactResult | None = None
        self._cache_valid = False

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        del ts_ms
        name = str(field or "").strip()
        if name == "originBone":
            self._origin_bone = _text_or_default(value, self._origin_bone)
        elif name == "directionBone":
            self._direction_bone = _text_or_default(value, self._direction_bone)
        elif name == "tipBone":
            self._tip_bone = _text_or_default(value, self._tip_bone)
        elif name == "supportBone":
            self._support_bone = _text_or_default(value, self._support_bone)
        elif name == "supportRightAxis":
            self._support_right_axis = _local_axis_or_default(value, self._support_right_axis)
        elif name == "supportUpAxis":
            self._support_up_axis = _local_axis_or_default(value, self._support_up_axis)
        elif name == "targetUpAxis":
            self._target_up_axis = _local_axis_or_default(value, self._target_up_axis)
        elif name == "targetRightAxis":
            self._target_right_axis = _local_axis_or_default(value, self._target_right_axis)
        elif name == "l0MinMeters":
            self._l0_min = _positive_or_default(value, self._l0_min, allow_zero=True)
        elif name == "l0MaxMeters":
            self._l0_max = _positive_or_default(value, self._l0_max)
        elif name == "lateralRangeMeters":
            self._lateral_range = _positive_or_default(value, self._lateral_range)
        elif name == "twistRangeDegrees":
            self._twist_range = _positive_or_default(value, self._twist_range)
        elif name == "tiltRangeDegrees":
            self._tilt_range = _positive_or_default(value, self._tilt_range)
        elif name == "radiusScale":
            self._radius_scale = _positive_or_default(value, self._radius_scale)
        elif name == "invertL0":
            self._invert_l0 = _boolean_or_default(value, self._invert_l0)
        elif name == "requireContact":
            self._require_contact = _boolean_or_default(value, self._require_contact)
        else:
            return
        self._invalidate_cache()

    async def validate_state(
        self,
        field: str,
        value: Any,
        *,
        ts_ms: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        if name in {"supportRightAxis", "supportUpAxis", "targetUpAxis", "targetRightAxis"}:
            normalized = _local_axis_or_default(value, "")
            if normalized not in LOCAL_AXIS_NAMES:
                raise ValueError(f"{name} must be one of {', '.join(LOCAL_AXIS_NAMES)}")
            return normalized
        if name in {"originBone", "directionBone", "tipBone", "supportBone"}:
            text = str(value or "").strip()
            if not text:
                raise ValueError(f"{name} must not be empty")
            return text
        if name == "l0MinMeters":
            return _validated_nonnegative(value, name)
        if name in {
            "l0MaxMeters",
            "lateralRangeMeters",
            "twistRangeDegrees",
            "tiltRangeDegrees",
            "radiusScale",
        }:
            return _validated_positive(value, name)
        if name in {"invertL0", "requireContact"}:
            return _boolean_or_default(value, False)
        return value

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        port_name = str(port or "").strip()
        if port_name not in {*AXIS_NAMES, "status", "frame"}:
            return None
        result = await self._result_for_context(ctx_id)
        if result is None:
            if port_name == "status":
                return {"valid": False, "contactValid": False, "reason": "missing_or_invalid_input"}
            return None
        if self._require_contact and not bool(result.status["contactValid"]):
            status = {**result.status, "valid": False, "reason": "outside_contact_radius"}
            if port_name == "status":
                return status
            if port_name == "frame":
                return {"axes": dict(result.axes), "status": status}
            return None
        if port_name == "frame":
            return {"axes": dict(result.axes), "status": dict(result.status)}
        return result.status if port_name == "status" else result.axes[port_name]

    async def _result_for_context(self, ctx_id: str | int | None) -> ContactResult | None:
        if ctx_id is not None and self._cache_valid and self._cached_ctx_id == ctx_id:
            return self._cached_result

        reference_raw = await self.pull("referenceSkeleton", ctx_id=ctx_id)
        target_raw = await self.pull("targetBone", ctx_id=ctx_id)
        target_up_axis, target_right_axis = _target_basis_or_default(
            target_raw,
            up_default=self._target_up_axis,
            right_default=self._target_right_axis,
        )
        result = calculate_contact_axes(
            reference_raw,
            target_raw,
            ContactGeometryConfig(
                origin_bone=self._origin_bone,
                direction_bone=self._direction_bone,
                tip_bone=self._tip_bone,
                support_bone=self._support_bone,
                support_right_axis=self._support_right_axis,
                support_up_axis=self._support_up_axis,
                target_up_axis=target_up_axis,
                target_right_axis=target_right_axis,
                l0_min=self._l0_min,
                l0_max=self._l0_max,
                lateral_range=self._lateral_range,
                twist_range=self._twist_range,
                tilt_range=self._tilt_range,
                radius_scale=self._radius_scale,
                invert_l0=self._invert_l0,
            ),
        )
        if ctx_id is not None:
            self._cached_ctx_id = ctx_id
            self._cached_result = result
            self._cache_valid = True
        return result

    def _invalidate_cache(self) -> None:
        self._cached_ctx_id = None
        self._cached_result = None
        self._cache_valid = False


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _text_or_default(value: Any, default: str) -> str:
    result = str(value or "").strip()
    return result or default


def _local_axis_or_default(value: Any, default: str) -> str:
    result = str(value or "").strip().lower()
    return result if result in LOCAL_AXIS_NAMES else default


def _target_basis_or_default(
    target: Any,
    *,
    up_default: str,
    right_default: str,
) -> tuple[str, str]:
    basis = target.get("basis") if isinstance(target, dict) else None
    if not isinstance(basis, dict):
        return up_default, right_default
    return (
        _local_axis_or_default(basis.get("up"), up_default),
        _local_axis_or_default(basis.get("right"), right_default),
    )


def _positive_or_default(value: Any, default: float, *, allow_zero: bool = False) -> float:
    result = _finite_float(value)
    if result is None or result < 0.0 or (not allow_zero and result <= 0.0):
        return default
    return result


def _validated_nonnegative(value: Any, name: str) -> float:
    result = _finite_float(value)
    if result is None or result < 0.0:
        raise ValueError(f"{name} must be a finite number greater than or equal to zero")
    return result


def _validated_positive(value: Any, name: str) -> float:
    result = _finite_float(value)
    if result is None or result <= 0.0:
        raise ValueError(f"{name} must be a finite number greater than zero")
    return result


def _boolean_or_default(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        if value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if value.strip().lower() in {"0", "false", "no", "off"}:
            return False
    return default


def _state(
    name: str,
    label: str,
    description: str,
    schema: Any,
    *,
    show_on_node: bool = False,
) -> F8StateSpec:
    return F8StateSpec(
        name=name,
        label=label,
        description=description,
        valueSchema=schema,
        access=F8StateAccess.rw,
        required=True,
        showOnNode=show_on_node,
    )


ContactPoseAxesRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    paletteCategory=f"{SERVICE_CLASS}.motion",
    operatorClass=OPERATOR_CLASS,
    version="0.2.0",
    label="Contact Pose Axes",
    description="Build a multi-bone contact frame and emit normalized SR6 L0/L1/L2/R0/R1/R2 axes.",
    tags=["skeleton", "contact", "geometry", "multibone", "sr6", "tcode"],
    dataInPorts=[
        F8DataPortSpec(
            name="referenceSkeleton",
            description="Reference participant skeleton containing origin, direction, tip and support bones.",
            valueSchema=_skeleton_schema(),
        ),
        F8DataPortSpec(name="targetBone", description="Selected target functional bone.", valueSchema=_bone_schema()),
    ],
    dataOutPorts=[
        F8DataPortSpec(
            name="frame",
            description="Atomic SR6 axes and contact diagnostics for one geometry sample.",
            valueSchema=_frame_schema(),
        ),
        *[
            F8DataPortSpec(
                name=axis,
                description=f"Normalized {axis} contact axis (0..1).",
                valueSchema=number_schema(minimum=0.0, maximum=1.0),
            )
            for axis in AXIS_NAMES
        ],
        F8DataPortSpec(name="status", description="Contact geometry diagnostics.", valueSchema=_status_schema()),
    ],
    stateFields=[
        _state(
            "originBone", "Origin Bone", "Reference base bone.", string_schema(default="Penis01"), show_on_node=True
        ),
        _state(
            "directionBone",
            "Direction Bone",
            "Second reference bone defining the positive L0 direction.",
            string_schema(default="Penis02"),
            show_on_node=True,
        ),
        _state(
            "tipBone",
            "Tip Bone",
            "Extended reference tip used for length and contact bounds.",
            string_schema(default="Penis09"),
        ),
        _state(
            "supportBone",
            "Support Bone",
            "Bone whose mapped axes stabilize the reference plane.",
            string_schema(default="M_Hips"),
        ),
        _state(
            "supportRightAxis",
            "Support Right Axis",
            "Support bone local axis mapped to body right.",
            string_schema(default="-local_x", enum=list(LOCAL_AXIS_NAMES)),
        ),
        _state(
            "supportUpAxis",
            "Support Up Axis",
            "Support bone local axis mapped to body up.",
            string_schema(default="+local_y", enum=list(LOCAL_AXIS_NAMES)),
        ),
        _state(
            "targetUpAxis",
            "Target Up Axis",
            "Target bone local axis mapped to target up.",
            string_schema(default="-local_y", enum=list(LOCAL_AXIS_NAMES)),
        ),
        _state(
            "targetRightAxis",
            "Target Right Axis",
            "Target bone local axis mapped to target right.",
            string_schema(default="+local_z", enum=list(LOCAL_AXIS_NAMES)),
        ),
        _state(
            "l0MinMeters", "L0 Input Min", "Axial distance mapped to L0=0.", number_schema(default=0.08, minimum=0.0)
        ),
        _state(
            "l0MaxMeters", "L0 Input Max", "Axial distance mapped to L0=1.", number_schema(default=0.27, minimum=0.001)
        ),
        _state(
            "lateralRangeMeters",
            "Lateral Range",
            "Symmetric L1/L2 input range.",
            number_schema(default=0.15, minimum=0.001),
        ),
        _state(
            "twistRangeDegrees",
            "Twist Range",
            "Symmetric R0 angle range.",
            number_schema(default=90.0, minimum=1.0, maximum=179.0),
        ),
        _state(
            "tiltRangeDegrees",
            "Tilt Range",
            "Symmetric R1/R2 angle range.",
            number_schema(default=30.0, minimum=1.0, maximum=89.0),
        ),
        _state(
            "radiusScale",
            "Radius Scale",
            "Contact cylinder radius as reference-length ratio.",
            number_schema(default=0.22, minimum=0.001, maximum=2.0),
        ),
        _state("invertL0", "Invert L0", "Invert the normalized primary axis.", boolean_schema(default=False)),
        _state(
            "requireContact",
            "Require Contact",
            "Suppress axes while outside the contact cylinder.",
            boolean_schema(default=False),
        ),
    ],
)


def register_operator(registry: Registry) -> Registry:
    registry.register_operator(ContactPoseAxesRuntimeNode.SPEC, ContactPoseAxesRuntimeNode, overwrite=True)
    return registry


__all__ = ["ContactPoseAxesRuntimeNode", "register_operator"]
