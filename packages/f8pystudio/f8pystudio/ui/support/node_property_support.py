from __future__ import annotations

import logging
from typing import Any

from f8pysdk.specs import F8DataTypeSchema
from f8pysdk.codec import dump_json

from ...editor_assist.protocol import editor_assist_context_for_field
from ...editor_assist.workspace import EditorAssistContext
from ...nodegraph.node_text_fields import resolve_node
from ...nodegraph.state_schema import schema_type_any
from ..dialogs.schema_builder_dialog import schema_from_json_obj, schema_to_json_obj

logger = logging.getLogger(__name__)


def state_input_is_connected(node: Any, field_name: str) -> bool:
    name = str(field_name or "").strip()
    if not name:
        return False
    port = node.get_input(f"[S]{name}")
    if port is None:
        return False
    try:
        return bool(port.connected_ports())
    except AttributeError as exc:
        logger.warning("state input connection has stale node reference; field=%s err=%s", name, exc)
        try:
            graph = node.graph
        except (AttributeError, RuntimeError, TypeError):
            return False
        try:
            valid_ids = {str(item.id or "").strip() for item in list(graph.all_nodes() or []) if str(item.id or "").strip()}
        except (AttributeError, RuntimeError, TypeError):
            return False
        connected = port.model.connected_ports
        stale_ids = [node_id for node_id in list(connected.keys()) if str(node_id or "") not in valid_ids]
        for node_id in stale_ids:
            connected.pop(node_id, None)
        return False


def get_node_spec(node: Any) -> Any | None:
    try:
        return node.spec
    except Exception:
        return None


def build_editor_assist_context(
    graph: Any,
    *,
    node_id: str,
    prop_name: str,
    language: str = "python",
) -> EditorAssistContext | None:
    field_name = str(prop_name or "").strip()
    if not field_name:
        return None
    node = resolve_node(graph, node_id)
    if node is None:
        return None
    spec = get_node_spec(node)
    if spec is None:
        return None
    return editor_assist_context_for_field(
        spec,
        field_kind="state",
        field_key=field_name,
        language=language,
        node=node,
    )


def node_missing_lock_info(node: Any) -> tuple[bool, str]:
    if node is None:
        return False, ""
    try:
        model = node.model
    except Exception:
        return False, ""
    try:
        f8_sys = model.f8_sys
    except Exception:
        return False, ""
    if not isinstance(f8_sys, dict):
        return False, ""
    missing_locked = bool(f8_sys.get("missingLocked"))
    missing_type = str(f8_sys.get("missingType") or "").strip()
    return missing_locked, missing_type


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if not isinstance(value, (bytes, bytearray)):
        try:
            return to_jsonable(value.value)
        except AttributeError:
            pass
    try:
        dumped = dump_json(value, mode="json")
    except Exception:
        try:
            dumped = dump_json(value)
        except Exception:
            dumped = None
    if dumped is not None:
        return to_jsonable(dumped)
    return str(value)


def schema_to_json_obj_loose(schema: Any) -> Any:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return dict(schema)
    if isinstance(schema, list):
        return list(schema)
    if isinstance(schema, (str, int, float, bool)):
        return schema
    schema_kind = schema_type_any(schema)
    if schema_kind in {"string", "number", "integer", "boolean", "null", "object", "array", "any"}:
        try:
            return schema_to_json_obj(schema)
        except Exception:
            logger.exception("strict schema_to_json_obj failed for F8DataTypeSchema")
            return None
    try:
        typed_schema = schema_from_json_obj_loose(schema)
    except Exception:
        typed_schema = None
    if typed_schema is not None:
        try:
            return schema_to_json_obj(typed_schema)
        except Exception:
            logger.exception("strict schema_to_json_obj failed after coercion")
            return None
    try:
        return dump_json(schema, mode="json")
    except (AttributeError, TypeError, ValueError):
        return str(schema)


def schema_from_json_obj_loose(obj: Any) -> F8DataTypeSchema:
    if isinstance(obj, dict):
        return schema_from_json_obj(obj)
    schema_kind = schema_type_any(obj)
    if schema_kind in {"string", "number", "integer", "boolean", "null", "object", "array", "any"}:
        return obj
    return schema_from_json_obj(obj)


__all__ = [
    "build_editor_assist_context",
    "get_node_spec",
    "node_missing_lock_info",
    "schema_from_json_obj_loose",
    "schema_to_json_obj_loose",
    "state_input_is_connected",
    "to_jsonable",
]
