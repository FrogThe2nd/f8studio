from __future__ import annotations

from typing import Any, Protocol, cast

from qtpy import QtGui, QtWidgets

from NodeGraphQt.constants import ITEM_CACHE_MODE, PortTypeEnum
from NodeGraphQt.errors import NodeWidgetError


class _ServiceNodePortsHost(Protocol):
    _input_items: dict[Any, Any]
    _output_items: dict[Any, Any]
    _widgets: dict[str, Any]

    def _port_group(self, name: str) -> str: ...

    def _display_port_label(self, name: str, *, max_chars: int | None = None) -> str: ...

    def _port_tooltip_text(self, view_name: str) -> str: ...

    def _create_input_port_item(self, *, painter_func: Any = None) -> Any: ...

    def _create_output_port_item(self, *, painter_func: Any = None) -> Any: ...

    def scene(self) -> Any: ...

    def post_init(self) -> None: ...


class ServiceNodePortsMixin:
    def _add_port(self, port: Any) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        full_name = str(port.name or "")
        group = host._port_group(full_name)
        max_chars = 10 if group == "state" else 18
        label = host._display_port_label(full_name, max_chars=max_chars)
        text = QtWidgets.QGraphicsTextItem(label, cast(Any, self))
        font = QtGui.QFont(text.font())
        font.setPointSize(8)
        text.setFont(font)
        text.setVisible(port.display_name)
        text.setCacheMode(ITEM_CACHE_MODE)
        tooltip = host._port_tooltip_text(full_name)
        try:
            text.setToolTip(tooltip)
            port.setToolTip(tooltip)
        except (AttributeError, RuntimeError, TypeError):
            pass
        if port.port_type == PortTypeEnum.IN.value:
            host._input_items[port] = text
        elif port.port_type == PortTypeEnum.OUT.value:
            host._output_items[port] = text
        if host.scene():
            host.post_init()
        return port

    def add_input(
        self,
        name: str = "input",
        multi_port: bool = False,
        display_name: bool = True,
        locked: bool = False,
        painter_func: Any = None,
    ) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        port = host._create_input_port_item(painter_func=painter_func)
        port.name = name
        port.port_type = PortTypeEnum.IN.value
        port.multi_connection = multi_port
        port.display_name = display_name
        port.locked = locked
        return self._add_port(port)

    def add_output(
        self,
        name: str = "output",
        multi_port: bool = False,
        display_name: bool = True,
        locked: bool = False,
        painter_func: Any = None,
    ) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        port = host._create_output_port_item(painter_func=painter_func)
        port.name = name
        port.port_type = PortTypeEnum.OUT.value
        port.multi_connection = multi_port
        port.display_name = display_name
        port.locked = locked
        return self._add_port(port)

    def _delete_port(self, port: Any, text: Any) -> None:
        port.setParentItem(None)
        text.setParentItem(None)
        host = cast(_ServiceNodePortsHost, self)
        scene = host.scene()
        if scene is not None:
            scene.removeItem(port)
            scene.removeItem(text)
        del port
        del text

    def delete_input(self, port: Any) -> None:
        host = cast(_ServiceNodePortsHost, self)
        self._delete_port(port=port, text=host._input_items.pop(port))

    def delete_output(self, port: Any) -> None:
        host = cast(_ServiceNodePortsHost, self)
        self._delete_port(port=port, text=host._output_items.pop(port))

    def get_input_text_item(self, port_item: Any) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        return host._input_items[port_item]

    def get_output_text_item(self, port_item: Any) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        return host._output_items[port_item]

    @property
    def widgets(self) -> dict[str, Any]:
        host = cast(_ServiceNodePortsHost, self)
        return host._widgets.copy()

    def add_widget(self, widget: Any) -> None:
        host = cast(_ServiceNodePortsHost, self)
        host._widgets[widget.get_name()] = widget

    def get_widget(self, name: str) -> Any:
        host = cast(_ServiceNodePortsHost, self)
        widget = host._widgets.get(name)
        if widget:
            return widget
        raise NodeWidgetError(f'node has no widget "{name}"')

    def has_widget(self, name: str) -> bool:
        host = cast(_ServiceNodePortsHost, self)
        return name in host._widgets

    def from_dict(self, node_dict: dict[str, Any]) -> None:
        cast(Any, super()).from_dict(node_dict)
        custom_prop = node_dict.get("custom") or {}
        host = cast(_ServiceNodePortsHost, self)
        for prop_name, value in custom_prop.items():
            prop_widget = host._widgets.get(prop_name)
            if prop_widget:
                prop_widget.set_value(value)
