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
            "operatorHints": ["f8.udp_in", "f8.skeleton_decoder", "viz three d"],
            "dataFlowHints": ["UDP In.packet -> Skeleton Decoder.packet -> Viz 3D.skeletons"],
            "validationHints": ["sample Skeleton Decoder.skeletons on the monitor/data channel"],
            "visualizationHints": ["Viz 3D shows decoded skeletons"],
        },
        "nodes": [
            {
                "nodeType": "f8.udp_in",
                "nodeId": "modding_udp_in",
                "name": "UDP In 39540",
                "role": "Receive skeleton packets from the game modding exporter.",
                "stateValues": {"bindAddress": "127.0.0.1", "port": int(port)},
                "position": [0.0, 0.0],
            },
            {
                "nodeType": "f8.skeleton_decoder",
                "nodeId": "modding_skeleton_decoder",
                "name": "Skeleton Decoder",
                "role": "Decode UDP skeleton packets into skeleton model payloads.",
                "stateValues": {},
                "position": [280.0, 0.0],
            },
            {
                "nodeType": "viz.three_d",
                "nodeId": "modding_viz_3d",
                "name": "Viz 3D",
                "role": "Visualize decoded skeletons.",
                "stateValues": {},
                "position": [560.0, 0.0],
            },
        ],
        "connections": [
            {
                "fromNodeId": "modding_udp_in",
                "fromPort": "packet",
                "toNodeId": "modding_skeleton_decoder",
                "toPort": "packet",
                "reason": "Feed incoming UDP packets into the skeleton decoder.",
            },
            {
                "fromNodeId": "modding_skeleton_decoder",
                "fromPort": "skeletons",
                "toNodeId": "modding_viz_3d",
                "toPort": "skeletons",
                "reason": "Show all decoded skeleton models in 3D.",
            },
        ],
        "validationTargets": [
            {
                "serviceId": "",
                "nodeId": "modding_skeleton_decoder",
                "port": "skeletons",
                "description": "At least one decoded skeleton model is observed.",
                "expectedMin": None,
                "expectedMax": None,
            }
        ],
    }


__all__ = ["skeleton_stream_graph_build_plan"]
