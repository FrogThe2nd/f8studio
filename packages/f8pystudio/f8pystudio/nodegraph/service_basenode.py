from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from typing import Any

from .node_base import F8StudioBaseNode

from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8StateAccess
from f8pysdk.command_state import parse_command_port_name

from collections import OrderedDict

from f8pysdk.schema_helpers import schema_default, schema_type

from qtpy import QtCore, QtGui, QtWidgets

from NodeGraphQt.constants import (
    ICON_NODE_BASE,
    ITEM_CACHE_MODE,
    Z_VAL_NODE,
    LayoutDirectionEnum,
    NodeEnum,
    PortEnum,
    NodePropWidgetEnum,
)
from NodeGraphQt.nodes.base_node import NodeBaseWidget
from NodeGraphQt.qgraphics.node_abstract import AbstractNodeItem
from NodeGraphQt.qgraphics.node_overlay_disabled import XDisabledItem
from NodeGraphQt.qgraphics.node_text_item import NodeTextItem
from NodeGraphQt.qgraphics.port import CustomPortItem, PortItem

from .port_painter import draw_square_port, DATA_PORT_COLOR, STATE_PORT_COLOR
from .service_process_toolbar import ServiceProcessToolbar
from .service_bridge_protocol import ServiceBridge
from .viewer import F8StudioNodeViewer
from .items.node_item_core import (
    StateFieldInfo as _StateFieldInfo,
    port_name as _port_name,
    state_field_info as _state_field_info,
)
from .items.service_toolbar_host import (
    current_service_id as _toolbar_current_service_id_impl,
    ensure_service_toolbar as _ensure_service_toolbar_impl,
    position_service_toolbar as _position_service_toolbar_impl,
    refresh_service_identity_bindings as _refresh_service_identity_bindings_impl,
)
from .items.embedded_resize_contract import (
    ResizableEmbeddedWidget,
    clamp_content_size,
    content_rect_with_minimum,
)
from .items.inline_command_panel import (
    ensure_inline_command_rows as _ensure_inline_command_rows_impl,
    refresh_inline_command_rows as _refresh_inline_command_rows_impl,
    invoke_command as _invoke_command_impl,
    prompt_command_args as _prompt_command_args_impl,
)
from .items.state_inline_controls import (
    build_state_inline_control as _build_state_inline_control_impl,
    ensure_state_inline_controls as _ensure_state_inline_controls_impl,
    is_state_inline_input_connected as _is_state_inline_input_connected_impl,
    refresh_state_inline_control_read_only as _refresh_state_inline_control_read_only_impl,
    refresh_state_inline_option_pools as _refresh_state_inline_option_pools_impl,
    set_state_inline_control_read_only as _set_state_inline_control_read_only_impl,
    sync_state_inline_controls_from_graph_property as _sync_state_inline_controls_from_graph_property_impl,
    toggle_state_inline_section as _toggle_state_inline_section_impl,
)
from .items.service_node_port_schema_actions import (
    data_port_tooltip as _data_port_tooltip_impl,
    display_port_label as _display_port_label_impl,
    find_data_port_spec as _find_data_port_spec_impl,
    find_effective_state_field as _find_effective_state_field_impl,
    find_state_field_spec as _find_state_field_spec_impl,
    on_port_right_click as _on_port_right_click_impl,
    open_data_port_editor_dialog as _open_data_port_editor_dialog_impl,
    open_data_port_schema_dialog as _open_data_port_schema_dialog_impl,
    open_state_field_editor_dialog as _open_state_field_editor_dialog_impl,
    open_state_field_schema_dialog as _open_state_field_schema_dialog_impl,
    parse_schema_port_view_name as _parse_schema_port_view_name_impl,
    port_group as _port_group_impl,
    port_tooltip_text as _port_tooltip_text_impl,
    refresh_port_tooltips as _refresh_port_tooltips_impl,
    schema_brief as _schema_brief_impl,
    schema_enum_items as _schema_enum_items_impl,
    schema_numeric_range as _schema_numeric_range_impl,
    state_port_tooltip as _state_port_tooltip_impl,
)
from .items.service_node_graph_hooks import (
    backend_node as _backend_node_impl,
    bridge as _bridge_impl,
    current_service_id as _current_service_id_impl,
    ensure_bridge_process_hook as _ensure_bridge_process_hook_impl,
    ensure_graph_property_hook as _ensure_graph_property_hook_impl,
    graph as _graph_impl,
    is_service_running as _is_service_running_impl,
    on_bridge_service_process_state as _on_bridge_service_process_state_impl,
    select_node_from_embedded_widget as _select_node_from_embedded_widget_impl,
    viewer_safe as _viewer_safe_impl,
)
from .items.service_node_ports import (
    add_input as _add_input_impl,
    add_output as _add_output_impl,
    add_port as _add_port_impl,
    add_widget as _add_widget_impl,
    delete_input as _delete_input_impl,
    delete_output as _delete_output_impl,
    delete_port as _delete_port_impl,
    from_dict as _ports_from_dict_impl,
    get_input_text_item as _get_input_text_item_impl,
    get_output_text_item as _get_output_text_item_impl,
    get_widget as _get_widget_impl,
    has_widget as _has_widget_impl,
    widgets as _widgets_impl,
)
from .service_spec_sync import (
    build_command_port as _build_command_port_impl,
    build_data_port as _build_data_port_impl,
    build_state_port as _build_state_port_impl,
    build_state_properties as _build_state_properties_impl,
    ensure_state_property_metadata as _ensure_state_property_metadata_impl,
    state_widget_for_schema as _state_widget_for_schema_impl,
    sync_from_spec as _sync_from_spec_impl,
)
from ..widgets.state_controls.schema_introspect import (
    schema_enum_items as _shared_schema_enum_items,
    schema_numeric_range as _shared_schema_numeric_range,
)
from ..widgets.schema_builder import SchemaBuilderDialog, schema_from_json_obj as _schema_from_json_obj

logger = logging.getLogger(__name__)

# Service and operator command rows now share the same inline-panel layout model.
# There is no separate command button panel anymore; `showOnNode` controls both
# the inline row and its paired command ports.

@dataclass
class _LayoutMetric:
    cache_key: str = ""
    width: float = 0.0
    height: float = 0.0


@dataclass
class _StatePanelLayoutMetric(_LayoutMetric):
    header_height: float = 0.0


def _clear_embedded_text_selection(widget: QtWidgets.QWidget | None) -> None:
    if widget is None:
        return

    line_edits = widget.findChildren(QtWidgets.QLineEdit)
    for line_edit in line_edits:
        try:
            line_edit.deselect()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            line_edit.setCursorPosition(len(line_edit.text()))
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            line_edit.clearFocus()
        except (AttributeError, RuntimeError, TypeError):
            pass

    plain_text_edits = widget.findChildren(QtWidgets.QPlainTextEdit)
    for plain_text_edit in plain_text_edits:
        try:
            cursor = plain_text_edit.textCursor()
            cursor.clearSelection()
            plain_text_edit.setTextCursor(cursor)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            plain_text_edit.clearFocus()
        except (AttributeError, RuntimeError, TypeError):
            pass

    text_edits = widget.findChildren(QtWidgets.QTextEdit)
    for text_edit in text_edits:
        try:
            cursor = text_edit.textCursor()
            cursor.clearSelection()
            text_edit.setTextCursor(cursor)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            text_edit.clearFocus()
        except (AttributeError, RuntimeError, TypeError):
            pass


class _F8PortMouseMixin:
    """
    Shared right-click behavior for node ports.

    - Right click on a port should never start live pipe creation.
    - Data ports may show a schema-focused context menu handled by the node item.
    """

    def _on_right_click(self, screen_pos: QtCore.QPoint) -> None:
        node_item = self.parentItem()
        if isinstance(node_item, F8StudioServiceNodeItem):
            node_item._on_port_right_click(self, screen_pos)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == QtCore.Qt.RightButton:
            self._on_right_click(event.screenPos())
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):  # type: ignore[override]
        self._on_right_click(event.screenPos())
        event.accept()


class F8StudioPortItem(_F8PortMouseMixin, PortItem):
    pass


class F8StudioCustomPortItem(_F8PortMouseMixin, CustomPortItem):
    pass


class F8StudioServiceBaseNode(F8StudioBaseNode):
    """
    Base class for all single-node service (nodes that are intended to live without
    a container).

    This class is intentionally small: container binding is orchestrated by
    `F8StudioGraph`, while the view-level `_container_item` link is managed by
    the container item.
    """

    svcId: Any

    def __init__(self, qgraphics_item=None):
        _nodeitem_cls = qgraphics_item or F8StudioServiceNodeItem
        assert issubclass(
            _nodeitem_cls, F8StudioServiceNodeItem
        ), "F8StudioServiceBaseNode requires a F8StudioServiceNodeItem or subclass."
        super().__init__(qgraphics_item=_nodeitem_cls)
        assert isinstance(self.spec, F8ServiceSpec), "F8StudioServiceBaseNode requires F8ServiceSpec"

        self.set_port_deletion_allowed(True)

        self._build_data_port()
        self._build_state_port()
        self._build_command_port()
        self._build_state_properties()

    def _build_data_port(self):
        _build_data_port_impl(self)

    def _build_state_port(self):
        _build_state_port_impl(self)

    def _build_command_port(self):
        _build_command_port_impl(self)

    def _build_state_properties(self) -> None:
        _build_state_properties_impl(self)

    def _ensure_state_property_metadata(
        self,
        *,
        name: str,
        widget_type: int,
        items: list[str] | None,
        prop_range: tuple[float, float] | None,
        tooltip: str | None,
    ) -> None:
        _ensure_state_property_metadata_impl(
            self,
            name=name,
            widget_type=widget_type,
            items=items,
            prop_range=prop_range,
            tooltip=tooltip,
        )

    @staticmethod
    def _state_widget_for_schema(value_schema) -> tuple[int, list[str] | None, tuple[float, float] | None]:
        return _state_widget_for_schema_impl(value_schema)

    def sync_from_spec(self) -> None:
        _sync_from_spec_impl(self)


class F8StudioServiceNodeItem(AbstractNodeItem):
    """
    Base Node item.

    Args:
        name (str): name displayed on the node.
        parent (QtWidgets.QGraphicsItem): parent item.
    """

    def __init__(self, name="node", parent=None):
        super(F8StudioServiceNodeItem, self).__init__(name, parent)
        
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsScenePositionChanges, True)

        pixmap = QtGui.QPixmap(ICON_NODE_BASE)
        if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToHeight(NodeEnum.ICON_SIZE.value, QtCore.Qt.SmoothTransformation)
        self._properties["icon"] = ICON_NODE_BASE
        self._icon_item = QtWidgets.QGraphicsPixmapItem(pixmap, self)
        self._icon_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self._text_item = NodeTextItem(self.name, self)
        self._x_item = XDisabledItem(self, "DISABLED")
        self._input_items = OrderedDict()
        self._output_items = OrderedDict()
        self._widgets = OrderedDict()
        self._proxy_mode = False
        self._proxy_mode_threshold = 70
        self._state_inline_proxies: OrderedDict[str, QtWidgets.QGraphicsProxyWidget] = OrderedDict()
        self._state_inline_controls: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._state_inline_bindings: OrderedDict[str, Any] = OrderedDict()
        self._state_inline_updaters: OrderedDict[str, Any] = OrderedDict()
        self._state_inline_toggles: OrderedDict[str, QtWidgets.QToolButton] = OrderedDict()
        self._state_inline_headers: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._state_inline_bodies: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._state_inline_expanded: dict[str, bool] = {}
        self._state_inline_option_pools: dict[str, str] = {}
        self._state_row_y: dict[str, tuple[float, float]] = {}
        self._command_inline_proxies: OrderedDict[str, QtWidgets.QGraphicsProxyWidget] = OrderedDict()
        self._command_inline_headers: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._command_inline_buttons: OrderedDict[str, QtWidgets.QAbstractButton] = OrderedDict()
        self._command_inline_descriptions: dict[str, str] = {}
        self._command_inline_serials: dict[str, str] = {}
        self._graph_prop_hooked: bool = False
        self._bridge_proc_hooked: bool = False
        self._state_inline_ctrl_serial: dict[str, str] = {}
        self._tooltip_filters: list[QtCore.QObject] = []
        self._svc_toolbar_proxy: QtWidgets.QGraphicsProxyWidget | None = None
        self._ports_end_y: float | None = None
        self._open_code_editors: list[QtWidgets.QDialog] = []
        self._layout_metrics_ready: bool = False
        self._embedded_widget_metrics: dict[str, _LayoutMetric] = {}
        self._state_panel_metrics: dict[str, _StatePanelLayoutMetric] = {}
        self._command_row_metrics: dict[str, _StatePanelLayoutMetric] = {}

    def _backend_node(self) -> Any | None:
        return _backend_node_impl(self)

    def _is_state_inline_input_connected(self, field_name: str) -> bool:
        return _is_state_inline_input_connected_impl(self, field_name)

    @staticmethod
    def _set_state_inline_control_read_only(control: QtWidgets.QWidget, *, read_only: bool) -> None:
        _set_state_inline_control_read_only_impl(control, read_only=read_only)

    def refresh_state_inline_control_read_only(self) -> None:
        _refresh_state_inline_control_read_only_impl(self)

    def _graph(self) -> Any | None:
        return _graph_impl(self)

    def _viewer_safe(self) -> Any | None:
        return _viewer_safe_impl(self)

    def _ensure_graph_property_hook(self) -> None:
        _ensure_graph_property_hook_impl(self)

    def _select_node_from_embedded_widget(self) -> None:
        _select_node_from_embedded_widget_impl(self)

    def _bridge(self) -> ServiceBridge | None:
        return _bridge_impl(self)

    def _ensure_bridge_process_hook(self) -> None:
        _ensure_bridge_process_hook_impl(self)

    def _is_service_running(self) -> bool:
        return _is_service_running_impl(self)

    def _on_bridge_service_process_state(self, service_id: str, running: bool) -> None:
        _on_bridge_service_process_state_impl(self, service_id, running)

    def _service_id(self) -> str:
        return _current_service_id_impl(self)

    def _invoke_command(self, cmd: Any) -> None:
        _invoke_command_impl(self, cmd)

    def _prompt_command_args(self, cmd: Any) -> dict[str, Any] | None:
        return _prompt_command_args_impl(self, cmd)

    def _ensure_inline_command_rows(self) -> None:
        _ensure_inline_command_rows_impl(self)

    def _refresh_inline_command_rows(self) -> None:
        _refresh_inline_command_rows_impl(self)

    def _sync_state_inline_controls_from_graph_property(self, node: Any, name: str, value: Any) -> None:
        _sync_state_inline_controls_from_graph_property_impl(self, node, name, value)

    def _refresh_state_inline_option_pools(self, changed_field: str) -> None:
        _refresh_state_inline_option_pools_impl(self, changed_field)

    def _toggle_state_inline_section(self, name: str, expanded: bool) -> None:
        _toggle_state_inline_section_impl(self, name, expanded)

    @staticmethod
    def _port_group(name: str) -> str:
        return _port_group_impl(name)

    @staticmethod
    def _display_port_label(name: str, *, max_chars: int | None = None) -> str:
        return _display_port_label_impl(name, max_chars=max_chars)

    @staticmethod
    def _schema_enum_items(value_schema: Any) -> list[str]:
        return _schema_enum_items_impl(value_schema)

    @staticmethod
    def _schema_numeric_range(value_schema: Any) -> tuple[float | None, float | None]:
        return _schema_numeric_range_impl(value_schema)

    @staticmethod
    def _parse_schema_port_view_name(view_name: str) -> tuple[str, bool, str] | None:
        return _parse_schema_port_view_name_impl(view_name)

    @staticmethod
    def _schema_brief(value_schema: Any) -> str:
        return _schema_brief_impl(value_schema)

    def _find_data_port_spec(self, *, is_in: bool, port_name: str) -> tuple[Any, int] | None:
        return _find_data_port_spec_impl(self, is_in=is_in, port_name=port_name)

    def _data_port_tooltip(self, *, is_in: bool, port_name: str) -> str:
        return _data_port_tooltip_impl(self, is_in=is_in, port_name=port_name)

    def _find_state_field_spec(self, *, field_name: str) -> tuple[Any, int] | None:
        return _find_state_field_spec_impl(self, field_name=field_name)

    def _state_port_tooltip(self, *, is_in: bool, field_name: str) -> str:
        return _state_port_tooltip_impl(self, is_in=is_in, field_name=field_name)

    def _port_tooltip_text(self, view_name: str) -> str:
        return _port_tooltip_text_impl(self, view_name)

    def _refresh_port_tooltips(self) -> None:
        _refresh_port_tooltips_impl(self)

    def _open_data_port_schema_dialog(self, *, is_in: bool, port_name: str) -> None:
        _open_data_port_schema_dialog_impl(self, is_in=is_in, port_name=port_name)

    def _open_state_field_schema_dialog(self, *, field_name: str) -> None:
        _open_state_field_schema_dialog_impl(self, field_name=field_name)

    def _open_data_port_editor_dialog(self, *, is_in: bool, port_name: str) -> None:
        _open_data_port_editor_dialog_impl(self, is_in=is_in, port_name=port_name)

    def _find_effective_state_field(self, *, field_name: str) -> Any | None:
        return _find_effective_state_field_impl(self, field_name=field_name)

    def _open_state_field_editor_dialog(self, *, field_name: str) -> None:
        _open_state_field_editor_dialog_impl(self, field_name=field_name)

    def _on_port_right_click(self, port: Any, screen_pos: QtCore.QPoint) -> None:
        _on_port_right_click_impl(self, port, screen_pos)

    def _build_state_inline_control(self, state_field: _StateFieldInfo) -> QtWidgets.QWidget:
        return _build_state_inline_control_impl(self, state_field)

    def _ensure_state_inline_controls(self) -> None:
        _ensure_state_inline_controls_impl(self)

    def post_init(self, viewer=None, pos=None):
        """
        Called after node has been added into the scene.

        Args:
            viewer (NodeGraphQt.widgets.viewer.NodeViewer): main viewer
            pos (tuple): the cursor pos if node is called with tab search.
        """
        if self.layout_direction == LayoutDirectionEnum.VERTICAL.value:
            font = QtGui.QFont()
            font.setPointSize(15)
            self.text_item.setFont(font)

            # hide port text items for vertical layout.
            if self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
                for text_item in self._input_items.values():
                    text_item.setVisible(False)
                for text_item in self._output_items.values():
                    text_item.setVisible(False)

    def _paint_horizontal(self, painter, option, widget):
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtCore.Qt.NoBrush)

        # base background.
        margin = 1.0
        rect = self.boundingRect()
        rect = QtCore.QRectF(
            rect.left() + margin, rect.top() + margin, rect.width() - (margin * 2), rect.height() - (margin * 2)
        )

        radius = 4.0
        painter.setBrush(QtGui.QColor(*self.color))
        painter.drawRoundedRect(rect, radius, radius)

        # light overlay on background when selected.
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
            painter.drawRoundedRect(rect, radius, radius)

        # node name background.
        padding = 3.0, 2.0
        text_rect = self._text_item.boundingRect()
        text_rect = QtCore.QRectF(
            text_rect.x() + padding[0],
            rect.y() + padding[1],
            rect.width() - padding[0] - margin,
            text_rect.height() - (padding[1] * 2),
        )
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
        else:
            painter.setBrush(QtGui.QColor(0, 0, 0, 80))
        painter.drawRoundedRect(text_rect, 3.0, 3.0)
        # node border
        if self.selected:
            border_width = 1.2
            border_color = QtGui.QColor(*NodeEnum.SELECTED_BORDER_COLOR.value)
        else:
            border_width = 0.8
            border_color = QtGui.QColor(*self.border_color)

        border_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.height())

        pen = QtGui.QPen(border_color, border_width)
        v = self._viewer_safe()
        zoom = None
        try:
            zoom = float(v.get_zoom()) if v is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            zoom = None
        pen.setCosmetic(bool(zoom is not None and zoom < 0.0))
        path = QtGui.QPainterPath()
        path.addRoundedRect(border_rect, radius, radius)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.restore()

    def _paint_vertical(self, painter, option, widget):
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtCore.Qt.NoBrush)

        # base background.
        margin = 1.0
        rect = self.boundingRect()
        rect = QtCore.QRectF(
            rect.left() + margin, rect.top() + margin, rect.width() - (margin * 2), rect.height() - (margin * 2)
        )

        radius = 4.0
        painter.setBrush(QtGui.QColor(*self.color))
        painter.drawRoundedRect(rect, radius, radius)

        # light overlay on background when selected.
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
            painter.drawRoundedRect(rect, radius, radius)

        # top & bottom edge background.
        padding = 2.0
        height = 10
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
        else:
            painter.setBrush(QtGui.QColor(0, 0, 0, 80))
        for y in [rect.y() + padding, rect.height() - height - 1]:
            edge_rect = QtCore.QRectF(rect.x() + padding, y, rect.width() - (padding * 2), height)
            painter.drawRoundedRect(edge_rect, 3.0, 3.0)
        # node border
        border_width = 0.8
        border_color = QtGui.QColor(*self.border_color)
        if self.selected:
            border_width = 1.2
            border_color = QtGui.QColor(*NodeEnum.SELECTED_BORDER_COLOR.value)
        border_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.height())

        pen = QtGui.QPen(border_color, border_width)
        v = self._viewer_safe()
        zoom = None
        try:
            zoom = float(v.get_zoom()) if v is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            zoom = None
        pen.setCosmetic(bool(zoom is not None and zoom < 0.0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(border_rect, radius, radius)

        painter.restore()

    def paint(self, painter, option, widget):
        """
        Draws the node base not the ports.

        Args:
            painter (QtGui.QPainter): painter used for drawing the item.
            option (QtGui.QStyleOptionGraphicsItem):
                used to describe the parameters needed to draw.
            widget (QtWidgets.QWidget): not used.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._paint_horizontal(painter, option, widget)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._paint_vertical(painter, option, widget)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

    def set_hidden_layer_link_count(self, count: int) -> None:
        _ = count
        return

    def mousePressEvent(self, event):
        """
        Re-implemented to ignore event if LMB is over port collision area.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.button() == QtCore.Qt.LeftButton:
            for p in self._input_items.keys():
                if p.hovered:
                    event.ignore()
                    return
            for p in self._output_items.keys():
                if p.hovered:
                    event.ignore()
                    return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """
        Re-implemented to ignore event if Alt modifier is pressed.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.modifiers() == QtCore.Qt.AltModifier:
            event.ignore()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """
        Re-implemented to emit "node_double_clicked" signal.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.button() == QtCore.Qt.LeftButton:
            if not self.disabled:
                # enable text item edit mode.
                items = self.scene().items(event.scenePos())
                if self._text_item in items:
                    self._text_item.set_editable(True)
                    self._text_item.setFocus()
                    event.ignore()
                    return

            viewer = self.viewer()
            if viewer:
                viewer.node_double_clicked.emit(self.id)
        super().mouseDoubleClickEvent(event)

    def _tooltip_disable(self, state):
        """
        Updates the node tooltip when the node is enabled/disabled.

        Args:
            state (bool): node disable state.
        """
        tooltip = "<b>{}</b>".format(self.name)
        if state:
            tooltip += ' <font color="red"><b>(DISABLED)</b></font>'
        tooltip += "<br/>{}<br/>".format(self.type_)
        self.setToolTip(tooltip)

    def _set_base_size(self, add_w=0.0, add_h=0.0):
        """
        Sets the initial base size for the node.

        Args:
            add_w (float): add additional width.
            add_h (float): add additional height.
        """
        old_rect = None
        try:
            old_rect = self.boundingRect()
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            old_rect = None

        w, h = self.calc_size(add_w, add_h)
        if w < NodeEnum.WIDTH.value:
            w = NodeEnum.WIDTH.value
        if h < NodeEnum.HEIGHT.value:
            h = NodeEnum.HEIGHT.value

        changed = True
        try:
            changed = bool(abs(float(w) - float(self._width)) > 0.01 or abs(float(h) - float(self._height)) > 0.01)
        except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
            changed = True

        if changed:    
            self.prepareGeometryChange()
        
        self._width, self._height = float(w), float(h)

        if not changed or old_rect is None:
            return

        new_rect = self.boundingRect()

        old_scene = self.mapToScene(old_rect).boundingRect()
        new_scene = self.mapToScene(new_rect).boundingRect()
        dirty = old_scene.united(new_scene).adjusted(-6, -6, 6, 6)
        sc = self.scene()
        if sc is not None:
            sc.update(dirty)
        v = self.viewer()
        if v is not None:
            v.viewport().update()

    def _set_text_color(self, color):
        """
        set text color.

        Args:
            color (tuple): color value in (r, g, b, a).
        """
        text_color = QtGui.QColor(*color)
        for port, text in self._input_items.items():
            text.setDefaultTextColor(text_color)
        for port, text in self._output_items.items():
            text.setDefaultTextColor(text_color)
        self._text_item.setDefaultTextColor(text_color)

    def activate_pipes(self):
        """
        active pipe color.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.activate()

    def highlight_pipes(self):
        """
        Highlight pipe color.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.highlight()

    def reset_pipes(self):
        """
        Reset all the pipe colors.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.reset()

    def _refresh_pipe_visual_state(self) -> None:
        """
        Force connected pipes to repaint after disabled state changes.
        """
        ports = self.inputs + self.outputs
        seen_pipe_ids: set[int] = set()
        for port in ports:
            try:
                connected_pipes = list(port.connected_pipes)
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                continue
            for pipe in connected_pipes:
                pipe_key = id(pipe)
                if pipe_key in seen_pipe_ids:
                    continue
                seen_pipe_ids.add(pipe_key)
                try:
                    pipe.update()
                except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                    continue

        scene = self.scene()
        if scene is not None:
            try:
                scene.update()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                pass
        viewer = self._viewer_safe()
        if viewer is not None:
            try:
                viewer.viewport().update()
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                pass

    def _invalidate_layout_metrics(self) -> None:
        self._layout_metrics_ready = False
        self._embedded_widget_metrics.clear()
        self._state_panel_metrics.clear()
        self._command_row_metrics.clear()

    def _prepare_layout_metrics(self) -> None:
        ready = True
        for widget_proxy in self._widgets.values():
            try:
                if widget_proxy.widget() is None:
                    ready = False
                    break
            except (AttributeError, RuntimeError, TypeError):
                ready = False
                break
        if ready:
            for proxy in self._state_inline_proxies.values():
                try:
                    if proxy.widget() is None:
                        ready = False
                        break
                except (AttributeError, RuntimeError, TypeError):
                    ready = False
                    break
        if ready:
            for proxy in self._command_inline_proxies.values():
                try:
                    if proxy.widget() is None:
                        ready = False
                        break
                except (AttributeError, RuntimeError, TypeError):
                    ready = False
                    break
        self._layout_metrics_ready = bool(ready)

    def _requires_layout_metrics_for_proxy(self) -> bool:
        return bool(self._widgets or self._state_inline_proxies or self._command_inline_proxies)

    def _supports_auto_proxy(self) -> bool:
        if not self._requires_layout_metrics_for_proxy():
            return True
        return bool(self._layout_metrics_ready)

    @staticmethod
    def _activate_widget_layout(widget: QtWidgets.QWidget | None) -> None:
        if widget is None:
            return
        try:
            widget.ensurePolished()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            layout = widget.layout()
        except (AttributeError, RuntimeError, TypeError):
            layout = None
        if layout is not None:
            try:
                layout.activate()
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            widget.updateGeometry()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            widget.adjustSize()
        except (AttributeError, RuntimeError, TypeError):
            pass

    @classmethod
    def _measure_qwidget_geometry(
        cls,
        widget: QtWidgets.QWidget | None,
        *,
        fixed_width: int | None = None,
    ) -> tuple[float, float]:
        if widget is None:
            return 0.0, 0.0
        width_value = int(max(1, fixed_width)) if fixed_width is not None else None
        if width_value is not None:
            try:
                widget.setFixedWidth(width_value)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                width_value = None
        cls._activate_widget_layout(widget)

        width_candidates: list[float] = []
        height_candidates: list[float] = []
        for size_getter in (widget.size, widget.sizeHint, widget.minimumSizeHint):
            try:
                size = size_getter()
            except (AttributeError, RuntimeError, TypeError):
                continue
            width_candidates.append(float(size.width()))
            height_candidates.append(float(size.height()))
        if width_value is not None:
            width_candidates.append(float(width_value))
            try:
                height_for_width = float(widget.heightForWidth(width_value))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                height_for_width = 0.0
            if height_for_width > 0.0:
                height_candidates.append(height_for_width)
            try:
                layout = widget.layout()
            except (AttributeError, RuntimeError, TypeError):
                layout = None
            if layout is not None:
                try:
                    total_height = float(layout.totalHeightForWidth(width_value))
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    total_height = 0.0
                if total_height > 0.0:
                    height_candidates.append(total_height)
        width = float(max(width_candidates, default=0.0))
        height = float(max(height_candidates, default=0.0))
        return width, height

    def _embedded_widget_metric_key(self, widget_proxy: Any, *, target_width: float | None) -> str:
        widget_name = type(widget_proxy).__name__
        if isinstance(widget_proxy, NodeBaseWidget):
            try:
                value = str(widget_proxy.get_name() or "").strip()
            except (AttributeError, RuntimeError, TypeError):
                value = ""
            if value:
                widget_name = value
        width_key = "natural" if target_width is None else str(max(1, int(round(target_width))))
        return f"{widget_name}|{type(widget_proxy).__name__}|{width_key}"

    def _measure_embedded_widget(
        self,
        widget_proxy: Any,
        *,
        target_width: float | None = None,
    ) -> _LayoutMetric:
        cache_key = self._embedded_widget_metric_key(widget_proxy, target_width=target_width)
        cached = self._embedded_widget_metrics.get(cache_key)
        if cached is not None:
            return cached

        width = 0.0
        height = 0.0
        target_width_value = None if target_width is None else max(1, int(round(target_width)))
        if isinstance(widget_proxy, ResizableEmbeddedWidget):
            try:
                min_width, min_height = widget_proxy.minimum_content_size()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                min_width, min_height = 0, 0
            apply_width = int(max(target_width_value or 0, int(min_width), 1))
            apply_height = int(max(int(min_height), 1))
            try:
                widget_proxy.apply_content_rect(apply_width, apply_height)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            try:
                widget_proxy.prepareGeometryChange()
            except (AttributeError, RuntimeError, TypeError):
                pass

        group_widget = None
        try:
            group_widget = widget_proxy.widget()
        except (AttributeError, RuntimeError, TypeError):
            group_widget = None
        width, height = self._measure_qwidget_geometry(group_widget, fixed_width=target_width_value)
        metric = _LayoutMetric(cache_key=cache_key, width=float(width), height=float(height))
        self._embedded_widget_metrics[cache_key] = metric
        return metric

    def _state_panel_metric_cache_key(self, name: str, *, target_width: float) -> str:
        width_key = max(1, int(round(target_width)))
        ctrl_serial = str(self._state_inline_ctrl_serial.get(name, "") or "")
        expanded = "1" if bool(self._state_inline_expanded.get(name, False)) else "0"
        return f"{name}|{width_key}|{expanded}|{ctrl_serial}"

    def _measure_state_panel_metric(self, name: str, width: float) -> _StatePanelLayoutMetric:
        cache_key = self._state_panel_metric_cache_key(name, target_width=width)
        return self._measure_inline_panel_metric(
            cache=self._state_panel_metrics,
            cache_key=cache_key,
            proxy_map=self._state_inline_proxies,
            header_map=self._state_inline_headers,
            name=name,
            width=width,
        )

    def _command_row_metric_cache_key(self, name: str, *, target_width: float) -> str:
        width_key = max(1, int(round(target_width)))
        serial = str(self._command_inline_serials.get(name, "") or "")
        return f"{name}|{width_key}|{serial}"

    def _measure_command_row_metric(self, name: str, width: float) -> _StatePanelLayoutMetric:
        cache_key = self._command_row_metric_cache_key(name, target_width=width)
        return self._measure_inline_panel_metric(
            cache=self._command_row_metrics,
            cache_key=cache_key,
            proxy_map=self._command_inline_proxies,
            header_map=self._command_inline_headers,
            name=name,
            width=width,
        )

    def _measure_inline_panel_metric(
        self,
        *,
        cache: dict[str, _StatePanelLayoutMetric],
        cache_key: str,
        proxy_map: OrderedDict[str, QtWidgets.QGraphicsProxyWidget],
        header_map: OrderedDict[str, QtWidgets.QWidget],
        name: str,
        width: float,
    ) -> _StatePanelLayoutMetric:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        width_value = max(1, int(round(width)))
        panel_proxy = proxy_map.get(name)
        panel_widget = None
        if panel_proxy is not None:
            try:
                panel_widget = panel_proxy.widget()
            except (AttributeError, RuntimeError, TypeError):
                panel_widget = None
        panel_width, panel_height = self._measure_qwidget_geometry(panel_widget, fixed_width=width_value)

        header_height = float(PortEnum.SIZE.value)
        header = header_map.get(name)
        if header is not None:
            _header_width, measured_header_height = self._measure_qwidget_geometry(header, fixed_width=width_value)
            if measured_header_height > 0.0:
                header_height = float(measured_header_height)

        metric = _StatePanelLayoutMetric(
            cache_key=cache_key,
            width=float(max(panel_width, float(width_value))),
            height=float(max(panel_height, header_height)),
            header_height=float(header_height),
        )
        cache[cache_key] = metric
        return metric

    def _visible_state_names_for_layout(self) -> list[str]:
        state_names = self._ordered_visible_state_names_from_spec()
        if state_names:
            return state_names
        state_names = [str(name) for name in self._state_inline_proxies.keys() if str(name)]
        if state_names:
            return state_names
        inferred: list[str] = []
        for port in self._input_items.keys():
            name = _port_name(port)
            if name.startswith("[S]"):
                inferred.append(name[3:])
        for port in self._output_items.keys():
            name = _port_name(port)
            if name.endswith("[S]"):
                inferred.append(name[:-3])
        return [value for value in list(OrderedDict.fromkeys(inferred).keys()) if value]

    def _visible_command_names_for_layout(self) -> list[str]:
        command_names = self._ordered_visible_command_names_from_spec()
        if command_names:
            return command_names
        command_names = [str(name) for name in self._command_inline_proxies.keys() if str(name)]
        if command_names:
            return command_names
        inferred: list[str] = []
        for port in self._input_items.keys():
            parsed = parse_command_port_name(_port_name(port))
            if parsed is None or not parsed[0]:
                continue
            inferred.append(parsed[1])
        for port in self._output_items.keys():
            parsed = parse_command_port_name(_port_name(port))
            if parsed is None or parsed[0]:
                continue
            inferred.append(parsed[1])
        return [value for value in list(OrderedDict.fromkeys(inferred).keys()) if value]

    def _set_port_text_visibility(self, *, visible: bool) -> None:
        for port, text in self._input_items.items():
            if not port.isVisible():
                continue
            port_name = _port_name(port)
            if self._port_group(port_name) == "state":
                text.setVisible(False)
                continue
            parsed_command = parse_command_port_name(port_name)
            if parsed_command is not None and parsed_command[1] in self._command_inline_buttons:
                text.setVisible(False)
                continue
            text.setVisible(bool(visible and port.display_name))
        for port, text in self._output_items.items():
            if not port.isVisible():
                continue
            port_name = _port_name(port)
            if self._port_group(port_name) == "state":
                text.setVisible(False)
                continue
            parsed_command = parse_command_port_name(port_name)
            if parsed_command is not None and parsed_command[1] in self._command_inline_buttons:
                text.setVisible(False)
                continue
            text.setVisible(bool(visible and port.display_name))

    def _command_names_with_inline_buttons(self) -> set[str]:
        return {str(name).strip() for name in self._command_inline_buttons.keys() if str(name).strip()}

    def _ordered_exec_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        node = self._backend_node()
        if node is None:
            return []
        try:
            return [f"[E]{name}" for name in list(node.ordered_exec_port_names(is_in=True) or [])] if bool(is_in) else [
                f"{name}[E]" for name in list(node.ordered_exec_port_names(is_in=False) or [])
            ]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return []

    def _ordered_data_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        node = self._backend_node()
        if node is None:
            return []
        current_names = {
            _port_name(port)
            for port in (self._input_items.keys() if bool(is_in) else self._output_items.keys())
            if _port_name(port)
        }
        try:
            ports = list(node.ordered_data_port_specs(is_in=bool(is_in)) or [])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return []
        ordered: list[str] = []
        for port in ports:
            name = str(port.name or "").strip()
            if not name:
                continue
            view_name = f"[D]{name}" if bool(is_in) else f"{name}[D]"
            if node.data_port_show_on_node(name, is_in=bool(is_in)) or view_name in current_names:
                ordered.append(view_name)
        return ordered

    def _ordered_visible_state_names_from_spec(self) -> list[str]:
        node = self._backend_node()
        if node is None:
            return []
        try:
            effective_state_fields = list(node.ordered_state_field_specs() or [])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            try:
                spec = node.spec
            except (AttributeError, RuntimeError, TypeError):
                return []
            try:
                effective_state_fields = list(spec.stateFields or [])
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return []

        ordered: list[str] = []
        for state_field in effective_state_fields:
            info = _state_field_info(state_field)
            if info is None or not info.show_on_node or not info.name:
                continue
            ordered.append(info.name)
        return ordered

    def _ordered_visible_command_names_from_spec(self) -> list[str]:
        node = self._backend_node()
        if node is None:
            return []
        try:
            commands = list(node.ordered_command_specs() or [])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            try:
                spec = node.spec
            except (AttributeError, RuntimeError, TypeError):
                return []
            try:
                commands = list(spec.commands or [])
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return []

        ordered: list[str] = []
        for command in commands:
            name = str(command.name or "").strip()
            if not name or not bool(command.showOnNode):
                continue
            ordered.append(name)
        return ordered

    def _ordered_command_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        inline_command_names = self._command_names_with_inline_buttons()
        ordered: list[str] = []
        for command_name in self._ordered_visible_command_names_from_spec():
            if command_name in inline_command_names:
                continue
            ordered.append(f"[C]{command_name}" if bool(is_in) else f"{command_name}[C]")
        return ordered

    def _should_enable_proxy_mode(self) -> bool:
        if ITEM_CACHE_MODE is QtWidgets.QGraphicsItem.ItemCoordinateCache:
            return False
        viewer = self._viewer_safe()
        if not isinstance(viewer, F8StudioNodeViewer):
            return False
        if not viewer.auto_proxy_enabled():
            return False
        if not self._supports_auto_proxy():
            return False
        rect = self.sceneBoundingRect()
        left = viewer.mapToGlobal(viewer.mapFromScene(rect.topLeft()))
        right = viewer.mapToGlobal(viewer.mapFromScene(rect.topRight()))
        width = right.x() - left.x()
        return bool(width < self._proxy_mode_threshold)

    def sync_proxy_mode(self, *, force: bool = False) -> None:
        self._apply_proxy_mode(self._should_enable_proxy_mode(), force=force)

    def _apply_proxy_mode(self, mode: bool, *, force: bool) -> None:
        if not force and mode is self._proxy_mode:
            return
        self._proxy_mode = bool(mode)
        visible = not bool(mode)

        if bool(mode):
            for proxy in self._state_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass
            for proxy in self._command_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass

        self._x_item.proxy_mode = self._proxy_mode

        for widget_proxy in self._widgets.values():
            try:
                group_widget = widget_proxy.widget()
            except (AttributeError, RuntimeError, TypeError):
                group_widget = None
            if group_widget is not None:
                group_widget.setVisible(visible)
        for proxy in self._state_inline_proxies.values():
            try:
                proxy.setVisible(visible)
            except (AttributeError, RuntimeError, TypeError):
                pass
        for proxy in self._command_inline_proxies.values():
            try:
                proxy.setVisible(visible)
            except (AttributeError, RuntimeError, TypeError):
                pass

        if visible:
            for proxy in self._state_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass
            for proxy in self._command_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass

        port_text_visible = False
        if self.layout_direction is not LayoutDirectionEnum.VERTICAL.value:
            port_text_visible = visible
        self._set_port_text_visibility(visible=bool(port_text_visible))
        self._text_item.setVisible(visible)
        self._icon_item.setVisible(visible)

    def _calc_size_horizontal(self):
        # width, height from node name text.
        text_w = self._text_item.boundingRect().width()
        text_h = self._text_item.boundingRect().height()

        # width, height from node ports (grouped rows).
        port_width = 0.0
        p_input_text_width = 0.0
        p_output_text_width = 0.0
        p_input_height = 0.0
        p_output_height = 0.0
        port_height = 0.0
        spacing = 1.0
        group_gap = 6.0

        for port, text in self._input_items.items():
            if not port.isVisible():
                continue
            if not port_width:
                port_width = port.boundingRect().width()
            if not port_height:
                port_height = port.boundingRect().height()
            # State labels are displayed via the collapsible header button, not port text.
            if self._port_group(_port_name(port)) == "state":
                continue
            t_width = text.boundingRect().width()
            if text.isVisible() and t_width > p_input_text_width:
                p_input_text_width = text.boundingRect().width()
        for port, text in self._output_items.items():
            if not port.isVisible():
                continue
            if not port_width:
                port_width = port.boundingRect().width()
            if not port_height:
                port_height = port.boundingRect().height()
            if self._port_group(_port_name(port)) == "state":
                continue
            t_width = text.boundingRect().width()
            if text.isVisible() and t_width > p_output_text_width:
                p_output_text_width = text.boundingRect().width()

        # Determine grouped row count using current ports (fallback when backend node isn't available).
        def _names_for(kind: str, *, is_in: bool) -> list[str]:
            items = self._input_items if is_in else self._output_items
            out = []
            for p in items.keys():
                try:
                    if not p.isVisible():
                        continue
                    pname = _port_name(p)
                    if self._port_group(pname) == kind:
                        out.append(pname)
                except (AttributeError, RuntimeError, TypeError):
                    continue
            return out

        exec_in = self._ordered_exec_port_names_for_layout(is_in=True) or _names_for("exec", is_in=True)
        exec_out = self._ordered_exec_port_names_for_layout(is_in=False) or _names_for("exec", is_in=False)
        data_in = self._ordered_data_port_names_for_layout(is_in=True) or _names_for("data", is_in=True)
        data_out = self._ordered_data_port_names_for_layout(is_in=False) or _names_for("data", is_in=False)
        standalone_command_in = self._ordered_command_port_names_for_layout(is_in=True) or _names_for(
            "command", is_in=True
        )
        standalone_command_out = self._ordered_command_port_names_for_layout(is_in=False) or _names_for(
            "command", is_in=False
        )
        inline_command_names = self._command_names_with_inline_buttons()
        if inline_command_names:
            standalone_command_in = [
                name
                for name in standalone_command_in
                if (parse_command_port_name(name) or (False, ""))[1] not in inline_command_names
            ]
            standalone_command_out = [
                name
                for name in standalone_command_out
                if (parse_command_port_name(name) or (False, ""))[1] not in inline_command_names
            ]
        state_in = _names_for("state", is_in=True)
        state_out = _names_for("state", is_in=False)
        other_in = _names_for("other", is_in=True)
        other_out = _names_for("other", is_in=False)

        state_names: list[str] = self._visible_state_names_for_layout()
        if not state_names:
            # Infer state row order from port names (best-effort).
            tmp: list[str] = []
            for n in state_in:
                if n.startswith("[S]"):
                    tmp.append(n[3:])
            for n in state_out:
                if n.endswith("[S]"):
                    tmp.append(n[:-3])
            state_names = [x for x in list(OrderedDict.fromkeys(tmp).keys()) if x]
        command_names: list[str] = self._visible_command_names_for_layout()

        rows_exec = max(len(exec_in), len(exec_out))
        rows_data = max(len(data_in), len(data_out))
        rows_command = max(len(standalone_command_in), len(standalone_command_out))
        rows_other = max(len(other_in), len(other_out))

        widget_width = 0.0
        widget_height = 0.0
        # Ensure state inline widgets exist so we can account for width.
        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self._ensure_inline_command_rows()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._prepare_layout_metrics()
        for widget in self._widgets.values():
            if not widget.isVisible() and not self._proxy_mode:
                continue
            metric = self._measure_embedded_widget(widget)
            if metric.width > widget_width:
                widget_width = metric.width
            widget_height += metric.height
        # State panels span the node width; they should not participate in width calculation.
        # Command widget spans the node width; it should not participate in width calculation.

        port_text_width = p_input_text_width + p_output_text_width
        side_padding = 0.0
        if all([widget_width, p_input_text_width, p_output_text_width]):
            port_text_width = max([p_input_text_width, p_output_text_width])
            port_text_width *= 2
        elif widget_width:
            side_padding = 10

        width = port_width + max([text_w, port_text_width]) + side_padding
        inner_width = max(10.0, float(width) - 8.0)

        # Calculate port area height with expandable state panels.
        ports_h = 0.0
        base_port_height = float(port_height or PortEnum.SIZE.value)

        def _add_group_rows(rows: int) -> None:
            nonlocal ports_h
            if rows <= 0:
                return
            if ports_h > 0:
                ports_h += group_gap
            ports_h += (rows * base_port_height) + (max(0, rows - 1) * spacing)

        _add_group_rows(rows_exec)
        _add_group_rows(rows_data)

        if state_names:
            if ports_h > 0:
                ports_h += group_gap
            for sname in state_names:
                metric = self._measure_state_panel_metric(sname, inner_width)
                panel_height = float(max(metric.height, base_port_height))
                ports_h += panel_height + spacing
            ports_h = max(0.0, ports_h - spacing)

        if command_names:
            if ports_h > 0:
                ports_h += group_gap
            for command_name in command_names:
                metric = self._measure_command_row_metric(command_name, inner_width)
                panel_height = float(max(metric.height, base_port_height))
                ports_h += panel_height + spacing
            ports_h = max(0.0, ports_h - spacing)

        _add_group_rows(rows_command)
        _add_group_rows(rows_other)

        p_input_height = ports_h
        p_output_height = ports_h

        port_area_height = max(p_input_height, p_output_height)
        height = max([text_h, port_area_height, widget_height])
        if widget_width:
            # add additional width for node widget.
            width += widget_width
        if widget_height:
            # add bottom margin for node widget.
            height += 4.0
        height *= 1.05
        return width, height

    def _calc_size_vertical(self):
        p_input_width = 0.0
        p_output_width = 0.0
        p_input_height = 0.0
        p_output_height = 0.0
        for port in self._input_items.keys():
            if port.isVisible():
                p_input_width += port.boundingRect().width()
                if not p_input_height:
                    p_input_height = port.boundingRect().height()
        for port in self._output_items.keys():
            if port.isVisible():
                p_output_width += port.boundingRect().width()
                if not p_output_height:
                    p_output_height = port.boundingRect().height()

        widget_width = 0.0
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible() and not self._proxy_mode:
                continue
            metric = self._measure_embedded_widget(widget)
            if metric.width > widget_width:
                widget_width = metric.width
            widget_height += metric.height

        width = max([p_input_width, p_output_width, widget_width])
        height = p_input_height + p_output_height + widget_height
        return width, height

    def calc_size(self, add_w=0.0, add_h=0.0):
        """
        Calculates the minimum node size.

        Args:
            add_w (float): additional width.
            add_h (float): additional height.

        Returns:
            tuple(float, float): width, height.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            width, height = self._calc_size_horizontal()
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            width, height = self._calc_size_vertical()
        else:
            raise RuntimeError("Node graph layout direction not valid!")

        # additional width, height.
        width += add_w
        height += add_h
        return width, height

    def _align_icon_horizontal(self, h_offset, v_offset):
        icon_rect = self._icon_item.boundingRect()
        text_rect = self._text_item.boundingRect()
        x = self.boundingRect().left() + 2.0
        y = text_rect.center().y() - (icon_rect.height() / 2)
        self._icon_item.setPos(x + h_offset, y + v_offset)

    def _align_icon_vertical(self, h_offset, v_offset):
        center_y = self.boundingRect().center().y()
        icon_rect = self._icon_item.boundingRect()
        text_rect = self._text_item.boundingRect()
        x = self.boundingRect().right() + h_offset
        y = center_y - text_rect.height() - (icon_rect.height() / 2) + v_offset
        self._icon_item.setPos(x, y)

    def align_icon(self, h_offset=0.0, v_offset=0.0):
        """
        Align node icon to the default top left of the node.

        Args:
            v_offset (float): additional vertical offset.
            h_offset (float): additional horizontal offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_icon_horizontal(h_offset, v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_icon_vertical(h_offset, v_offset)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

    def _align_label_horizontal(self, h_offset, v_offset):
        rect = self.boundingRect()
        text_rect = self._text_item.boundingRect()
        x = rect.center().x() - (text_rect.width() / 2)
        self._text_item.setPos(x + h_offset, rect.y() + v_offset)

    def _align_label_vertical(self, h_offset, v_offset):
        rect = self._text_item.boundingRect()
        x = self.boundingRect().right() + h_offset
        y = self.boundingRect().center().y() - (rect.height() / 2) + v_offset
        self.text_item.setPos(x, y)

    def align_label(self, h_offset=0.0, v_offset=0.0):
        """
        Center node label text to the top of the node.

        Args:
            v_offset (float): vertical offset.
            h_offset (float): horizontal offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_label_horizontal(h_offset, v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_label_vertical(h_offset, v_offset)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

    def _content_rect_for_widgets(self, *, top_y: float) -> tuple[float, float, float, float]:
        """
        Compute available node-inner content rect for embedded widgets.
        """
        rect = self.boundingRect()
        return content_rect_with_minimum(
            x=rect.left() + 4.0,
            y=top_y,
            width=rect.width() - 8.0,
            height=rect.bottom() - top_y - 4.0,
            minimum=(10, 10),
        )

    def _apply_widget_resize_policy(
        self,
        widget_proxy: Any,
        *,
        content_rect: tuple[float, float, float, float],
    ) -> bool:
        """
        Apply optional node->widget resize contract.

        Returns:
            bool: True when resize was applied via `ResizableEmbeddedWidget`.
        """
        if not isinstance(widget_proxy, ResizableEmbeddedWidget):
            return False

        try:
            min_size = widget_proxy.minimum_content_size()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        target_w, target_h = clamp_content_size(
            width=float(content_rect[2]),
            height=float(content_rect[3]),
            minimum=min_size,
        )
        try:
            widget_proxy.apply_content_rect(target_w, target_h)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

        try:
            widget_proxy.prepareGeometryChange()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            qwidget = widget_proxy.widget()
            if qwidget is None:
                return True
            qwidget.adjustSize()
        except (AttributeError, RuntimeError, TypeError):
            return True
        return True

    def _align_widgets_horizontal(self, v_offset):
        rect = self.boundingRect()
        inputs = [p for p in self.inputs if p.isVisible()]
        outputs = [p for p in self.outputs if p.isVisible()]

        if not self._widgets:
            return
        rect = self.boundingRect()
        # Place regular NodeGraphQt embedded widgets below the ports area. This
        # prevents custom widgets from overlapping the ports/state/command region.
        base_y = float(self._ports_end_y or (rect.y() + v_offset))
        y = base_y + 6.0
        for widget in self._widgets.values():
            content_rect = self._content_rect_for_widgets(top_y=y)
            resized = self._apply_widget_resize_policy(widget, content_rect=content_rect)
            widget_rect = widget.boundingRect()
            target_width = float(content_rect[2]) if resized else None
            metric = self._measure_embedded_widget(widget, target_width=target_width)
            widget_width = float(max(widget_rect.width(), metric.width))
            widget_height = float(max(widget_rect.height(), metric.height))
            if resized:
                x = float(content_rect[0])
                widget.widget().setTitleAlign("center")
            elif not inputs:
                x = rect.left() + 10
                widget.widget().setTitleAlign("left")
            elif not outputs:
                x = rect.right() - widget_width - 10
                widget.widget().setTitleAlign("right")
            else:
                x = rect.center().x() - (widget_width / 2)
                widget.widget().setTitleAlign("center")
            widget.setPos(x, y)
            y += widget_height

    def _align_widgets_vertical(self, v_offset):
        if not self._widgets:
            return
        rect = self.boundingRect()
        y = rect.center().y() + v_offset
        widget_height = 0.0
        for widget in self._widgets.values():
            metric = self._measure_embedded_widget(widget)
            widget_rect = widget.boundingRect()
            widget_height += float(max(widget_rect.height(), metric.height))
        y -= widget_height / 2

        for widget in self._widgets.values():
            content_rect = self._content_rect_for_widgets(top_y=y)
            resized = self._apply_widget_resize_policy(widget, content_rect=content_rect)
            widget_rect = widget.boundingRect()
            target_width = float(content_rect[2]) if resized else None
            metric = self._measure_embedded_widget(widget, target_width=target_width)
            widget_width = float(max(widget_rect.width(), metric.width))
            widget_height = float(max(widget_rect.height(), metric.height))
            if resized:
                x = float(content_rect[0])
                widget.widget().setTitleAlign("center")
            else:
                x = rect.center().x() - (widget_width / 2)
                widget.widget().setTitleAlign("center")
            widget.setPos(x, y)
            y += widget_height

    def align_widgets(self, v_offset=0.0):
        """
        Align node widgets to the default center of the node.

        Args:
            v_offset (float): vertical offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_widgets_horizontal(v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_widgets_vertical(v_offset)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

    def _align_ports_horizontal(self, v_offset):
        width = self._width
        txt_offset = PortEnum.CLICK_FALLOFF.value - 2
        spacing = 1.0
        group_gap = 6.0

        # Ensure inline widgets exist before aligning so sizing + rows match.
        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass

        node = self._backend_node()
        if node is None:
            spec = None
        else:
            try:
                spec = node.spec
            except (AttributeError, RuntimeError, TypeError):
                spec = None
        try:
            eff_states = list(node.effective_state_fields() or []) if node is not None else []
        except (AttributeError, RuntimeError, TypeError, ValueError):
            if spec is None:
                eff_states = []
            else:
                try:
                    eff_states = list(spec.stateFields or [])
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    eff_states = []

        # Build ordered port name lists per group.
        exec_in_names = self._ordered_exec_port_names_for_layout(is_in=True)
        exec_out_names = self._ordered_exec_port_names_for_layout(is_in=False)

        data_in_names = self._ordered_data_port_names_for_layout(is_in=True)
        data_out_names = self._ordered_data_port_names_for_layout(is_in=False)
        command_in_names = self._ordered_command_port_names_for_layout(is_in=True)
        command_out_names = self._ordered_command_port_names_for_layout(is_in=False)
        if node is not None:
            try:
                if not data_in_names:
                    data_in_names = self._ordered_data_port_names_for_layout(is_in=True)
                if not data_out_names:
                    data_out_names = self._ordered_data_port_names_for_layout(is_in=False)
                if not command_in_names:
                    command_in_names = self._ordered_command_port_names_for_layout(is_in=True)
                if not command_out_names:
                    command_out_names = self._ordered_command_port_names_for_layout(is_in=False)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                data_in_names = []
                data_out_names = []
                command_in_names = []
                command_out_names = []

        state_names: list[str] = []
        for s in eff_states:
            info = _state_field_info(s)
            if info is None or not info.show_on_node:
                continue
            if info.name:
                state_names.append(info.name)

        # Fallback when spec is unavailable: keep insertion order but grouped.
        if not exec_in_names:
            exec_in_names = [
                _port_name(p) for p in self._input_items.keys() if self._port_group(_port_name(p)) == "exec"
            ]
        if not exec_out_names:
            exec_out_names = [
                _port_name(p) for p in self._output_items.keys() if self._port_group(_port_name(p)) == "exec"
            ]
        if not data_in_names:
            data_in_names = [
                _port_name(p) for p in self._input_items.keys() if self._port_group(_port_name(p)) == "data"
            ]
        if not data_out_names:
            data_out_names = [
                _port_name(p) for p in self._output_items.keys() if self._port_group(_port_name(p)) == "data"
            ]
        if not command_in_names:
            command_in_names = [
                _port_name(p) for p in self._input_items.keys() if self._port_group(_port_name(p)) == "command"
            ]
        if not command_out_names:
            command_out_names = [
                _port_name(p) for p in self._output_items.keys() if self._port_group(_port_name(p)) == "command"
            ]
        inline_command_names = self._visible_command_names_for_layout()
        if inline_command_names:
            command_in_names = [
                name
                for name in command_in_names
                if (parse_command_port_name(name) or (False, ""))[1] not in inline_command_names
            ]
            command_out_names = [
                name
                for name in command_out_names
                if (parse_command_port_name(name) or (False, ""))[1] not in inline_command_names
            ]
        if not state_names:
            # Infer state rows from existing ports.
            tmp: list[str] = []
            for p in self._input_items.keys():
                n = _port_name(p)
                if n.startswith("[S]"):
                    tmp.append(n[3:])
            for p in self._output_items.keys():
                n = _port_name(p)
                if n.endswith("[S]"):
                    tmp.append(n[:-3])
            state_names = [x for x in list(OrderedDict.fromkeys(tmp).keys()) if x]

        other_in_names = [
            _port_name(p) for p in self._input_items.keys() if self._port_group(_port_name(p)) == "other"
        ]
        other_out_names = [
            _port_name(p) for p in self._output_items.keys() if self._port_group(_port_name(p)) == "other"
        ]

        inputs_by_name = {_port_name(p): p for p in self.inputs if p.isVisible()}
        outputs_by_name = {_port_name(p): p for p in self.outputs if p.isVisible()}

        # Determine base port geometry.
        port_width = 0.0
        port_height = 0.0
        for p in list(inputs_by_name.values()) + list(outputs_by_name.values()):
            try:
                port_width = float(p.boundingRect().width())
                port_height = float(p.boundingRect().height())
                break
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        in_x = (port_width / 2.0) * -1.0
        out_x = width - (port_width / 2.0)

        rect = self.boundingRect()
        inner_x = rect.left() + 4.0
        inner_w = max(10.0, rect.width() - 8.0)

        def place_row(in_name: str | None, out_name: str | None, *, y: float):
            if in_name:
                p = inputs_by_name.get(in_name)
                if p is not None:
                    p.setPos(in_x, y)
            if out_name:
                p = outputs_by_name.get(out_name)
                if p is not None:
                    p.setPos(out_x, y)

        def place_panel_row(
            *,
            panel_proxy: Any,
            metric: _StatePanelLayoutMetric | None,
            y_value: float,
        ) -> float:
            header_h = float(max(port_height, metric.header_height if metric is not None else 0.0))
            panel_h = float(max(header_h, metric.height if metric is not None else 0.0))
            if panel_proxy is not None and metric is not None:
                panel_w = float(max(metric.width, inner_w))
                panel_x = rect.left() + (rect.width() - panel_w) / 2.0
                min_x = float(inner_x)
                max_x = float(rect.right() - 4.0 - panel_w)
                if max_x < min_x:
                    panel_x = min_x
                else:
                    panel_x = max(min_x, min(panel_x, max_x))
                try:
                    panel_proxy.setPos(panel_x, y_value)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            return header_h, panel_h

        y = float(v_offset)
        groups: list[tuple[str, list[str], list[str]]] = [
            ("exec", exec_in_names, exec_out_names),
            ("data", data_in_names, data_out_names),
            ("state", [f"[S]{n}" for n in state_names], [f"{n}[S]" for n in state_names]),
            ("command", [f"[C]{n}" for n in inline_command_names], [f"{n}[C]" for n in inline_command_names]),
            ("command_ports", command_in_names, command_out_names),
            ("other", other_in_names, other_out_names),
        ]

        for gi, (gname, ins, outs) in enumerate(groups):
            if gname == "state":
                rows = len(state_names)
            elif gname == "command":
                rows = len(inline_command_names)
            else:
                rows = max(len(ins), len(outs))
            if rows <= 0:
                continue
            for i in range(rows):
                in_name = ins[i] if i < len(ins) else None
                out_name = outs[i] if i < len(outs) else None

                if gname not in {"state", "command"}:
                    place_row(in_name, out_name, y=y)
                    y += port_height + spacing
                    continue

                if gname == "state":
                    state_key = state_names[i] if i < len(state_names) else None
                    panel_proxy = self._state_inline_proxies.get(state_key) if state_key else None
                    metric = self._measure_state_panel_metric(state_key, inner_w) if state_key else None
                else:
                    command_key = inline_command_names[i] if i < len(inline_command_names) else None
                    panel_proxy = self._command_inline_proxies.get(command_key) if command_key else None
                    metric = self._measure_command_row_metric(command_key, inner_w) if command_key else None

                header_h, panel_h = place_panel_row(panel_proxy=panel_proxy, metric=metric, y_value=y)
                port_y = y + (header_h - port_height) / 2.0
                place_row(in_name, out_name, y=port_y)
                y += panel_h + spacing
            # group gap (except after last visible group)
            # determine if any later group has rows.
            has_later = False
            for _g2, ins2, outs2 in groups[gi + 1 :]:
                if _g2 == "state":
                    if len(state_names) > 0:
                        has_later = True
                        break
                elif _g2 == "command":
                    if len(inline_command_names) > 0:
                        has_later = True
                        break
                else:
                    if max(len(ins2), len(outs2)) > 0:
                        has_later = True
                        break
            if has_later:
                y += group_gap
        self._ports_end_y = y

        # adjust input text position
        for port, text in self._input_items.items():
            if port.isVisible():
                txt_x = port.boundingRect().width() / 2 - txt_offset
                text.setPos(txt_x, port.y() - 1.5)

        # adjust output text position
        for port, text in self._output_items.items():
            if port.isVisible():
                txt_width = text.boundingRect().width() - txt_offset
                txt_x = port.x() - txt_width
                text.setPos(txt_x, port.y() - 1.5)

    def _align_ports_vertical(self, v_offset):
        # adjust input position
        inputs = [p for p in self.inputs if p.isVisible()]
        if inputs:
            port_width = inputs[0].boundingRect().width()
            port_height = inputs[0].boundingRect().height()
            half_width = port_width / 2
            delta = self._width / (len(inputs) + 1)
            port_x = delta
            port_y = (port_height / 2) * -1
            for port in inputs:
                port.setPos(port_x - half_width, port_y)
                port_x += delta

        # adjust output position
        outputs = [p for p in self.outputs if p.isVisible()]
        if outputs:
            port_width = outputs[0].boundingRect().width()
            port_height = outputs[0].boundingRect().height()
            half_width = port_width / 2
            delta = self._width / (len(outputs) + 1)
            port_x = delta
            port_y = self._height - (port_height / 2)
            for port in outputs:
                port.setPos(port_x - half_width, port_y)
                port_x += delta

    def align_ports(self, v_offset=0.0):
        """
        Align input, output ports in the node layout.

        Args:
            v_offset (float): port vertical offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_ports_horizontal(v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_ports_vertical(v_offset)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

    def _draw_node_horizontal(self):
        self._invalidate_layout_metrics()
        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self._ensure_inline_command_rows()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._prepare_layout_metrics()
        height = self._text_item.boundingRect().height() + 4.0

        target_proxy_mode = self._should_enable_proxy_mode()
        self._set_port_text_visibility(visible=not target_proxy_mode)

        # setup initial base size.
        self._set_base_size(add_h=height)
        # set text color when node is initialized.
        self._set_text_color(self.text_color)
        # set the tooltip
        self._tooltip_disable(self.disabled)

        # --- set the initial node layout ---
        # (do all the graphic item layout offsets here)

        # align label text
        self.align_label()
        # align icon
        self.align_icon(h_offset=2.0, v_offset=1.0)
        # arrange input and output ports.
        self.align_ports(v_offset=height)
        # arrange node widgets
        self.align_widgets(v_offset=height)
        self.sync_proxy_mode(force=True)

        if self._proxy_mode != target_proxy_mode:
            self._set_port_text_visibility(visible=not self._proxy_mode)
            self._set_base_size(add_h=height)
            self.align_label()
            self.align_icon(h_offset=2.0, v_offset=1.0)
            self.align_ports(v_offset=height)
            self.align_widgets(v_offset=height)
            self.sync_proxy_mode(force=True)

        self.update()

    def _draw_node_vertical(self):
        self._invalidate_layout_metrics()
        self._prepare_layout_metrics()
        # hide the port text items in vertical layout.
        self._set_port_text_visibility(visible=False)

        # setup initial base size.
        self._set_base_size()
        # set text color when node is initialized.
        self._set_text_color(self.text_color)
        # set the tooltip
        self._tooltip_disable(self.disabled)

        # --- setup node layout ---
        # (do all the graphic item layout offsets here)

        # align label text
        self.align_label(h_offset=6)
        # align icon
        self.align_icon(h_offset=6, v_offset=4)
        # arrange input and output ports.
        self.align_ports()
        # arrange node widgets
        self.align_widgets()
        self.sync_proxy_mode(force=True)

        self.update()

    def draw_node(self):
        """
        Re-draw the node item in the scene with proper
        calculated size and widgets aligned.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._draw_node_horizontal()
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._draw_node_vertical()
        else:
            raise RuntimeError("Node graph layout direction not valid!")
        self._refresh_port_tooltips()
        self._position_service_toolbar()

    def post_init(self, viewer=None, pos=None):
        """
        Called after node has been added into the scene.
        Adjust the node layout and form after the node has been added.

        Args:
            viewer (NodeGraphQt.widgets.viewer.NodeViewer): not used
            pos (tuple): cursor position.
        """
        self.draw_node()
        self._ensure_service_toolbar(viewer)
        self._position_service_toolbar()

        # set initial node position.
        if pos:
            self.xy_pos = pos
            self._position_service_toolbar()

    def _ensure_service_toolbar(self, viewer: Any | None) -> None:
        _ensure_service_toolbar_impl(self, viewer)

    def _current_service_id(self) -> str:
        return _toolbar_current_service_id_impl(self)

    def refresh_service_identity_bindings(self) -> None:
        _refresh_service_identity_bindings_impl(self)

    def _position_service_toolbar(self) -> None:
        _position_service_toolbar_impl(self)

    def auto_switch_mode(self):
        """
        Re-evaluate proxy mode using the current viewer transform.
        """
        self.sync_proxy_mode(force=False)

    def set_proxy_mode(self, mode):
        """
        Set whether to draw the node with proxy mode.
        (proxy mode toggles visibility for some qgraphic items in the node.)

        Args:
            mode (bool): true to enable proxy mode.
        """
        self._apply_proxy_mode(bool(mode), force=False)

    def _has_inline_state_controls(self) -> bool:
        if bool(self._state_inline_proxies):
            return True
        node = self._backend_node()
        if node is None:
            return False
        try:
            fields = list(node.effective_state_fields() or [])
        except Exception:
            try:
                spec = node.spec
                fields = list(spec.stateFields or []) if spec is not None else []
            except Exception:
                fields = []
        for field in fields:
            info = _state_field_info(field)
            if info is not None and info.show_on_node:
                return True
        return False

    @property
    def icon(self):
        return self._properties["icon"]

    @icon.setter
    def icon(self, path=None):
        self._properties["icon"] = path
        path = path or ICON_NODE_BASE
        pixmap = QtGui.QPixmap(path)
        if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToHeight(NodeEnum.ICON_SIZE.value, QtCore.Qt.SmoothTransformation)
        if pixmap.size().width() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToWidth(NodeEnum.ICON_SIZE.value, QtCore.Qt.SmoothTransformation)
        self._icon_item.setPixmap(pixmap)
        if self.scene():
            self.post_init()

        self.update()

    @AbstractNodeItem.layout_direction.setter
    def layout_direction(self, value=0):
        AbstractNodeItem.layout_direction.fset(self, value)
        self.draw_node()

    @AbstractNodeItem.width.setter
    def width(self, width=0.0):
        w, h = self.calc_size()
        width = width if width > w else w
        AbstractNodeItem.width.fset(self, width)

    @AbstractNodeItem.height.setter
    def height(self, height=0.0):
        w, h = self.calc_size()
        h = 70 if h < 70 else h
        height = height if height > h else h
        AbstractNodeItem.height.fset(self, height)

    @AbstractNodeItem.disabled.setter
    def disabled(self, state=False):
        AbstractNodeItem.disabled.fset(self, state)
        for n, w in self._widgets.items():
            w.widget().setDisabled(state)
        self._tooltip_disable(state)
        self._x_item.setVisible(state)
        self._refresh_pipe_visual_state()

    @AbstractNodeItem.selected.setter
    def selected(self, selected=False):
        AbstractNodeItem.selected.fset(self, selected)
        if selected:
            self.highlight_pipes()

    @AbstractNodeItem.name.setter
    def name(self, name=""):
        AbstractNodeItem.name.fset(self, name)
        if name == self._text_item.toPlainText():
            return
        self._text_item.setPlainText(name)
        if self.scene():
            self.align_label()
        self.update()

    @AbstractNodeItem.color.setter
    def color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.color.fset(self, color)
        if self.scene():
            self.scene().update()
        self.update()

    @AbstractNodeItem.border_color.setter
    def border_color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.border_color.fset(self, color)
        if self.scene():
            self.scene().update()
        self.update()

    @AbstractNodeItem.text_color.setter
    def text_color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.text_color.fset(self, color)
        self._set_text_color(color)
        self.update()

    @property
    def text_item(self):
        """
        Get the node name text qgraphics item.

        Returns:
            NodeTextItem: node text object.
        """
        return self._text_item

    @property
    def icon_item(self):
        """
        Get the node icon qgraphics item.

        Returns:
            QtWidgets.QGraphicsPixmapItem: node icon object.
        """
        return self._icon_item

    @property
    def inputs(self):
        """
        Returns:
            list[PortItem]: input port graphic items.
        """
        return list(self._input_items.keys())

    @property
    def outputs(self):
        """
        Returns:
            list[PortItem]: output port graphic items.
        """
        return list(self._output_items.keys())

    def _add_port(self, port):
        return _add_port_impl(self, port)

    def add_input(self, name="input", multi_port=False, display_name=True, locked=False, painter_func=None):
        return _add_input_impl(
            self,
            port_name=name,
            multi_port=multi_port,
            display_name=display_name,
            locked=locked,
            painter_func=painter_func,
            port_item_cls=F8StudioPortItem,
            custom_port_item_cls=F8StudioCustomPortItem,
        )

    def add_output(self, name="output", multi_port=False, display_name=True, locked=False, painter_func=None):
        return _add_output_impl(
            self,
            port_name=name,
            multi_port=multi_port,
            display_name=display_name,
            locked=locked,
            painter_func=painter_func,
            port_item_cls=F8StudioPortItem,
            custom_port_item_cls=F8StudioCustomPortItem,
        )

    def _delete_port(self, port, text):
        _delete_port_impl(self, port=port, text=text)

    def delete_input(self, port):
        _delete_input_impl(self, port)

    def delete_output(self, port):
        _delete_output_impl(self, port)

    def get_input_text_item(self, port_item):
        return _get_input_text_item_impl(self, port_item)

    def get_output_text_item(self, port_item):
        return _get_output_text_item_impl(self, port_item)

    @property
    def widgets(self):
        return _widgets_impl(self)

    def add_widget(self, widget):
        _add_widget_impl(self, widget)

    def get_widget(self, name):
        return _get_widget_impl(self, name)

    def has_widget(self, name):
        return _has_widget_impl(self, name)

    def from_dict(self, node_dict):
        super().from_dict(node_dict)
        _ports_from_dict_impl(self, node_dict)
