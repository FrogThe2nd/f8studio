from __future__ import annotations

import logging
import json
from typing import Any

from .node_base import F8StudioBaseNode

from f8pysdk import F8ServiceSpec, F8StateAccess

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
    ensure_inline_command_widget as _ensure_inline_command_widget_impl,
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
        self._build_state_properties()

    def _build_data_port(self):
        _build_data_port_impl(self)

    def _build_state_port(self):
        _build_state_port_impl(self)

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
        self._state_inline_updaters: OrderedDict[str, Any] = OrderedDict()
        self._state_inline_toggles: OrderedDict[str, QtWidgets.QToolButton] = OrderedDict()
        self._state_inline_headers: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._state_inline_bodies: OrderedDict[str, QtWidgets.QWidget] = OrderedDict()
        self._state_inline_expanded: dict[str, bool] = {}
        self._state_inline_option_pools: dict[str, str] = {}
        self._state_row_y: dict[str, tuple[float, float]] = {}
        self._graph_prop_hooked: bool = False
        self._bridge_proc_hooked: bool = False
        self._state_inline_ctrl_serial: dict[str, str] = {}
        self._cmd_serial: str = ""
        self._cmd_proxy: QtWidgets.QGraphicsProxyWidget | None = None
        self._cmd_widget: QtWidgets.QWidget | None = None
        self._cmd_buttons: list[QtWidgets.QAbstractButton] = []
        self._tooltip_filters: list[QtCore.QObject] = []
        self._svc_toolbar_proxy: QtWidgets.QGraphicsProxyWidget | None = None
        self._ports_end_y: float | None = None
        self._open_code_editors: list[QtWidgets.QDialog] = []

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

    def _ensure_inline_command_widget(self) -> None:
        _ensure_inline_command_widget_impl(self)

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
        self.auto_switch_mode()
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._paint_horizontal(painter, option, widget)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._paint_vertical(painter, option, widget)
        else:
            raise RuntimeError("Node graph layout direction not valid!")

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

        exec_in = _names_for("exec", is_in=True)
        exec_out = _names_for("exec", is_in=False)
        data_in = _names_for("data", is_in=True)
        data_out = _names_for("data", is_in=False)
        state_in = _names_for("state", is_in=True)
        state_out = _names_for("state", is_in=False)
        other_in = _names_for("other", is_in=True)
        other_out = _names_for("other", is_in=False)

        state_names: list[str] = [n for n, p in self._state_inline_proxies.items() if p.isVisible()]
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

        rows_exec = max(len(exec_in), len(exec_out))
        rows_data = max(len(data_in), len(data_out))
        rows_other = max(len(other_in), len(other_out))

        # Calculate port area height with expandable state panels.
        ports_h = 0.0
        if port_height:

            def _add_group_rows(rows: int) -> None:
                nonlocal ports_h
                if rows <= 0:
                    return
                if ports_h > 0:
                    ports_h += group_gap
                ports_h += (rows * port_height) + (max(0, rows - 1) * spacing)

            _add_group_rows(rows_exec)
            _add_group_rows(rows_data)

            # State: each row has a header (ports+toggle) and optional expanded body.
            if state_names:
                if ports_h > 0:
                    ports_h += group_gap
                for i, sname in enumerate(state_names):
                    header_h = port_height
                    try:
                        header = self._state_inline_headers.get(sname)
                        if header is not None:
                            header_h = float(max(port_height, header.sizeHint().height()))
                    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                        header_h = port_height
                    # Size hint for the expanded body depends on width (options wrap).
                    # Use the proxy widget bounding rect after forcing a best-effort width.
                    panel_h = header_h
                    try:
                        proxy = self._state_inline_proxies.get(sname)
                        if proxy is not None and proxy.isVisible():
                            try:
                                w = proxy.widget()
                                if w is not None:
                                    rect_w = max(10, int(self.boundingRect().width() - 8.0))
                                    w.setFixedWidth(rect_w)
                                    w.adjustSize()
                            except (AttributeError, RuntimeError, TypeError, ValueError):
                                pass
                            try:
                                panel_h = float(max(header_h, proxy.boundingRect().height()))
                            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                                panel_h = header_h
                    except (AttributeError, RuntimeError, TypeError, ValueError, KeyError, ImportError, OSError):
                        panel_h = header_h
                    ports_h += panel_h + spacing
                ports_h = max(0.0, ports_h - spacing)  # remove trailing row spacing

            _add_group_rows(rows_other)

            p_input_height = ports_h
            p_output_height = ports_h

        port_text_width = p_input_text_width + p_output_text_width

        # width, height from node embedded widgets.
        widget_width = 0.0
        widget_height = 0.0
        # Ensure state inline widgets exist so we can account for width.
        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self._ensure_inline_command_widget()
        except (AttributeError, RuntimeError, TypeError):
            pass
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            w_width = widget.boundingRect().width()
            w_height = widget.boundingRect().height()
            if w_width > widget_width:
                widget_width = w_width
            widget_height += w_height
        # State panels span the node width; they should not participate in width calculation.
        # Command widget spans the node width; it should not participate in width calculation.

        side_padding = 0.0
        if all([widget_width, p_input_text_width, p_output_text_width]):
            port_text_width = max([p_input_text_width, p_output_text_width])
            port_text_width *= 2
        elif widget_width:
            side_padding = 10

        width = port_width + max([text_w, port_text_width]) + side_padding

        port_area_height = max(p_input_height, p_output_height)
        height = max([text_h, port_area_height, widget_height])
        if widget_width:
            # add additional width for node widget.
            width += widget_width
        if widget_height:
            # add bottom margin for node widget.
            height += 4.0

        # Commands: compute height using the final width (flow wrap depends on width).
        if self._cmd_proxy is not None:
            try:
                if self._cmd_proxy.isVisible() and port_height:
                    rect_w = max(10, int(width - 8.0))
                    if self._cmd_widget is not None:
                        self._cmd_widget.setFixedWidth(rect_w)
                        self._cmd_widget.adjustSize()
                    cmd_h = float(self._cmd_proxy.boundingRect().height())
                    if cmd_h > 0:
                        height = max(height, port_area_height + cmd_h + 10.0)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
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
            if not widget.isVisible():
                continue
            if widget.boundingRect().width() > widget_width:
                widget_width = widget.boundingRect().width()
            widget_height += widget.boundingRect().height()

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

        # Command buttons are placed below the ports area and should span the full node width.
        cmd_bottom = None
        if self._cmd_proxy is not None and self._cmd_proxy.isVisible():
            try:
                y = float(self._ports_end_y or (rect.y() + v_offset))
                # Force the underlying QWidget to take the full available width.
                try:
                    if self._cmd_widget is not None:
                        self._cmd_widget.setFixedWidth(max(10, int(rect.width() - 8.0)))
                        self._cmd_widget.adjustSize()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
                w_rect = self._cmd_proxy.boundingRect()
                x = rect.left() + 4.0
                self._cmd_proxy.setPos(x, y + 6.0)
                cmd_bottom = y + 6.0 + w_rect.height()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                cmd_bottom = None

        if not self._widgets:
            return
        rect = self.boundingRect()
        # Place regular NodeGraphQt embedded widgets below the ports area (and below
        # command area if present). This prevents custom widgets from overlapping
        # the ports/state region.
        base_y = float(self._ports_end_y or (rect.y() + v_offset))
        y = base_y + 6.0
        if cmd_bottom is not None:
            y = max(y, cmd_bottom + 6.0)
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            content_rect = self._content_rect_for_widgets(top_y=y)
            resized = self._apply_widget_resize_policy(widget, content_rect=content_rect)
            widget_rect = widget.boundingRect()
            if resized:
                x = float(content_rect[0])
                widget.widget().setTitleAlign("center")
            elif not inputs:
                x = rect.left() + 10
                widget.widget().setTitleAlign("left")
            elif not outputs:
                x = rect.right() - widget_rect.width() - 10
                widget.widget().setTitleAlign("right")
            else:
                x = rect.center().x() - (widget_rect.width() / 2)
                widget.widget().setTitleAlign("center")
            widget.setPos(x, y)
            y += widget_rect.height()

    def _align_widgets_vertical(self, v_offset):
        if not self._widgets:
            return
        rect = self.boundingRect()
        y = rect.center().y() + v_offset
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            widget_rect = widget.boundingRect()
            widget_height += widget_rect.height()
        y -= widget_height / 2

        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            content_rect = self._content_rect_for_widgets(top_y=y)
            resized = self._apply_widget_resize_policy(widget, content_rect=content_rect)
            widget_rect = widget.boundingRect()
            if resized:
                x = float(content_rect[0])
                widget.widget().setTitleAlign("center")
            else:
                x = rect.center().x() - (widget_rect.width() / 2)
                widget.widget().setTitleAlign("center")
            widget.setPos(x, y)
            y += widget_rect.height()

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
        exec_in_names: list[str] = []
        exec_out_names: list[str] = []

        data_in_names: list[str] = []
        data_out_names: list[str] = []
        if node is not None:
            try:
                existing_in = {_port_name(p) for p in self._input_items.keys()}
                existing_out = {_port_name(p) for p in self._output_items.keys()}
                for p in list(spec.dataInPorts or []):
                    port_name = f"[D]{p.name}"
                    if node.data_port_show_on_node(str(p.name or ""), is_in=True) or port_name in existing_in:
                        data_in_names.append(port_name)
                for p in list(spec.dataOutPorts or []):
                    port_name = f"{p.name}[D]"
                    if node.data_port_show_on_node(str(p.name or ""), is_in=False) or port_name in existing_out:
                        data_out_names.append(port_name)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                data_in_names = []
                data_out_names = []

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

        y = float(v_offset)
        groups: list[tuple[str, list[str], list[str]]] = [
            ("exec", exec_in_names, exec_out_names),
            ("data", data_in_names, data_out_names),
            ("state", [f"[S]{n}" for n in state_names], [f"{n}[S]" for n in state_names]),
            ("other", other_in_names, other_out_names),
        ]

        for gi, (gname, ins, outs) in enumerate(groups):
            if gname == "state":
                rows = len(state_names)
            else:
                rows = max(len(ins), len(outs))
            if rows <= 0:
                continue
            for i in range(rows):
                in_name = ins[i] if i < len(ins) else None
                out_name = outs[i] if i < len(outs) else None

                if gname != "state":
                    place_row(in_name, out_name, y=y)
                    y += port_height + spacing
                    continue

                # State row: place collapsible panel + ports aligned to header line.
                state_key = state_names[i] if i < len(state_names) else None
                panel_proxy = self._state_inline_proxies.get(state_key) if state_key else None
                header_h = port_height
                body_h = 0.0
                if state_key and panel_proxy is not None:
                    # Ensure width is up to date before measuring heights (option rows wrap by width).
                    try:
                        w = panel_proxy.widget()
                        if w is not None:
                            w.setFixedWidth(int(inner_w))
                            w.adjustSize()
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
                    try:
                        if self._state_inline_headers.get(state_key) is not None:
                            header_h = float(
                                max(port_height, self._state_inline_headers[state_key].sizeHint().height())
                            )
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        header_h = port_height
                    try:
                        body_w = self._state_inline_bodies.get(state_key)
                        if body_w is not None and body_w.isVisible():
                            body_h = float(max(0.0, body_w.sizeHint().height()))
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        body_h = 0.0
                    try:
                        # Center panels using their *actual* width. Some controls
                        # can enforce minimum sizes that override our target width,
                        # causing asymmetric margins if we anchor at `inner_x`.
                        w = panel_proxy.widget()
                        if w is None:
                            panel_proxy.setPos(inner_x, y)
                        else:
                            panel_w = float(w.width() or 0)
                            if panel_w <= 0:
                                panel_w = float(panel_proxy.boundingRect().width() or 0)
                            if panel_w <= 0:
                                panel_proxy.setPos(inner_x, y)
                            else:
                                panel_x = rect.left() + (rect.width() - panel_w) / 2.0
                                # Clamp inside the node content area so the right edge
                                # never gets clipped by the node boundary.
                                min_x = float(inner_x)
                                max_x = float(rect.right() - 4.0 - panel_w)
                                if max_x < min_x:
                                    panel_x = min_x
                                else:
                                    panel_x = max(min_x, min(panel_x, max_x))
                                panel_proxy.setPos(panel_x, y)
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass

                port_y = y + (header_h - port_height) / 2.0
                place_row(in_name, out_name, y=port_y)
                y += header_h + spacing
                if body_h > 0.0:
                    y += body_h + spacing
            # group gap (except after last visible group)
            # determine if any later group has rows.
            has_later = False
            for _g2, ins2, outs2 in groups[gi + 1 :]:
                if _g2 == "state":
                    if len(state_names) > 0:
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
        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self._ensure_inline_command_widget()
        except (AttributeError, RuntimeError, TypeError):
            pass
        height = self._text_item.boundingRect().height() + 4.0

        # update port text items in visibility.
        for port, text in self._input_items.items():
            if port.isVisible():
                if self._port_group(_port_name(port)) == "state":
                    text.setVisible(False)
                else:
                    text.setVisible(port.display_name)
        for port, text in self._output_items.items():
            if port.isVisible():
                if self._port_group(_port_name(port)) == "state":
                    text.setVisible(False)
                else:
                    text.setVisible(port.display_name)

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

        self.update()

    def _draw_node_vertical(self):
        # hide the port text items in vertical layout.
        for port, text in self._input_items.items():
            text.setVisible(False)
        for port, text in self._output_items.items():
            text.setVisible(False)

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
        Decide whether to draw the node with proxy mode.
        (this is called at the start in the "self.paint()" function.)
        """
        if ITEM_CACHE_MODE is QtWidgets.QGraphicsItem.ItemCoordinateCache:
            return

        v = self._viewer_safe()
        if v is None:
            return

        rect = self.sceneBoundingRect()
        l = v.mapToGlobal(v.mapFromScene(rect.topLeft()))
        r = v.mapToGlobal(v.mapFromScene(rect.topRight()))
        # width is the node width in screen
        width = r.x() - l.x()

        self.set_proxy_mode(width < self._proxy_mode_threshold)

    def set_proxy_mode(self, mode):
        """
        Set whether to draw the node with proxy mode.
        (proxy mode toggles visibility for some qgraphic items in the node.)

        Args:
            mode (bool): true to enable proxy mode.
        """
        if mode is self._proxy_mode:
            return
        self._proxy_mode = mode

        visible = not mode

        if bool(mode):
            for proxy in self._state_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass
            if self._cmd_proxy is not None:
                try:
                    _clear_embedded_text_selection(self._cmd_proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass

        # disable overlay item.
        self._x_item.proxy_mode = self._proxy_mode

        # node widget visibility.
        for w in self._widgets.values():
            w.widget().setVisible(visible)
        for p in self._state_inline_proxies.values():
            try:
                p.setVisible(visible)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if self._cmd_proxy is not None:
            try:
                self._cmd_proxy.setVisible(visible)
            except (AttributeError, RuntimeError, TypeError):
                pass

        if visible:
            for proxy in self._state_inline_proxies.values():
                try:
                    _clear_embedded_text_selection(proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass
            if self._cmd_proxy is not None:
                try:
                    _clear_embedded_text_selection(self._cmd_proxy.widget())
                except (AttributeError, RuntimeError, TypeError):
                    pass

        # port text is not visible in vertical layout.
        if self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            port_text_visible = False
        else:
            port_text_visible = visible

        # input port text visibility.
        for port, text in self._input_items.items():
            try:
                is_state = self._port_group(_port_name(port)) == "state"
            except (AttributeError, RuntimeError, TypeError, ValueError):
                is_state = False
            should_show = bool(port_text_visible and port.display_name and not is_state)
            text.setVisible(should_show)

        # output port text visibility.
        for port, text in self._output_items.items():
            try:
                is_state = self._port_group(_port_name(port)) == "state"
            except (AttributeError, RuntimeError, TypeError, ValueError):
                is_state = False
            should_show = bool(port_text_visible and port.display_name and not is_state)
            text.setVisible(should_show)

        self._text_item.setVisible(visible)
        self._icon_item.setVisible(visible)

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
