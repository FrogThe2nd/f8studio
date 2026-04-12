from __future__ import annotations

PYENGINE_NODE_LIBRARY_CATEGORY_LABELS: dict[str, str] = {
    "f8.pyengine.input": "PyEngine / Input",
    "f8.pyengine.output": "PyEngine / Output",
    "f8.pyengine.execution": "PyEngine / Execution",
    "f8.pyengine.signal": "PyEngine / Signal",
    "f8.pyengine.expr": "PyEngine / Expr",
    "f8.pyengine.motion": "PyEngine / Motion",
    "f8.pyengine.debug": "PyEngine / Debug",
}


def display_node_category_label(category: str) -> str:
    category_id = str(category or "").strip()
    if not category_id:
        return "uncategorized"
    return str(PYENGINE_NODE_LIBRARY_CATEGORY_LABELS.get(category_id, category_id))
