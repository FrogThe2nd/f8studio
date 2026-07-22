from __future__ import annotations

from enum import StrEnum


class NodeRole(StrEnum):
    SOURCE = "source"
    DETECT = "detect"
    SHAPE = "shape"
    OUTPUT = "output"
    VIEW = "view"
    ADVANCED = "advanced"


NODE_ROLE_LABELS: dict[NodeRole, str] = {
    NodeRole.SOURCE: "Source",
    NodeRole.DETECT: "Detect",
    NodeRole.SHAPE: "Shape",
    NodeRole.OUTPUT: "Output",
    NodeRole.VIEW: "View",
    NodeRole.ADVANCED: "Advanced",
}


_CATEGORY_ROLES: dict[str, NodeRole] = {
    "f8.pyengine.input": NodeRole.SOURCE,
    "f8.pyengine.analysis": NodeRole.DETECT,
    "f8.pyengine.signal": NodeRole.SHAPE,
    "f8.pyengine.motion": NodeRole.SHAPE,
    "f8.pyengine.expr": NodeRole.SHAPE,
    "f8.pyengine.output": NodeRole.OUTPUT,
    "f8.pyengine.debug": NodeRole.VIEW,
    "f8.pystudio.viz": NodeRole.VIEW,
}


def node_role_for_palette_category(category: str) -> NodeRole:
    category_id = str(category or "").strip().lower()
    return _CATEGORY_ROLES.get(category_id, NodeRole.ADVANCED)


__all__ = [
    "NODE_ROLE_LABELS",
    "NodeRole",
    "node_role_for_palette_category",
]
