from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, TypeVar

from .node_base import F8StudioBaseNode

from f8pysdk.command import command_input_port_name, command_output_port_name
from f8pysdk.specs import F8OperatorSpec, F8StateAccess
from f8pysdk.specs import schema_default, schema_type

from qtpy import QtCore, QtWidgets
from NodeGraphQt.errors import NodePropertyError, PortError, PortRegistrationError
from NodeGraphQt.nodes.base_node import NodeBaseWidget

from NodeGraphQt.constants import (
    Z_VAL_NODE,
    NodePropWidgetEnum,
)

from .port_painter import (
    COMMAND_PORT_COLOR,
    EXEC_PORT_COLOR,
    STATE_PORT_COLOR,
    data_port_color,
    draw_exec_port,
    draw_square_port,
)
from .service_basenode import F8StudioServiceNodeItem
from .service_spec_sync import build_command_port as _build_command_port_impl
from .items.inline_command_panel import ensure_inline_command_rows as _ensure_inline_command_rows_impl

logger = logging.getLogger(__name__)
WidgetT = TypeVar("WidgetT", bound=NodeBaseWidget)
_SPEC_FIELD_ERRORS = (AttributeError, TypeError, ValueError)
_NODEGRAPH_API_ERRORS = (
    AttributeError,
    KeyError,
    RuntimeError,
    TypeError,
    ValueError,
    NodePropertyError,
    PortError,
    PortRegistrationError,
)
_QT_ACCESS_ERRORS = (AttributeError, RuntimeError, TypeError)
_POSITION_READ_ERRORS = (AttributeError, IndexError, RuntimeError, TypeError, ValueError)
_BRIDGE_QUERY_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True)
class _InputPortDefinition:
    color: tuple[int, int, int]
    painter_func: Callable[..., None] | None = None
    multi_input: bool = False


@dataclass(frozen=True)
class _OutputPortDefinition:
    color: tuple[int, int, int]
    painter_func: Callable[..., None] | None = None
    multi_output: bool = True


def _spec_name(item: Any) -> str:
    try:
        return str(item.name or "").strip()
    except _SPEC_FIELD_ERRORS:
        return ""


def _spec_description(item: Any) -> str:
    try:
        return str(item.description or "").strip()
    except _SPEC_FIELD_ERRORS:
        return ""


def _show_on_node(item: Any) -> bool:
    try:
        return bool(item.showOnNode)
    except _SPEC_FIELD_ERRORS:
        return False


def _state_access(field: Any) -> Any | None:
    try:
        return field.access
    except _SPEC_FIELD_ERRORS:
        return None


def _value_schema(field: Any) -> Any | None:
    try:
        return field.valueSchema
    except _SPEC_FIELD_ERRORS:
        return None


def _schema_default_value(value_schema: Any) -> Any:
    try:
        return schema_default(value_schema)
    except _SPEC_FIELD_ERRORS:
        return None


def _node_item_id(node_item: Any) -> str:
    try:
        return str(node_item.id or "").strip()
    except _NODEGRAPH_API_ERRORS:
        return ""


def _operator_service_id(node: Any) -> str:
    try:
        return str(node.svcId or "").strip()
    except _NODEGRAPH_API_ERRORS:
        return ""


def _port_has_connections(port: Any | None) -> bool:
    if port is None:
        return False
    try:
        return bool(port.connected_ports())
    except _NODEGRAPH_API_ERRORS:
        logger.debug("Failed to inspect operator port connections.", exc_info=True)
        return False


def _remove_orphaned_port_items(
    *,
    view: Any,
    port_items: dict[Any, Any],
    valid_port_views: set[Any],
) -> None:
    try:
        scene = view.scene()
    except _QT_ACCESS_ERRORS:
        logger.debug("Failed to inspect operator node scene during orphaned port cleanup.", exc_info=True)
        scene = None

    for port_item in list(port_items.keys()):
        if port_item in valid_port_views:
            continue
        text_item = port_items.pop(port_item, None)
        if text_item is None:
            continue
        try:
            port_item.setParentItem(None)
            text_item.setParentItem(None)
            if scene is not None:
                scene.removeItem(port_item)
                scene.removeItem(text_item)
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to remove orphaned operator port graphics.", exc_info=True)


class F8StudioOperatorBaseNode(F8StudioBaseNode):
    """
    Base class for all operator nodes (nodes that are intended to live inside
    a container).

    This class is intentionally small: container binding is orchestrated by
    `F8StudioGraph`, while the view-level `_container_item` link is managed by
    the container item.
    """

    svcId: Any

    def __init__(self, qgraphics_item=None):
        _nodeitem_cls = qgraphics_item or F8StudioOperatorNodeItem
        assert issubclass(
            _nodeitem_cls, F8StudioOperatorNodeItem
        ), "F8StudioOperatorBaseNode requires a F8StudioOperatorNodeItem or subclass."
        super().__init__(qgraphics_item=_nodeitem_cls)
        assert isinstance(self.spec, F8OperatorSpec), "F8StudioOperatorBaseNode requires F8OperatorSpec"

        self.set_port_deletion_allowed(True)

        self._build_exec_port()
        self._build_data_port()
        self._build_state_port()
        self._build_command_port()
        self._build_state_properties()

    def _build_exec_port(self):
        for p in self.ordered_exec_port_names(is_in=True):
            self.add_input(
                f"[E]{p}",
                multi_input=False,
                color=EXEC_PORT_COLOR,
                painter_func=draw_exec_port,
            )

        for p in self.ordered_exec_port_names(is_in=False):
            self.add_output(
                f"{p}[E]",
                multi_output=False,
                color=EXEC_PORT_COLOR,
                painter_func=draw_exec_port,
            )

    def _build_data_port(self):

        for p in self.ordered_data_port_specs(is_in=True):
            name = _spec_name(p)
            if not name or not self.data_port_show_on_node(name, is_in=True):
                continue
            self.add_input(
                f"[D]{name}",
                multi_input=False,
                color=data_port_color(p),
            )

        for p in self.ordered_data_port_specs(is_in=False):
            name = _spec_name(p)
            if not name or not self.data_port_show_on_node(name, is_in=False):
                continue
            self.add_output(
                f"{name}[D]",
                multi_output=True,
                color=data_port_color(p),
            )

    def _build_state_port(self):

        for s in self.effective_state_fields():
            name = _spec_name(s)
            if not name or not _show_on_node(s):
                continue
            access = _state_access(s)

            if access in (F8StateAccess.rw, F8StateAccess.wo):
                self.add_input(
                    f"[S]{name}",
                    multi_input=False,
                    color=STATE_PORT_COLOR,
                    painter_func=draw_square_port,
                )

            if access in (F8StateAccess.rw, F8StateAccess.ro):
                self.add_output(
                    f"{name}[S]",
                    multi_output=True,
                    color=STATE_PORT_COLOR,
                    painter_func=draw_square_port,
                )

    def _build_command_port(self) -> None:
        _build_command_port_impl(self)

    def _build_state_properties(self) -> None:
        for s in self.effective_state_fields() or []:
            name = _spec_name(s)
            if not name:
                continue
            value_schema = _value_schema(s)
            default_value = _schema_default_value(value_schema)
            widget_type, items, prop_range = self._state_widget_for_schema(value_schema)
            tooltip = _spec_description(s) or None
            has_prop = False
            try:
                has_prop = bool(self.has_property(name))  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError):
                has_prop = False
            if not has_prop:
                try:
                    self.create_property(
                        name,
                        default_value,
                        items=items,
                        range=prop_range,
                        widget_type=widget_type,
                        widget_tooltip=tooltip,
                        tab="State",
                    )
                except _NODEGRAPH_API_ERRORS:
                    logger.exception("Failed to create operator state property '%s'", name)
                    continue
            self._ensure_state_property_metadata(
                name=name,
                widget_type=widget_type,
                items=items,
                prop_range=prop_range,
                tooltip=tooltip,
            )

    def _ensure_state_property_metadata(
        self,
        *,
        name: str,
        widget_type: int,
        items: list[str] | None,
        prop_range: tuple[float, float] | None,
        tooltip: str | None,
    ) -> None:
        graph_model = self.graph.model if self.graph is not None else None
        if graph_model is None:
            return
        attrs: dict[str, dict[str, dict[str, Any]]] = {
            self.type_: {
                name: {
                    "widget_type": widget_type,
                    "tab": "State",
                }
            }
        }
        if items:
            attrs[self.type_][name]["items"] = list(items)
        if prop_range is not None:
            attrs[self.type_][name]["range"] = prop_range
        if tooltip:
            attrs[self.type_][name]["tooltip"] = tooltip
        try:
            graph_model.set_node_common_properties(attrs)
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to ensure operator state property metadata: node=%s field=%s", self.type_, name)

    def state_bool(self, name: str, *, default: bool) -> bool:
        value = self.get_property(name)
        if value is None:
            return bool(default)
        return bool(value)

    def set_state_bool(self, name: str, enabled: bool) -> None:
        self.set_property(name, bool(enabled), push_undo=False)

    def widget_by_name(self, name: str, widget_type: type[WidgetT]) -> WidgetT | None:
        widget = self.view.widgets.get(name)
        if isinstance(widget, widget_type):
            return widget
        return None

    def sync_bool_state_to_widget(
        self,
        *,
        state_name: str,
        default: bool,
        widget_name: str,
        widget_type: type[WidgetT],
        apply_value: Callable[[WidgetT, bool], None],
    ) -> None:
        widget = self.widget_by_name(widget_name, widget_type)
        if widget is None:
            return
        apply_value(widget, self.state_bool(state_name, default=default))

    @staticmethod
    def _state_widget_for_schema(value_schema) -> tuple[int, list[str] | None, tuple[float, float] | None]:
        """
        Best-effort mapping from F8DataTypeSchema -> NodeGraphQt property widget.
        """
        if value_schema is None:
            return NodePropWidgetEnum.QTEXT_EDIT.value, None, None
        try:
            t = schema_type(value_schema)
        except _SPEC_FIELD_ERRORS:
            t = ""

        # enum choice.
        try:
            enum_items = list(value_schema.enum or [])
        except _SPEC_FIELD_ERRORS:
            enum_items = []
        if enum_items:
            return NodePropWidgetEnum.QCOMBO_BOX.value, [str(x) for x in enum_items], None

        if t == "boolean":
            return NodePropWidgetEnum.QCHECK_BOX.value, None, None
        if t == "integer":
            # Avoid QSpinBox widgets due to PySide6 incompatibilities in NodeGraphQt's PropSpinBox.
            return NodePropWidgetEnum.QLINE_EDIT.value, None, None
        if t == "number":
            # Avoid QDoubleSpinBox widgets due to PySide6 incompatibilities in NodeGraphQt's PropDoubleSpinBox.
            return NodePropWidgetEnum.QLINE_EDIT.value, None, None
        if t == "string":
            return NodePropWidgetEnum.QLINE_EDIT.value, None, None

        # object/array/any (and unknowns) edited as JSON-ish text.
        return NodePropWidgetEnum.QTEXT_EDIT.value, None, None

    def sync_from_spec(self) -> None:
        """
        Rebuild runtime aspects derived from `self.spec`:
        - ports (exec/data/state)
        - state properties (adds any missing fields)
        """
        self._ensure_port_deletion_allowed()
        desired_inputs, desired_outputs = self._desired_ports_from_spec()
        self._sync_ports_to_definitions(desired_inputs=desired_inputs, desired_outputs=desired_outputs)
        self._cleanup_orphaned_port_graphics()
        self._build_state_properties()
        self._redraw_after_spec_sync()

    def _ensure_port_deletion_allowed(self) -> None:
        try:
            if not self.port_deletion_allowed():
                self.set_port_deletion_allowed(True)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Failed to enable operator port deletion during spec sync.", exc_info=True)

    def _desired_ports_from_spec(self) -> tuple[dict[str, _InputPortDefinition], dict[str, _OutputPortDefinition]]:
        desired_inputs: dict[str, _InputPortDefinition] = {}
        desired_outputs: dict[str, _OutputPortDefinition] = {}

        for p in list(self.ordered_exec_port_names(is_in=True) or []):
            desired_inputs[f"[E]{p}"] = _InputPortDefinition(color=EXEC_PORT_COLOR, painter_func=draw_exec_port)
        for p in list(self.ordered_exec_port_names(is_in=False) or []):
            desired_outputs[f"{p}[E]"] = _OutputPortDefinition(
                color=EXEC_PORT_COLOR,
                painter_func=draw_exec_port,
                multi_output=False,
            )

        for p in list(self.ordered_data_port_specs(is_in=True) or []):
            n = _spec_name(p)
            if not n:
                continue
            port_name = f"[D]{n}"
            show_on_node = self.data_port_show_on_node(n, is_in=True)
            if not show_on_node:
                try:
                    if _port_has_connections(self.get_input(port_name)):
                        show_on_node = True
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug("Failed to inspect existing input port %r during operator spec sync.", port_name, exc_info=True)
            if show_on_node:
                desired_inputs[port_name] = _InputPortDefinition(color=data_port_color(p))

        for p in list(self.ordered_data_port_specs(is_in=False) or []):
            n = _spec_name(p)
            if not n:
                continue
            port_name = f"{n}[D]"
            show_on_node = self.data_port_show_on_node(n, is_in=False)
            if not show_on_node:
                try:
                    if _port_has_connections(self.get_output(port_name)):
                        show_on_node = True
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug("Failed to inspect existing output port %r during operator spec sync.", port_name, exc_info=True)
            if show_on_node:
                desired_outputs[port_name] = _OutputPortDefinition(color=data_port_color(p))

        for s in list(self.ordered_state_field_specs() or []):
            name = _spec_name(s)
            if not name or not _show_on_node(s):
                continue
            access = _state_access(s)
            if access in (F8StateAccess.rw, F8StateAccess.wo):
                desired_inputs[f"[S]{name}"] = _InputPortDefinition(color=STATE_PORT_COLOR, painter_func=draw_square_port)
            if access in (F8StateAccess.rw, F8StateAccess.ro):
                desired_outputs[f"{name}[S]"] = _OutputPortDefinition(color=STATE_PORT_COLOR, painter_func=draw_square_port)

        for command in list(self.ordered_command_specs() or []):
            name = _spec_name(command)
            if not name or not _show_on_node(command):
                continue
            desired_inputs[command_input_port_name(name)] = _InputPortDefinition(
                color=COMMAND_PORT_COLOR,
                painter_func=draw_square_port,
            )
            desired_outputs[command_output_port_name(name)] = _OutputPortDefinition(
                color=COMMAND_PORT_COLOR,
                painter_func=draw_square_port,
            )

        return desired_inputs, desired_outputs

    def _sync_ports_to_definitions(
        self,
        *,
        desired_inputs: dict[str, _InputPortDefinition],
        desired_outputs: dict[str, _OutputPortDefinition],
    ) -> None:
        # Important: NodeGraphQt `delete_input/delete_output` does not clear
        # pipes. If ports are removed while still connected, NodeGraphQt can
        # leave dangling pipes in the scene, crashing during paint.

        # Remove ports that no longer exist in spec (disconnect first).
        current_input_names = set(self.inputs().keys())
        current_output_names = set(self.outputs().keys())
        desired_input_names = set(desired_inputs.keys())
        desired_output_names = set(desired_outputs.keys())

        for name in sorted(current_input_names - desired_input_names):
            self._delete_stale_input_port(name)

        for name in sorted(current_output_names - desired_output_names):
            self._delete_stale_output_port(name)

        # Add new ports from spec.
        current_input_names = set(self.inputs().keys())
        current_output_names = set(self.outputs().keys())

        for name in sorted(desired_input_names - current_input_names):
            self._add_missing_input_port(name, desired_inputs[name])

        for name in sorted(desired_output_names - current_output_names):
            self._add_missing_output_port(name, desired_outputs[name])

    def _delete_stale_input_port(self, name: str) -> None:
        try:
            port = self.get_input(name)
            if port is not None:
                self._clear_stale_port_connections(port=port, port_name=name)
            self.delete_input(name)
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to delete input port %r", name)

    def _delete_stale_output_port(self, name: str) -> None:
        try:
            port = self.get_output(name)
            if port is not None:
                self._clear_stale_port_connections(port=port, port_name=name)
            self.delete_output(name)
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to delete output port %r", name)

    @staticmethod
    def _clear_stale_port_connections(*, port: Any, port_name: str) -> None:
        try:
            port.clear_connections(push_undo=False, emit_signal=False)
        except _NODEGRAPH_API_ERRORS:
            logger.debug("Failed to clear stale operator port connections for %r.", port_name, exc_info=True)

    def _add_missing_input_port(self, name: str, definition: _InputPortDefinition) -> None:
        try:
            self.add_input(
                name,
                multi_input=definition.multi_input,
                color=definition.color,
                painter_func=definition.painter_func,
            )
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to add input port %r", name)

    def _add_missing_output_port(self, name: str, definition: _OutputPortDefinition) -> None:
        try:
            self.add_output(
                name,
                multi_output=definition.multi_output,
                color=definition.color,
                painter_func=definition.painter_func,
            )
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to add output port %r", name)

    def _cleanup_orphaned_port_graphics(self) -> None:
        # Best-effort cleanup for any orphaned port items left on the QGraphics node.
        try:
            view = self.view
            valid_in_views = {p.view for p in self.input_ports()}
            valid_out_views = {p.view for p in self.output_ports()}

            try:
                input_items = view._input_items
            except _NODEGRAPH_API_ERRORS:
                input_items = None
            if isinstance(input_items, dict):
                _remove_orphaned_port_items(view=view, port_items=input_items, valid_port_views=valid_in_views)

            try:
                output_items = view._output_items
            except _NODEGRAPH_API_ERRORS:
                output_items = None
            if isinstance(output_items, dict):
                _remove_orphaned_port_items(view=view, port_items=output_items, valid_port_views=valid_out_views)
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed cleanup for orphaned operator port graphics")

    def _redraw_after_spec_sync(self) -> None:
        try:
            self.view.draw_node()
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Failed to redraw operator node after sync_from_spec")

class F8StudioOperatorNodeItem(F8StudioServiceNodeItem):
    """
    Operator node item: reuse the service-node layout (grouped ports + inline collapsible
    state widgets + persisted expand state), but without service process controls.

    This intentionally disables the service process toolbar only.
    Operator command rows reuse the same inline-row presentation as service nodes.
    """

    def __init__(self, name="node", parent=None):
        super().__init__(name, parent)
        # Operator nodes may be canvas-managed (no container). Containers bind by
        # setting `view._container_item`; keep it always defined to avoid crashes
        # during interactive moves before binding.
        self._container_item = None
        self._drag_start_xy: tuple[float, float] | None = None
        self._drag_start_container_id: str = ""

    def itemChange(self, change, value):  # type: ignore[override]
        """
        Keep operator interaction behaviors:
        - highlight pipes on selection
        """
        if change == QtWidgets.QGraphicsItem.ItemSelectedChange and self.scene():
            self._sync_selection_pipe_state(selected=bool(value))
            self._sync_selection_z_value()

        return super().itemChange(change, value)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton:
            self._record_drag_start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() != QtCore.Qt.LeftButton:
            return
        self._handle_left_mouse_release()

    def _sync_selection_pipe_state(self, *, selected: bool) -> None:
        try:
            self.reset_pipes()
            if selected:
                self.highlight_pipes()
        except _NODEGRAPH_API_ERRORS:
            logger.debug("Failed to update operator pipe selection highlight.", exc_info=True)

    def _sync_selection_z_value(self) -> None:
        try:
            self.setZValue(Z_VAL_NODE)
            if not self.selected:
                self.setZValue(Z_VAL_NODE + 1)
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to update operator selection z value.", exc_info=True)

    def _record_drag_start(self) -> None:
        self._drag_start_xy = self._current_xy_pos()
        self._drag_start_container_id = self._current_container_id()

    def _current_xy_pos(self) -> tuple[float, float] | None:
        try:
            pos = self.xy_pos
            return float(pos[0]), float(pos[1])
        except _POSITION_READ_ERRORS:
            logger.debug("Failed to record operator drag start position for node id=%s.", _node_item_id(self), exc_info=True)
            return None

    def _current_container_id(self) -> str:
        container = self._container_item
        if container is None:
            return ""
        try:
            return str(container.id or "").strip()
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to record operator drag start container for node id=%s.", _node_item_id(self), exc_info=True)
            return ""

    def _handle_left_mouse_release(self) -> None:
        start_xy = self._drag_start_xy
        start_container_id = str(self._drag_start_container_id or "")
        self._drag_start_xy = None
        self._drag_start_container_id = ""
        if start_xy is None:
            return
        graph = self._graph_for_drop_rebind()
        if graph is None:
            return
        self._notify_operator_drop(graph=graph, start_xy=start_xy, start_container_id=start_container_id)

    def _graph_for_drop_rebind(self) -> Any | None:
        viewer = self.viewer()
        if viewer is None:
            return None
        try:
            return viewer.f8_graph
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to access operator graph during drop rebind for node id=%s.", _node_item_id(self), exc_info=True)
            return None

    def _notify_operator_drop(self, *, graph: Any, start_xy: tuple[float, float], start_container_id: str) -> None:
        try:
            graph.on_operator_drop(
                node_id=_node_item_id(self),
                start_pos=start_xy,
                start_container_id=start_container_id,
            )
        except _NODEGRAPH_API_ERRORS:
            logger.exception("Operator drop rebind failed for node id=%s", _node_item_id(self))

    def _ensure_service_toolbar(self, viewer: Any | None) -> None:  # type: ignore[override]
        return

    def _position_service_toolbar(self) -> None:  # type: ignore[override]
        return

    def _service_id(self) -> str:  # type: ignore[override]
        node = self._backend_node()
        if node is not None:
            service_id = _operator_service_id(node)
            if service_id:
                return service_id
        container = self._container_item
        if container is not None:
            try:
                service_id = str(container.id or "").strip()
            except _QT_ACCESS_ERRORS:
                logger.debug("Failed to read operator container service id for node id=%s.", _node_item_id(self), exc_info=True)
                service_id = ""
            if service_id:
                return service_id
        return ""

    def _is_service_running(self) -> bool:  # type: ignore[override]
        bridge = self._bridge()
        service_id = self._service_id()
        if bridge is None or not service_id:
            return False
        try:
            return bool(bridge.is_service_running(service_id))
        except _BRIDGE_QUERY_ERRORS:
            logger.debug("Failed to query operator service process state for service id=%s.", service_id, exc_info=True)
            return False

    def _on_bridge_service_process_state(self, service_id: str, running: bool) -> None:  # type: ignore[override]
        if str(service_id or "").strip() != self._service_id():
            return
        try:
            self._refresh_inline_command_rows()
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to refresh operator command rows after service state change: service_id=%s.", service_id, exc_info=True)
        try:
            QtCore.QTimer.singleShot(0, self.draw_node)
        except _QT_ACCESS_ERRORS:
            logger.debug("Failed to schedule operator redraw after service state change: service_id=%s.", service_id, exc_info=True)

    def _ensure_inline_command_rows(self) -> None:  # type: ignore[override]
        _ensure_inline_command_rows_impl(self)
