from __future__ import annotations

import logging
from typing import Any

from NodeGraphQt.constants import NodePropWidgetEnum

from f8pysdk.specs import F8StateAccess
from f8pysdk.command import command_input_port_name, command_output_port_name
from f8pysdk.specs import schema_default, schema_type

from .items.node_item_core import state_field_info as _state_field_info
from .port_painter import COMMAND_PORT_COLOR, DATA_PORT_COLOR, STATE_PORT_COLOR, draw_square_port

logger = logging.getLogger(__name__)


def build_data_port(node: Any) -> None:
    for port in node.spec.dataInPorts:
        if not node.data_port_show_on_node(str(port.name or ""), is_in=True):
            continue
        node.add_input(
            f"[D]{port.name}",
            multi_input=False,
            color=DATA_PORT_COLOR,
        )

    for port in node.spec.dataOutPorts:
        if not node.data_port_show_on_node(str(port.name or ""), is_in=False):
            continue
        node.add_output(
            f"{port.name}[D]",
            multi_output=True,
            color=DATA_PORT_COLOR,
        )


def build_state_port(node: Any) -> None:
    for state_field in node.effective_state_fields():
        info = _state_field_info(state_field)
        if info is None or not info.show_on_node:
            continue

        if info.access in [F8StateAccess.rw, F8StateAccess.wo] or info.access_str in {"rw", "wo"}:
            node.add_input(
                f"[S]{info.name}",
                multi_input=False,
                color=STATE_PORT_COLOR,
                painter_func=draw_square_port,
            )

        if info.access in [F8StateAccess.rw, F8StateAccess.ro] or info.access_str in {"rw", "ro"}:
            node.add_output(
                f"{info.name}[S]",
                multi_output=True,
                color=STATE_PORT_COLOR,
                painter_func=draw_square_port,
            )


def build_command_port(node: Any) -> None:
    for command in list(node.effective_commands() or []):
        name = str(command.name or "").strip()
        if not name:
            continue
        if not bool(command.showOnNode):
            continue
        node.add_input(
            command_input_port_name(name),
            multi_input=False,
            color=COMMAND_PORT_COLOR,
            painter_func=draw_square_port,
        )
        node.add_output(
            command_output_port_name(name),
            multi_output=True,
            color=COMMAND_PORT_COLOR,
            painter_func=draw_square_port,
        )


def ensure_state_property_metadata(
    node: Any,
    *,
    name: str,
    widget_type: int,
    items: list[str] | None,
    prop_range: tuple[float, float] | None,
    tooltip: str | None,
) -> None:
    graph_model = node.graph.model if node.graph is not None else None
    if graph_model is None:
        return
    attrs: dict[str, dict[str, dict[str, Any]]] = {
        node.type_: {
            name: {
                "widget_type": widget_type,
                "tab": "State",
            }
        }
    }
    if items:
        attrs[node.type_][name]["items"] = list(items)
    if prop_range is not None:
        attrs[node.type_][name]["range"] = prop_range
    if tooltip:
        attrs[node.type_][name]["tooltip"] = tooltip
    try:
        graph_model.set_node_common_properties(attrs)
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        logger.exception("Failed to ensure service state property metadata: node=%s field=%s", node.type_, name)


def state_widget_for_schema(value_schema: Any) -> tuple[int, list[str] | None, tuple[float, float] | None]:
    """
    Best-effort mapping from F8DataTypeSchema -> NodeGraphQt property widget.
    """
    if value_schema is None:
        return NodePropWidgetEnum.QTEXT_EDIT.value, None, None
    try:
        schema_kind = str(schema_type(value_schema) or "").strip().lower()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        schema_kind = ""
    if not schema_kind:
        try:
            raw_type = value_schema.type
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            raw_type = None
        if raw_type is not None:
            try:
                schema_kind = str(raw_type.value).strip().lower()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                schema_kind = str(raw_type).strip().lower()

    enum_items: list[Any] = []
    try:
        enum_items = list(value_schema.enum or [])
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        enum_items = []
    if enum_items:
        return NodePropWidgetEnum.QCOMBO_BOX.value, [str(item) for item in enum_items], None

    if schema_kind == "boolean":
        return NodePropWidgetEnum.QCHECK_BOX.value, None, None
    if schema_kind == "integer":
        return NodePropWidgetEnum.QLINE_EDIT.value, None, None
    if schema_kind == "number":
        return NodePropWidgetEnum.QLINE_EDIT.value, None, None
    if schema_kind == "string":
        return NodePropWidgetEnum.QLINE_EDIT.value, None, None

    return NodePropWidgetEnum.QTEXT_EDIT.value, None, None


def build_state_properties(node: Any) -> None:
    for state_field in node.effective_state_fields() or []:
        info = _state_field_info(state_field)
        if info is None:
            continue
        try:
            default_value = schema_default(info.value_schema)
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            default_value = None
        widget_type, items, prop_range = state_widget_for_schema(info.value_schema)
        tooltip = info.tooltip or None
        has_prop = False
        try:
            has_prop = bool(node.has_property(info.name))  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            has_prop = False
        if not has_prop:
            node.create_property(
                info.name,
                default_value,
                items=items,
                range=prop_range,
                widget_type=widget_type,
                widget_tooltip=tooltip,
                tab="State",
            )
        ensure_state_property_metadata(
            node,
            name=info.name,
            widget_type=widget_type,
            items=items,
            prop_range=prop_range,
            tooltip=tooltip,
        )


def sync_from_spec(node: Any) -> None:
    """
    Rebuild runtime aspects derived from `node.spec`:
    - ports (exec/data/state)
    - state properties (adds any missing fields)
    """
    if not node.port_deletion_allowed():
        node.set_port_deletion_allowed(True)

    desired_inputs: dict[str, dict[str, Any]] = {}
    desired_outputs: dict[str, dict[str, Any]] = {}

    def _port_has_connections(port: Any) -> bool:
        if port is None:
            return False
        try:
            return bool(port.connected_ports())
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            try:
                return bool(port.connected_ports)
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                return False

    for port in list(node.spec.dataInPorts or []):
        try:
            name = str(port.name or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            name = ""
        if not name:
            continue
        port_name = f"[D]{name}"
        show_on_node = node.data_port_show_on_node(name, is_in=True)
        if not show_on_node:
            try:
                if _port_has_connections(node.get_input(port_name)):
                    show_on_node = True
            except (AttributeError, RuntimeError, TypeError):
                pass
        if show_on_node:
            desired_inputs[port_name] = {"color": DATA_PORT_COLOR, "multi_input": False}

    for port in list(node.spec.dataOutPorts or []):
        try:
            name = str(port.name or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            name = ""
        if not name:
            continue
        port_name = f"{name}[D]"
        show_on_node = node.data_port_show_on_node(name, is_in=False)
        if not show_on_node:
            try:
                if _port_has_connections(node.get_output(port_name)):
                    show_on_node = True
            except (AttributeError, RuntimeError, TypeError):
                pass
        if show_on_node:
            desired_outputs[port_name] = {"color": DATA_PORT_COLOR, "multi_output": True}

    for state_field in list(node.effective_state_fields() or []):
        info = _state_field_info(state_field)
        if info is None or not info.show_on_node:
            continue
        if info.access in [F8StateAccess.rw, F8StateAccess.wo] or info.access_str in {"rw", "wo"}:
            desired_inputs[f"[S]{info.name}"] = {
                "color": STATE_PORT_COLOR,
                "painter_func": draw_square_port,
                "multi_input": False,
            }
        if info.access in [F8StateAccess.rw, F8StateAccess.ro] or info.access_str in {"rw", "ro"}:
            desired_outputs[f"{info.name}[S]"] = {
                "color": STATE_PORT_COLOR,
                "painter_func": draw_square_port,
                "multi_output": True,
            }

    for command in list(node.effective_commands() or []):
        name = str(command.name or "").strip()
        if not name:
            continue
        if not bool(command.showOnNode):
            continue
        desired_inputs[command_input_port_name(name)] = {
            "color": COMMAND_PORT_COLOR,
            "painter_func": draw_square_port,
            "multi_input": False,
        }
        desired_outputs[command_output_port_name(name)] = {
            "color": COMMAND_PORT_COLOR,
            "painter_func": draw_square_port,
            "multi_output": True,
        }

    current_input_names = set(node.inputs().keys())
    current_output_names = set(node.outputs().keys())
    desired_input_names = set(desired_inputs.keys())
    desired_output_names = set(desired_outputs.keys())

    for name in sorted(current_input_names - desired_input_names):
        try:
            input_port = node.get_input(name)
            if input_port is not None:
                try:
                    input_port.clear_connections(push_undo=False, emit_signal=False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            node.delete_input(name)
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError) as exc:
            logger.warning("Failed to delete input port %r: %s", name, exc)

    for name in sorted(current_output_names - desired_output_names):
        try:
            output_port = node.get_output(name)
            if output_port is not None:
                try:
                    output_port.clear_connections(push_undo=False, emit_signal=False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            node.delete_output(name)
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError) as exc:
            logger.warning("Failed to delete output port %r: %s", name, exc)

    current_input_names = set(node.inputs().keys())
    current_output_names = set(node.outputs().keys())

    for name in sorted(desired_input_names - current_input_names):
        meta = desired_inputs.get(name) or {}
        try:
            node.add_input(
                name,
                multi_input=bool(meta.get("multi_input", False)),
                color=meta.get("color"),
                painter_func=meta.get("painter_func"),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError) as exc:
            logger.warning("Failed to add input port %r: %s", name, exc)

    for name in sorted(desired_output_names - current_output_names):
        meta = desired_outputs.get(name) or {}
        try:
            node.add_output(
                name,
                multi_output=bool(meta.get("multi_output", True)),
                color=meta.get("color"),
                painter_func=meta.get("painter_func"),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError) as exc:
            logger.warning("Failed to add output port %r: %s", name, exc)

    try:
        view = node.view
        valid_in_views = {port.view for port in node.input_ports()}
        valid_out_views = {port.view for port in node.output_ports()}

        try:
            input_items = view._input_items
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            input_items = None
        if isinstance(input_items, dict):
            for port_item in list(input_items.keys()):
                if port_item in valid_in_views:
                    continue
                text_item = input_items.pop(port_item, None)
                if text_item is None:
                    continue
                try:
                    port_item.setParentItem(None)
                    text_item.setParentItem(None)
                    if view.scene() is not None:
                        view.scene().removeItem(port_item)
                        view.scene().removeItem(text_item)
                except RuntimeError:
                    pass

        try:
            output_items = view._output_items
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            output_items = None
        if isinstance(output_items, dict):
            for port_item in list(output_items.keys()):
                if port_item in valid_out_views:
                    continue
                text_item = output_items.pop(port_item, None)
                if text_item is None:
                    continue
                try:
                    port_item.setParentItem(None)
                    text_item.setParentItem(None)
                    if view.scene() is not None:
                        view.scene().removeItem(port_item)
                        view.scene().removeItem(text_item)
                except RuntimeError:
                    pass
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        logger.debug("sync_from_spec orphan port-item cleanup failed", exc_info=True)

    build_state_properties(node)

    try:
        node.view.draw_node()
    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
        logger.debug("sync_from_spec draw_node failed", exc_info=True)
