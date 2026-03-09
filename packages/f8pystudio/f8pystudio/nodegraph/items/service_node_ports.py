from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from NodeGraphQt.constants import ITEM_CACHE_MODE, PortTypeEnum
from NodeGraphQt.errors import NodeWidgetError


def add_port(node_item: Any, port: Any) -> Any:
    """
    Adds a port qgraphics item into the node.
    """
    full_name = str(port.name or "")
    group = node_item._port_group(full_name)
    # Aggressively elide state port labels to reduce width usage.
    max_chars = 10 if group == "state" else 18
    label = node_item._display_port_label(full_name, max_chars=max_chars)
    text = QtWidgets.QGraphicsTextItem(label, node_item)
    text.font().setPointSize(8)
    text.setFont(text.font())
    text.setVisible(port.display_name)
    text.setCacheMode(ITEM_CACHE_MODE)
    tooltip = node_item._port_tooltip_text(full_name)
    try:
        text.setToolTip(tooltip)
        port.setToolTip(tooltip)
    except (AttributeError, RuntimeError, TypeError):
        pass
    if port.port_type == PortTypeEnum.IN.value:
        node_item._input_items[port] = text
    elif port.port_type == PortTypeEnum.OUT.value:
        node_item._output_items[port] = text
    if node_item.scene():
        node_item.post_init()
    return port


def add_input(
    node_item: Any,
    *,
    port_name: str = "input",
    multi_port: bool = False,
    display_name: bool = True,
    locked: bool = False,
    painter_func: Any = None,
    port_item_cls: type[Any],
    custom_port_item_cls: type[Any],
) -> Any:
    """
    Adds an input port qgraphics item into the node.
    """
    if painter_func:
        port = custom_port_item_cls(node_item, painter_func)
    else:
        port = port_item_cls(node_item)
    port.name = port_name
    port.port_type = PortTypeEnum.IN.value
    port.multi_connection = multi_port
    port.display_name = display_name
    port.locked = locked
    return add_port(node_item, port)


def add_output(
    node_item: Any,
    *,
    port_name: str = "output",
    multi_port: bool = False,
    display_name: bool = True,
    locked: bool = False,
    painter_func: Any = None,
    port_item_cls: type[Any],
    custom_port_item_cls: type[Any],
) -> Any:
    """
    Adds an output port qgraphics item into the node.
    """
    if painter_func:
        port = custom_port_item_cls(node_item, painter_func)
    else:
        port = port_item_cls(node_item)
    port.name = port_name
    port.port_type = PortTypeEnum.OUT.value
    port.multi_connection = multi_port
    port.display_name = display_name
    port.locked = locked
    return add_port(node_item, port)


def delete_port(node_item: Any, *, port: Any, text: Any) -> None:
    """
    Removes a port item and its port text from node.
    """
    port.setParentItem(None)
    text.setParentItem(None)
    scene = node_item.scene()
    if scene is not None:
        scene.removeItem(port)
        scene.removeItem(text)
    del port
    del text


def delete_input(node_item: Any, port: Any) -> None:
    delete_port(node_item, port=port, text=node_item._input_items.pop(port))


def delete_output(node_item: Any, port: Any) -> None:
    delete_port(node_item, port=port, text=node_item._output_items.pop(port))


def get_input_text_item(node_item: Any, port_item: Any) -> Any:
    return node_item._input_items[port_item]


def get_output_text_item(node_item: Any, port_item: Any) -> Any:
    return node_item._output_items[port_item]


def widgets(node_item: Any) -> dict[str, Any]:
    return node_item._widgets.copy()


def add_widget(node_item: Any, widget: Any) -> None:
    node_item._widgets[widget.get_name()] = widget


def get_widget(node_item: Any, name: str) -> Any:
    widget = node_item._widgets.get(name)
    if widget:
        return widget
    raise NodeWidgetError('node has no widget "{}"'.format(name))


def has_widget(node_item: Any, name: str) -> bool:
    return name in node_item._widgets.keys()


def from_dict(node_item: Any, node_dict: dict[str, Any]) -> None:
    custom_prop = node_dict.get("custom") or {}
    for prop_name, value in custom_prop.items():
        prop_widget = node_item._widgets.get(prop_name)
        if prop_widget:
            prop_widget.set_value(value)
