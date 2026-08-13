from __future__ import annotations

from typing import Any

from .models import DEFAULT_SKELETON_UDP_PORT


def skeleton_stream_graph_build_plan(*, port: int = DEFAULT_SKELETON_UDP_PORT, goal: str = "") -> dict[str, Any]:
    resolved_goal = str(goal or "").strip() or "Preview verified UDP skeleton stream in PyStudio"
    return {
        "summary": "Create UDP skeleton stream preview graph.",
        "requirement": {
            "goal": resolved_goal,
            "serviceHints": ["f8.pyengine"],
            "operatorHints": ["f8.udp_in", "f8.skeleton_decoder", "f8.viz.three_d"],
            "dataFlowHints": ["UDP In.packet -> Skeleton Decoder.packet -> Viz 3D.skeletons"],
            "validationHints": ["sample Skeleton Decoder.skeletons on the monitor/data channel"],
            "visualizationHints": ["Viz 3D shows decoded skeletons"],
        },
        "nodes": [
            _node("f8.udp_in", "modding_udp_in", "UDP In 39540", {"bindAddress": "127.0.0.1", "port": int(port)}, 0, 0),
            _node("f8.skeleton_decoder", "modding_skeleton_decoder", "Skeleton Decoder", {}, 280, 0),
            _node("f8.viz.three_d", "modding_viz_3d", "Viz 3D", {}, 560, 0),
        ],
        "connections": [
            _connection("modding_udp_in", "packet", "modding_skeleton_decoder", "packet", "Decode UDP datagrams."),
            _connection(
                "modding_skeleton_decoder",
                "skeletons",
                "modding_viz_3d",
                "skeletons",
                "Visualize all decoded skeletons.",
            ),
        ],
        "validationTargets": [
            {
                "serviceId": "f8.pyengine",
                "nodeId": "modding_skeleton_decoder",
                "port": "skeletons",
                "description": "At least one decoded skeleton model is observed.",
                "expectedMin": None,
                "expectedMax": None,
            }
        ],
        "safety": {
            "requiresVerifiedBinaryStream": True,
            "physicalOutputArmed": False,
            "physicalOutputNodeId": "",
        },
    }


def skeleton_osr_graph_build_plan(
    *,
    profile_id: str,
    port: int = DEFAULT_SKELETON_UDP_PORT,
    reference_role: str = "male",
    reference_role_index: int = 0,
    target_role: str = "female",
    target_role_index: int = 0,
    reference_bone: str = "MalePenisBase",
    target_bone: str = "Vagina",
    primary_axis: str = "local_y",
    serial_port: str = "COM4",
) -> dict[str, Any]:
    normalized_profile_id = str(profile_id or "").strip()
    if not normalized_profile_id:
        raise ValueError("profile_id is required for a stable OSR graph")
    return {
        "summary": "Build a verified Unity skeleton-to-OSR L0 graph with disarmed serial output.",
        "requirement": {
            "goal": "Convert stable Unity character motion into a safe OSR TCode L0 stream",
            "serviceHints": ["f8.pyengine", "f8.studio"],
            "operatorHints": [
                "f8.udp_in",
                "f8.skeleton_decoder",
                "f8.skeleton_selector",
                "f8.bone_selector",
                "f8.relative_pose_axes",
                "f8.stream_watchdog",
                "f8.envelope",
                "f8.range_map",
                "f8.smooth_filter",
                "f8.rate_limiter",
                "f8.tcode",
                "f8.viz.tcode",
                "f8.serial_out",
            ],
            "dataFlowHints": [
                "UDP -> decode -> stable role selectors -> bone selectors -> relative pose L0",
                "L0 -> envelope -> output range -> smoothing -> rate limit -> TCode",
                "TCode -> visualization and disabled serial output",
            ],
            "validationHints": [
                "verify skeletons and L0 on monitor/data before enabling Serial Out",
                "watchdog blocks serial exec when any skeleton sample is older than 250 ms",
            ],
            "visualizationHints": ["Viz 3D shows skeletons", "TCode Viz shows generated L0 without hardware"],
        },
        "nodes": [
            _node("f8.udp_in", "modding_udp_in", f"UDP In {port}", {"bindAddress": "127.0.0.1", "port": int(port)}, 0, 0),
            _node("f8.skeleton_decoder", "modding_skeleton_decoder", "Skeleton Decoder", {"cleanupAfterMs": 1000}, 260, 0),
            _node("f8.viz.three_d", "modding_viz_3d", "Skeleton Preview", {}, 520, -220),
            _node(
                "f8.skeleton_selector",
                "modding_reference_selector",
                "Reference Character",
                {
                    "profileId": normalized_profile_id,
                    "role": str(reference_role).lower(),
                    "roleIndex": int(reference_role_index),
                    "allowLegacyFallback": False,
                },
                520,
                0,
            ),
            _node(
                "f8.skeleton_selector",
                "modding_target_selector",
                "Target Character",
                {
                    "profileId": normalized_profile_id,
                    "role": str(target_role).lower(),
                    "roleIndex": int(target_role_index),
                    "allowLegacyFallback": False,
                },
                520,
                180,
            ),
            _node("f8.bone_selector", "modding_reference_bone", "Reference Bone", {"target": reference_bone}, 780, 0),
            _node("f8.bone_selector", "modding_target_bone", "Target Bone", {"target": target_bone}, 780, 180),
            _node(
                "f8.relative_pose_axes",
                "modding_relative_pose",
                "Relative Pose Axes",
                {"primaryAxis": primary_axis, "invertPrimary": False},
                1040,
                80,
            ),
            _node("f8.envelope", "modding_l0_envelope", "L0 Calibration", {"method": "EMA", "min_span": 0.02}, 1300, 80),
            _node(
                "f8.range_map",
                "modding_l0_range",
                "L0 Output Range",
                {"inMin": 0.0, "inMax": 1.0, "outMin": 0.05, "outMax": 0.95, "curve": "LINEAR"},
                1560,
                80,
            ),
            _node(
                "f8.smooth_filter",
                "modding_l0_smooth",
                "L0 Smooth",
                {"filter_type": "EMA", "ema_alpha": 0.35},
                1820,
                80,
            ),
            _node(
                "f8.rate_limiter",
                "modding_l0_limit",
                "L0 Rate Limit",
                {"inMin": 0.0, "inMax": 1.0, "maxRateUp": 1.5, "maxRateDown": 1.5, "maxAccel": 8.0},
                2080,
                80,
            ),
            _node("f8.tcode", "modding_tcode", "OSR TCode", {"intervalMs": 20}, 2340, 80),
            _node("f8.viz.tcode", "modding_tcode_viz", "TCode Preview", {}, 2600, -40),
            _node(
                "f8.stream_watchdog",
                "modding_watchdog",
                "Skeleton Watchdog",
                {"timeoutMs": 250},
                1040,
                360,
            ),
            _node("f8.tick", "modding_tick", "Output Tick 50 Hz", {"tickMs": 20}, 1300, 360),
            _node(
                "f8.serial_out",
                "modding_serial_out",
                "OSR Serial Out (Disarmed)",
                {"enabled": False, "port": str(serial_port), "baudrate": 115200},
                2600,
                180,
            ),
        ],
        "connections": [
            _connection("modding_udp_in", "packet", "modding_skeleton_decoder", "packet", "Decode exporter packets."),
            _connection("modding_skeleton_decoder", "skeletons", "modding_viz_3d", "skeletons", "Preview skeletons."),
            _connection("modding_skeleton_decoder", "skeletons", "modding_reference_selector", "skeletons", "Select the stable reference role."),
            _connection("modding_skeleton_decoder", "skeletons", "modding_target_selector", "skeletons", "Select the stable target role."),
            _connection("modding_reference_selector", "skeleton", "modding_reference_bone", "skeleton", "Select the reference bone."),
            _connection("modding_target_selector", "skeleton", "modding_target_bone", "skeleton", "Select the target bone."),
            _connection("modding_reference_bone", "bone", "modding_relative_pose", "referenceBone", "Use reference pose."),
            _connection("modding_target_bone", "bone", "modding_relative_pose", "targetBone", "Use target pose."),
            _connection("modding_relative_pose", "L0", "modding_l0_envelope", "value", "Calibrate raw axial motion."),
            _connection("modding_l0_envelope", "normalized", "modding_l0_range", "value", "Apply safe output travel."),
            _connection("modding_l0_range", "value", "modding_l0_smooth", "value", "Smooth normalized travel."),
            _connection("modding_l0_smooth", "value", "modding_l0_limit", "value", "Limit speed and acceleration."),
            _connection("modding_l0_limit", "value", "modding_tcode", "L0", "Generate OSR L0 commands."),
            _connection("modding_tcode", "tcode", "modding_tcode_viz", "tcode", "Preview TCode without hardware."),
            _connection("modding_tcode", "tcode", "modding_serial_out", "value", "Stage TCode at the disabled serial sink."),
            _connection("modding_skeleton_decoder", "skeletons", "modding_watchdog", "value", "Check all skeleton receive timestamps."),
            _connection("modding_tick", "exec", "modding_watchdog", "check", "Evaluate freshness at 50 Hz."),
            _connection("modding_watchdog", "valid", "modding_serial_out", "exec", "Write only while the stream is fresh."),
        ],
        "validationTargets": [
            {
                "serviceId": "f8.pyengine",
                "nodeId": "modding_relative_pose",
                "port": "L0",
                "description": "Raw reference-local axial motion is present.",
                "expectedMin": None,
                "expectedMax": None,
            },
            {
                "serviceId": "f8.pyengine",
                "nodeId": "modding_l0_limit",
                "port": "value",
                "description": "Final normalized L0 remains inside 0..1.",
                "expectedMin": 0.0,
                "expectedMax": 1.0,
            },
        ],
        "stableSelectors": {
            "reference": {"profileId": normalized_profile_id, "role": str(reference_role).lower(), "roleIndex": int(reference_role_index)},
            "target": {"profileId": normalized_profile_id, "role": str(target_role).lower(), "roleIndex": int(target_role_index)},
            "referenceBone": str(reference_bone),
            "targetBone": str(target_bone),
        },
        "axis": {"mode": str(primary_axis), "output": "L0"},
        "calibration": {
            "envelope": {"method": "EMA", "minSpan": 0.02},
            "outputRange": {"minimum": 0.05, "maximum": 0.95, "inverted": False},
            "smoothing": {"method": "EMA", "alpha": 0.35},
            "rateLimit": {"upPerSecond": 1.5, "downPerSecond": 1.5, "acceleration": 8.0},
        },
        "safety": {
            "requiresVerifiedBinaryStream": True,
            "watchdogTimeoutMs": 250,
            "physicalOutputArmed": False,
            "physicalOutputNodeId": "modding_serial_out",
            "armStateField": "enabled",
        },
    }


def _node(
    node_type: str,
    node_id: str,
    name: str,
    state_values: dict[str, Any],
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "nodeType": node_type,
        "nodeId": node_id,
        "name": name,
        "role": name,
        "stateValues": state_values,
        "position": [float(x), float(y)],
    }


def _connection(
    from_node_id: str,
    from_port: str,
    to_node_id: str,
    to_port: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "fromNodeId": from_node_id,
        "fromPort": from_port,
        "toNodeId": to_node_id,
        "toPort": to_port,
        "reason": reason,
    }


__all__ = ["skeleton_osr_graph_build_plan", "skeleton_stream_graph_build_plan"]
