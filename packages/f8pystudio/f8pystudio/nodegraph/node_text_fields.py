from __future__ import annotations

import logging
from typing import Any

from qtpy import QtWidgets

from ..editor_assist.session import EditorSessionKey
from ..ui_notifications import show_warning

logger = logging.getLogger(__name__)


def studio_session_key(graph: Any, node_id: str, field_name: str) -> EditorSessionKey | None:
    if graph is None:
        return None
    node_id_s = str(node_id or "").strip()
    field_s = str(field_name or "").strip()
    if not node_id_s or not field_s:
        return None
    return EditorSessionKey.studio_node(
        graph_id=f"graph:{id(graph)}",
        node_id=node_id_s,
        field_name=field_s,
    )


def resolve_node(graph: Any, node_id: str) -> Any | None:
    if graph is None:
        return None
    nid = str(node_id or "").strip()
    if not nid:
        return None
    try:
        return graph.get_node_by_id(nid)  # type: ignore[attr-defined]
    except Exception:
        logger.exception("graph.get_node_by_id failed nodeId=%s", nid)
        return None


def get_node_text(graph: Any, node_id: str, field_name: str) -> str:
    node = resolve_node(graph, node_id)
    key = str(field_name or "").strip()
    if node is None or not key:
        return ""
    try:
        value = node.get_property(key)  # type: ignore[attr-defined]
    except KeyError:
        return ""
    except Exception:
        logger.exception("node.get_property failed nodeId=%s field=%s", str(node_id or ""), key)
        return ""
    return "" if value is None else str(value)


def set_node_text(
    graph: Any,
    node_id: str,
    field_name: str,
    text: str,
    *,
    push_undo: bool = True,
    warning_parent: QtWidgets.QWidget | None = None,
) -> None:
    nid = str(node_id or "").strip()
    key = str(field_name or "").strip()
    node = resolve_node(graph, nid)
    if node is None or not key:
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Target node/field not found.\nnodeId={nid}\nfield={key}",
        )
        return

    try:
        _ = node.get_property(key)  # type: ignore[attr-defined]
    except KeyError:
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Target field does not exist on node.\nnodeId={nid}\nfield={key}",
        )
        return
    except Exception as exc:
        logger.exception("node.get_property failed before set nodeId=%s field=%s", nid, key)
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Failed to validate save target.\nnodeId={nid}\nfield={key}\nerror={type(exc).__name__}: {exc}",
        )
        return

    value = str(text or "")
    try:
        try:
            node.set_property(key, value, push_undo=bool(push_undo))  # type: ignore[attr-defined]
        except TypeError:
            node.set_property(key, value)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.exception("node.set_property failed nodeId=%s field=%s", nid, key)
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Failed to write code to node.\nnodeId={nid}\nfield={key}\nerror={type(exc).__name__}: {exc}",
        )
