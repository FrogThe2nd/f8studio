from __future__ import annotations

import math
from dataclasses import dataclass
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

OPERATOR_CLASS = "f8.contact_pose_axes"
_AXES = ("L0", "L1", "L2", "R0", "R1", "R2")
_LOCAL_AXES = ("+local_x", "-local_x", "+local_y", "-local_y", "+local_z", "-local_z")
_EPSILON = 1e-8


def _bone_schema():
    return complex_object_schema(
        properties={
            "name": string_schema(),
            "pos": array_schema(items=number_schema()),
            "rot": array_schema(items=number_schema()),
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
            **{axis: number_schema() for axis in _AXES},
        }
    )


@dataclass(frozen=True, slots=True)
class _Pose:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class _ContactResult:
    axes: dict[str, float]
    status: dict[str, Any]


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
            if normalized not in _LOCAL_AXES:
                raise ValueError(f"{name} must be one of {', '.join(_LOCAL_AXES)}")
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
        if port_name not in {*_AXES, "status"}:
            return None
        reference_raw = await self.pull("referenceSkeleton", ctx_id=ctx_id)
        target_raw = await self.pull("targetBone", ctx_id=ctx_id)
        result = self._calculate(reference_raw, target_raw)
        if result is None:
            if port_name == "status":
                return {"valid": False, "contactValid": False, "reason": "missing_or_invalid_input"}
            return None
        if self._require_contact and not bool(result.status["contactValid"]):
            if port_name == "status":
                return {**result.status, "valid": False, "reason": "outside_contact_radius"}
            return None
        return result.status if port_name == "status" else result.axes[port_name]

    def _calculate(self, reference_raw: Any, target_raw: Any) -> _ContactResult | None:
        bones = _parse_skeleton(reference_raw)
        origin = bones.get(self._origin_bone)
        direction = bones.get(self._direction_bone)
        tip = bones.get(self._tip_bone)
        support = bones.get(self._support_bone)
        target = _parse_pose(target_raw)
        if origin is None or direction is None or tip is None or support is None or target is None:
            return None

        axis = _normalize(_subtract(direction.position, origin.position))
        if axis is None:
            return None
        reference_length = _magnitude(_subtract(tip.position, origin.position))
        if reference_length <= _EPSILON:
            return None

        support_right = _axis_from_rotation(support.rotation, self._support_right_axis)
        support_up = _axis_from_rotation(support.rotation, self._support_up_axis)
        reference_right = _normalize(_project_on_plane(support_right, axis))
        if reference_right is None:
            reference_right = _normalize(_project_on_plane(support_up, axis))
        if reference_right is None:
            return None
        reference_forward = _normalize(_cross(reference_right, axis))
        if reference_forward is None:
            return None

        target_up = _normalize(_axis_from_rotation(target.rotation, self._target_up_axis))
        target_right = _normalize(_axis_from_rotation(target.rotation, self._target_right_axis))
        if target_up is None or target_right is None:
            return None

        delta = _subtract(target.position, origin.position)
        axial = _dot(delta, axis)
        closest_axial = _clamp(axial, 0.0, reference_length)
        closest_point = _add(origin.position, _scale(axis, closest_axial))
        radial_vector = _subtract(target.position, closest_point)
        radial_distance = _magnitude(radial_vector)
        lateral_forward = _dot(radial_vector, reference_forward)
        lateral_right = _dot(radial_vector, reference_right)
        reference_radius = reference_length * self._radius_scale
        contact_valid = (
            radial_distance <= reference_radius
            and axial >= -reference_radius
            and axial <= reference_length + reference_radius
        )

        corrected_target_right = _project_on_plane(target_right, axis)
        if _magnitude(corrected_target_right) <= _EPSILON:
            return None
        twist = _signed_angle_degrees(reference_right, corrected_target_right, axis)
        target_up_on_forward_plane = _project_on_plane(target_up, reference_forward)
        target_up_on_right_plane = _project_on_plane(target_up, reference_right)
        roll = -_signed_angle_degrees(axis, target_up_on_forward_plane, reference_forward)
        pitch = _signed_angle_degrees(axis, target_up_on_right_plane, reference_right)

        l0 = _range01(axial, self._l0_min, self._l0_max)
        if self._invert_l0:
            l0 = 1.0 - l0
        axes = {
            "L0": l0,
            "L1": _symmetric01(lateral_forward, self._lateral_range),
            "L2": _symmetric01(lateral_right, self._lateral_range),
            "R0": _symmetric01(twist, self._twist_range),
            "R1": _symmetric01(roll, self._tilt_range),
            "R2": _symmetric01(pitch, self._tilt_range),
        }
        return _ContactResult(
            axes=axes,
            status={
                "valid": True,
                "contactValid": contact_valid,
                "reason": "ok" if contact_valid else "outside_contact_radius",
                "referenceLength": reference_length,
                "referenceRadius": reference_radius,
                "axialMeters": axial,
                "radialMeters": radial_distance,
                "lateralForwardMeters": lateral_forward,
                "lateralRightMeters": lateral_right,
                "twistDegrees": twist,
                "rollDegrees": roll,
                "pitchDegrees": pitch,
                **axes,
            },
        )


def _parse_skeleton(value: Any) -> dict[str, _Pose]:
    if not isinstance(value, dict) or not isinstance(value.get("bones"), list):
        return {}
    result: dict[str, _Pose] = {}
    for raw_bone in value["bones"]:
        pose = _parse_pose(raw_bone)
        if pose is not None:
            result[pose.name] = pose
    return result


def _parse_pose(value: Any) -> _Pose | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    position_raw = value.get("pos")
    rotation_raw = value.get("rot")
    if not name or not isinstance(position_raw, list) or len(position_raw) != 3:
        return None
    if not isinstance(rotation_raw, list) or len(rotation_raw) != 4:
        return None
    position = tuple(_finite_float(item) for item in position_raw)
    rotation = tuple(_finite_float(item) for item in rotation_raw)
    if any(item is None for item in position) or any(item is None for item in rotation):
        return None
    normalized_rotation = _normalize_quaternion(tuple(float(item) for item in rotation if item is not None))
    if normalized_rotation is None:
        return None
    return _Pose(
        name=name,
        position=tuple(float(item) for item in position if item is not None),
        rotation=normalized_rotation,
    )


def _axis_from_rotation(rotation: tuple[float, float, float, float], axis_name: str) -> tuple[float, float, float]:
    sign = -1.0 if axis_name.startswith("-") else 1.0
    if axis_name.endswith("local_y"):
        local = (0.0, sign, 0.0)
    elif axis_name.endswith("local_z"):
        local = (0.0, 0.0, sign)
    else:
        local = (sign, 0.0, 0.0)
    return _rotate_vector(rotation, local)


def _signed_angle_degrees(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    axis: tuple[float, float, float],
) -> float:
    start_normalized = _normalize(start)
    end_normalized = _normalize(end)
    axis_normalized = _normalize(axis)
    if start_normalized is None or end_normalized is None or axis_normalized is None:
        return 0.0
    sine = _dot(axis_normalized, _cross(start_normalized, end_normalized))
    cosine = _clamp(_dot(start_normalized, end_normalized), -1.0, 1.0)
    return math.degrees(math.atan2(sine, cosine))


def _rotate_vector(
    quaternion: tuple[float, float, float, float], vector: tuple[float, float, float]
) -> tuple[float, float, float]:
    vector_quaternion = (0.0, vector[0], vector[1], vector[2])
    rotated = _quaternion_multiply(
        _quaternion_multiply(quaternion, vector_quaternion),
        _quaternion_inverse(quaternion),
    )
    return (rotated[1], rotated[2], rotated[3])


def _normalize_quaternion(value: tuple[float, ...]) -> tuple[float, float, float, float] | None:
    if len(value) != 4:
        return None
    magnitude = math.sqrt(sum(component * component for component in value))
    if magnitude <= _EPSILON:
        return None
    return tuple(component / magnitude for component in value)  # type: ignore[return-value]


def _quaternion_inverse(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return (value[0], -value[1], -value[2], -value[3])


def _quaternion_multiply(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def _add(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _subtract(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _scale(value: tuple[float, float, float], scalar: float) -> tuple[float, float, float]:
    return tuple(component * scalar for component in value)  # type: ignore[return-value]


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _magnitude(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _normalize(value: tuple[float, float, float]) -> tuple[float, float, float] | None:
    magnitude = _magnitude(value)
    return None if magnitude <= _EPSILON else _scale(value, 1.0 / magnitude)


def _project_on_plane(
    value: tuple[float, float, float], normal: tuple[float, float, float]
) -> tuple[float, float, float]:
    normalized_normal = _normalize(normal)
    if normalized_normal is None:
        return value
    return _subtract(value, _scale(normalized_normal, _dot(value, normalized_normal)))


def _range01(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum + _EPSILON:
        return 0.5
    return _clamp((value - minimum) / (maximum - minimum), 0.0, 1.0)


def _symmetric01(value: float, maximum: float) -> float:
    return _clamp(0.5 + value / (2.0 * maximum), 0.0, 1.0)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


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
    return result if result in _LOCAL_AXES else default


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
    version="0.1.0",
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
        *[
            F8DataPortSpec(
                name=axis,
                description=f"Normalized {axis} contact axis (0..1).",
                valueSchema=number_schema(minimum=0.0, maximum=1.0),
            )
            for axis in _AXES
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
            string_schema(default="-local_x", enum=list(_LOCAL_AXES)),
        ),
        _state(
            "supportUpAxis",
            "Support Up Axis",
            "Support bone local axis mapped to body up.",
            string_schema(default="+local_y", enum=list(_LOCAL_AXES)),
        ),
        _state(
            "targetUpAxis",
            "Target Up Axis",
            "Target bone local axis mapped to target up.",
            string_schema(default="-local_y", enum=list(_LOCAL_AXES)),
        ),
        _state(
            "targetRightAxis",
            "Target Right Axis",
            "Target bone local axis mapped to target right.",
            string_schema(default="+local_z", enum=list(_LOCAL_AXES)),
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
