from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol

from qtpy import QtWidgets

from ..editor_assist.session import EditorSessionKey
from ..ui.support.ui_notifications import show_warning

logger = logging.getLogger(__name__)

_NODE_TEXT_GRAPH_LOOKUP_ERRORS = (RuntimeError, TypeError, ValueError)
_NODE_TEXT_PROPERTY_READ_ERRORS = (RuntimeError, TypeError, ValueError)
_NODE_TEXT_PROPERTY_WRITE_ERRORS = (RuntimeError, TypeError, ValueError)


class NodeTextPropertyNode(Protocol):
    def get_property(self, name: str) -> object: ...

    def set_property(self, name: str, value: object, *, push_undo: bool = True) -> None: ...


class NodeTextGraph(Protocol):
    def get_node_by_id(self, node_id: str) -> NodeTextPropertyNode | None: ...


@dataclass(frozen=True)
class NodeTextEditorBinding:
    value_getter: Callable[[], str]
    value_setter: Callable[[str], bool]
    target_exists: Callable[[], bool]
    session_key: EditorSessionKey


def studio_session_key(graph: NodeTextGraph | None, node_id: str, field_name: str) -> EditorSessionKey | None:
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


def resolve_node(graph: NodeTextGraph | None, node_id: str) -> NodeTextPropertyNode | None:
    if graph is None:
        return None
    nid = str(node_id or "").strip()
    if not nid:
        return None
    try:
        return graph.get_node_by_id(nid)
    except _NODE_TEXT_GRAPH_LOOKUP_ERRORS as exc:
        logger.exception("graph.get_node_by_id failed nodeId=%s", nid, exc_info=exc)
        return None


def get_node_text(graph: NodeTextGraph | None, node_id: str, field_name: str) -> str:
    node = resolve_node(graph, node_id)
    key = str(field_name or "").strip()
    if node is None or not key:
        return ""
    try:
        value = node.get_property(key)
    except KeyError:
        return ""
    except _NODE_TEXT_PROPERTY_READ_ERRORS as exc:
        logger.exception("node.get_property failed nodeId=%s field=%s", str(node_id or ""), key, exc_info=exc)
        return ""
    return "" if value is None else str(value)


def node_text_target_exists(graph: NodeTextGraph | None, node_id: str, field_name: str) -> bool:
    node = resolve_node(graph, node_id)
    key = str(field_name or "").strip()
    if node is None or not key:
        return False
    try:
        _ = node.get_property(key)
    except KeyError:
        return False
    except _NODE_TEXT_PROPERTY_READ_ERRORS as exc:
        logger.exception(
            "node.get_property failed while checking text target nodeId=%s field=%s",
            str(node_id or ""),
            key,
            exc_info=exc,
        )
        return False
    return True


def set_node_text(
    graph: NodeTextGraph | None,
    node_id: str,
    field_name: str,
    text: str,
    *,
    push_undo: bool = True,
    warning_parent: QtWidgets.QWidget | None = None,
) -> bool:
    nid = str(node_id or "").strip()
    key = str(field_name or "").strip()
    node = resolve_node(graph, nid)
    if node is None or not key:
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Target node/field not found.\nnodeId={nid}\nfield={key}",
        )
        return False

    try:
        _ = node.get_property(key)
    except KeyError:
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Target field does not exist on node.\nnodeId={nid}\nfield={key}",
        )
        return False
    except _NODE_TEXT_PROPERTY_READ_ERRORS as exc:
        logger.exception("node.get_property failed before set nodeId=%s field=%s", nid, key, exc_info=exc)
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Failed to validate save target.\nnodeId={nid}\nfield={key}\nerror={type(exc).__name__}: {exc}",
        )
        return False

    value = str(text or "")
    try:
        node.set_property(key, value, push_undo=bool(push_undo))
    except _NODE_TEXT_PROPERTY_WRITE_ERRORS as exc:
        logger.exception("node.set_property failed nodeId=%s field=%s", nid, key, exc_info=exc)
        show_warning(
            warning_parent,
            "Code Save Failed",
            f"Failed to write code to node.\nnodeId={nid}\nfield={key}\nerror={type(exc).__name__}: {exc}",
        )
        return False
    return True


def node_text_editor_binding(
    graph: NodeTextGraph | None,
    node_id: str,
    field_name: str,
    *,
    warning_parent: QtWidgets.QWidget | None = None,
) -> NodeTextEditorBinding | None:
    session_key = studio_session_key(graph, node_id, field_name)
    if session_key is None:
        return None
    nid = str(node_id or "").strip()
    key = str(field_name or "").strip()
    return NodeTextEditorBinding(
        value_getter=lambda: get_node_text(graph, nid, key),
        value_setter=lambda text: set_node_text(
            graph,
            nid,
            key,
            str(text or ""),
            push_undo=True,
            warning_parent=warning_parent,
        ),
        target_exists=lambda: node_text_target_exists(graph, nid, key),
        session_key=session_key,
    )
