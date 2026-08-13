from __future__ import annotations

CORE_NODE_LIBRARY_CATEGORY_LABELS: dict[str, str] = {
    "svc": "Services",
}

PYENGINE_NODE_LIBRARY_CATEGORY_LABELS: dict[str, str] = {
    "f8.pyengine.input": "PyEngine / Input",
    "f8.pyengine.output": "PyEngine / Output",
    "f8.pyengine.execution": "PyEngine / Execution",
    "f8.pyengine.flow": "PyEngine / Flow",
    "f8.pyengine.analysis": "PyEngine / Analysis",
    "f8.pyengine.signal": "PyEngine / Signal",
    "f8.pyengine.expr": "PyEngine / Expr",
    "f8.pyengine.motion": "PyEngine / Motion",
    "f8.pyengine.debug": "PyEngine / Debug",
    "f8.pyengine.services": "PyEngine / Services",
}

CPPENGINE_NODE_LIBRARY_CATEGORY_LABELS: dict[str, str] = {
    "f8.cppengine.analysis": "CppEngine / Analysis",
    "f8.cppengine.debug": "CppEngine / Debug",
    "f8.cppengine.execution": "CppEngine / Execution",
    "f8.cppengine.expr": "CppEngine / Expr",
    "f8.cppengine.flow": "CppEngine / Flow",
    "f8.cppengine.io": "CppEngine / I/O",
    "f8.cppengine.motion": "CppEngine / Motion",
    "f8.cppengine.output": "CppEngine / Output",
    "f8.cppengine.playback": "CppEngine / Playback",
    "f8.cppengine.script": "CppEngine / Script",
    "f8.cppengine.signal": "CppEngine / Signal",
    "f8.cppengine.state": "CppEngine / State",
    "f8.cppengine.wave": "CppEngine / Wave",
}

PYSTUDIO_NODE_LIBRARY_CATEGORY_LABELS: dict[str, str] = {
    "f8.pystudio.viz": "PyStudio / Viz",
    "f8.pystudio.control": "PyStudio / Control",
    "f8.pystudio.expr": "PyStudio / Expr",
    "f8.pystudio.canvas": "PyStudio / Canvas",
    "f8.pystudio.routing": "PyStudio / Routing",
}


def display_node_category_label(category: str) -> str:
    category_id = str(category or "").strip()
    if not category_id:
        return "uncategorized"
    if category_id in CORE_NODE_LIBRARY_CATEGORY_LABELS:
        return CORE_NODE_LIBRARY_CATEGORY_LABELS[category_id]
    if category_id in PYENGINE_NODE_LIBRARY_CATEGORY_LABELS:
        return PYENGINE_NODE_LIBRARY_CATEGORY_LABELS[category_id]
    if category_id in CPPENGINE_NODE_LIBRARY_CATEGORY_LABELS:
        return CPPENGINE_NODE_LIBRARY_CATEGORY_LABELS[category_id]
    if category_id in PYSTUDIO_NODE_LIBRARY_CATEGORY_LABELS:
        return PYSTUDIO_NODE_LIBRARY_CATEGORY_LABELS[category_id]
    return category_id


__all__ = [
    "CORE_NODE_LIBRARY_CATEGORY_LABELS",
    "CPPENGINE_NODE_LIBRARY_CATEGORY_LABELS",
    "PYENGINE_NODE_LIBRARY_CATEGORY_LABELS",
    "PYSTUDIO_NODE_LIBRARY_CATEGORY_LABELS",
    "display_node_category_label",
]
