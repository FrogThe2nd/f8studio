from .colors import DEFAULT_SERIES_COLORS, series_color, series_colors
from .skeletons import (
    COCO17_EDGES,
    HUMAN36M_17_EDGES,
    MEDIAPIPE_POSE_33_EDGES,
    UNITY_HUMANOID_NAME_EDGES,
    skeleton_edges_for_nodes,
    skeleton_edges_for_protocol,
)

__all__ = [
    "COCO17_EDGES",
    "DEFAULT_SERIES_COLORS",
    "HUMAN36M_17_EDGES",
    "MEDIAPIPE_POSE_33_EDGES",
    "UNITY_HUMANOID_NAME_EDGES",
    "series_color",
    "series_colors",
    "skeleton_edges_for_nodes",
    "skeleton_edges_for_protocol",
]
