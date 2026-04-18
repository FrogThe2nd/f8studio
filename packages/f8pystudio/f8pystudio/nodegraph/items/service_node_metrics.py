from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from f8pysdk.command import parse_command_port_name

from qtpy import QtWidgets

from NodeGraphQt.constants import PortEnum
from NodeGraphQt.nodes.base_node import NodeBaseWidget

from .embedded_resize_contract import ResizableEmbeddedWidget
from .node_item_core import port_name as _port_name, state_field_info as _state_field_info


@dataclass
class LayoutMetric:
    cache_key: str = ""
    width: float = 0.0
    height: float = 0.0


@dataclass
class StatePanelLayoutMetric(LayoutMetric):
    header_height: float = 0.0


@dataclass
class HorizontalPortLayoutGroups:
    exec_in: list[str]
    exec_out: list[str]
    data_in: list[str]
    data_out: list[str]
    standalone_command_in: list[str]
    standalone_command_out: list[str]
    state_in: list[str]
    state_out: list[str]
    other_in: list[str]
    other_out: list[str]
    state_names: list[str]
    command_names: list[str]


def invalidate_layout_metrics(node_item: Any) -> None:
    node_item._layout_metrics_ready = False
    node_item._embedded_widget_metrics.clear()
    node_item._state_panel_metrics.clear()
    node_item._command_row_metrics.clear()


def prepare_layout_metrics(node_item: Any) -> None:
    ready = True
    for widget_proxy in node_item._widgets.values():
        try:
            if widget_proxy.widget() is None:
                ready = False
                break
        except (AttributeError, RuntimeError, TypeError):
            ready = False
            break
    if ready:
        for proxy in node_item._state_inline_proxies.values():
            try:
                if proxy.widget() is None:
                    ready = False
                    break
            except (AttributeError, RuntimeError, TypeError):
                ready = False
                break
    if ready:
        for proxy in node_item._command_inline_proxies.values():
            try:
                if proxy.widget() is None:
                    ready = False
                    break
            except (AttributeError, RuntimeError, TypeError):
                ready = False
                break
    node_item._layout_metrics_ready = bool(ready)


def requires_layout_metrics_for_proxy(node_item: Any) -> bool:
    return bool(node_item._widgets or node_item._state_inline_proxies or node_item._command_inline_proxies)


def supports_auto_proxy(node_item: Any) -> bool:
    if not requires_layout_metrics_for_proxy(node_item):
        return True
    return bool(node_item._layout_metrics_ready)


def activate_widget_layout(widget: QtWidgets.QWidget | None) -> None:
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


def measure_qwidget_geometry(
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
    activate_widget_layout(widget)

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


def embedded_widget_metric_key(widget_proxy: Any, *, target_width: float | None) -> str:
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


def measure_embedded_widget(
    node_item: Any,
    widget_proxy: Any,
    *,
    target_width: float | None = None,
) -> LayoutMetric:
    cache_key = embedded_widget_metric_key(widget_proxy, target_width=target_width)
    cached = node_item._embedded_widget_metrics.get(cache_key)
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
    width, height = measure_qwidget_geometry(group_widget, fixed_width=target_width_value)
    metric = LayoutMetric(cache_key=cache_key, width=float(width), height=float(height))
    node_item._embedded_widget_metrics[cache_key] = metric
    return metric


def state_panel_metric_cache_key(node_item: Any, name: str, *, target_width: float) -> str:
    width_key = max(1, int(round(target_width)))
    ctrl_serial = str(node_item._state_inline_ctrl_serial.get(name, "") or "")
    expanded = "1" if bool(node_item._state_inline_expanded.get(name, False)) else "0"
    return f"{name}|{width_key}|{expanded}|{ctrl_serial}"


def measure_state_panel_metric(node_item: Any, name: str, width: float) -> StatePanelLayoutMetric:
    cache_key = state_panel_metric_cache_key(node_item, name, target_width=width)
    return measure_inline_panel_metric(
        node_item,
        cache=node_item._state_panel_metrics,
        cache_key=cache_key,
        proxy_map=node_item._state_inline_proxies,
        header_map=node_item._state_inline_headers,
        name=name,
        width=width,
    )


def command_row_metric_cache_key(node_item: Any, name: str, *, target_width: float) -> str:
    width_key = max(1, int(round(target_width)))
    serial = str(node_item._command_inline_serials.get(name, "") or "")
    return f"{name}|{width_key}|{serial}"


def measure_command_row_metric(node_item: Any, name: str, width: float) -> StatePanelLayoutMetric:
    cache_key = command_row_metric_cache_key(node_item, name, target_width=width)
    return measure_inline_panel_metric(
        node_item,
        cache=node_item._command_row_metrics,
        cache_key=cache_key,
        proxy_map=node_item._command_inline_proxies,
        header_map=node_item._command_inline_headers,
        name=name,
        width=width,
    )


def measure_inline_panel_metric(
    node_item: Any,
    *,
    cache: dict[str, StatePanelLayoutMetric],
    cache_key: str,
    proxy_map: OrderedDict[str, QtWidgets.QGraphicsProxyWidget],
    header_map: OrderedDict[str, QtWidgets.QWidget],
    name: str,
    width: float,
) -> StatePanelLayoutMetric:
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
    panel_width, panel_height = measure_qwidget_geometry(panel_widget, fixed_width=width_value)

    header_height = float(PortEnum.SIZE.value)
    header = header_map.get(name)
    if header is not None:
        _header_width, measured_header_height = measure_qwidget_geometry(header, fixed_width=width_value)
        if measured_header_height > 0.0:
            header_height = float(measured_header_height)

    metric = StatePanelLayoutMetric(
        cache_key=cache_key,
        width=float(max(panel_width, float(width_value))),
        height=float(max(panel_height, header_height)),
        header_height=float(header_height),
    )
    cache[cache_key] = metric
    return metric


def visible_state_names_for_layout(node_item: Any) -> list[str]:
    state_names = ordered_visible_state_names_from_spec(node_item)
    if state_names:
        return state_names
    state_names = [str(name) for name in node_item._state_inline_proxies.keys() if str(name)]
    if state_names:
        return state_names
    inferred: list[str] = []
    for port in node_item._input_items.keys():
        name = _port_name(port)
        if name.startswith("[S]"):
            inferred.append(name[3:])
    for port in node_item._output_items.keys():
        name = _port_name(port)
        if name.endswith("[S]"):
            inferred.append(name[:-3])
    return [value for value in list(OrderedDict.fromkeys(inferred).keys()) if value]


def visible_command_names_for_layout(node_item: Any) -> list[str]:
    command_names = ordered_visible_command_names_from_spec(node_item)
    if command_names:
        return command_names
    command_names = [str(name) for name in node_item._command_inline_proxies.keys() if str(name)]
    if command_names:
        return command_names
    inferred: list[str] = []
    for port in node_item._input_items.keys():
        parsed = parse_command_port_name(_port_name(port))
        if parsed is None or not parsed[0]:
            continue
        inferred.append(parsed[1])
    for port in node_item._output_items.keys():
        parsed = parse_command_port_name(_port_name(port))
        if parsed is None or parsed[0]:
            continue
        inferred.append(parsed[1])
    return [value for value in list(OrderedDict.fromkeys(inferred).keys()) if value]


def command_names_with_inline_buttons(node_item: Any) -> set[str]:
    return {str(name).strip() for name in node_item._command_inline_buttons.keys() if str(name).strip()}


def ordered_exec_port_names_for_layout(node_item: Any, *, is_in: bool) -> list[str]:
    node = node_item._backend_node()
    if node is None:
        return []
    try:
        if bool(is_in):
            return [f"[E]{name}" for name in list(node.ordered_exec_port_names(is_in=True) or [])]
        return [f"{name}[E]" for name in list(node.ordered_exec_port_names(is_in=False) or [])]
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def ordered_data_port_names_for_layout(node_item: Any, *, is_in: bool) -> list[str]:
    node = node_item._backend_node()
    if node is None:
        return []
    current_names = {
        _port_name(port)
        for port in (node_item._input_items.keys() if bool(is_in) else node_item._output_items.keys())
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


def ordered_visible_state_names_from_spec(node_item: Any) -> list[str]:
    node = node_item._backend_node()
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


def ordered_visible_command_names_from_spec(node_item: Any) -> list[str]:
    node = node_item._backend_node()
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


def ordered_command_port_names_for_layout(node_item: Any, *, is_in: bool) -> list[str]:
    inline_command_names = command_names_with_inline_buttons(node_item)
    ordered: list[str] = []
    for command_name in ordered_visible_command_names_from_spec(node_item):
        if command_name in inline_command_names:
            continue
        ordered.append(f"[C]{command_name}" if bool(is_in) else f"{command_name}[C]")
    return ordered


def visible_port_names_for_group(node_item: Any, kind: str, *, is_in: bool) -> list[str]:
    items = node_item._input_items if is_in else node_item._output_items
    out: list[str] = []
    for port in items.keys():
        try:
            if not port.isVisible():
                continue
            port_name = _port_name(port)
            if node_item._port_group(port_name) == kind:
                out.append(port_name)
        except (AttributeError, RuntimeError, TypeError):
            continue
    return out


def infer_state_names_from_port_views(state_in: list[str], state_out: list[str]) -> list[str]:
    inferred: list[str] = []
    for name in state_in:
        if name.startswith("[S]"):
            inferred.append(name[3:])
    for name in state_out:
        if name.endswith("[S]"):
            inferred.append(name[:-3])
    return [value for value in list(OrderedDict.fromkeys(inferred).keys()) if value]


def filter_standalone_command_port_names(names: list[str], inline_command_names: set[str]) -> list[str]:
    if not inline_command_names:
        return names
    return [
        name
        for name in names
        if (parse_command_port_name(name) or (False, ""))[1] not in inline_command_names
    ]


def collect_horizontal_port_layout_groups(node_item: Any) -> HorizontalPortLayoutGroups:
    exec_in = ordered_exec_port_names_for_layout(node_item, is_in=True) or visible_port_names_for_group(
        node_item, "exec", is_in=True
    )
    exec_out = ordered_exec_port_names_for_layout(node_item, is_in=False) or visible_port_names_for_group(
        node_item, "exec", is_in=False
    )
    data_in = ordered_data_port_names_for_layout(node_item, is_in=True) or visible_port_names_for_group(
        node_item, "data", is_in=True
    )
    data_out = ordered_data_port_names_for_layout(node_item, is_in=False) or visible_port_names_for_group(
        node_item, "data", is_in=False
    )
    standalone_command_in = ordered_command_port_names_for_layout(
        node_item, is_in=True
    ) or visible_port_names_for_group(node_item, "command", is_in=True)
    standalone_command_out = ordered_command_port_names_for_layout(
        node_item, is_in=False
    ) or visible_port_names_for_group(node_item, "command", is_in=False)
    inline_command_names = command_names_with_inline_buttons(node_item)
    standalone_command_in = filter_standalone_command_port_names(standalone_command_in, inline_command_names)
    standalone_command_out = filter_standalone_command_port_names(standalone_command_out, inline_command_names)
    state_in = visible_port_names_for_group(node_item, "state", is_in=True)
    state_out = visible_port_names_for_group(node_item, "state", is_in=False)
    other_in = visible_port_names_for_group(node_item, "other", is_in=True)
    other_out = visible_port_names_for_group(node_item, "other", is_in=False)

    state_names = visible_state_names_for_layout(node_item)
    if not state_names:
        state_names = infer_state_names_from_port_views(state_in, state_out)
    command_names = visible_command_names_for_layout(node_item)
    return HorizontalPortLayoutGroups(
        exec_in=exec_in,
        exec_out=exec_out,
        data_in=data_in,
        data_out=data_out,
        standalone_command_in=standalone_command_in,
        standalone_command_out=standalone_command_out,
        state_in=state_in,
        state_out=state_out,
        other_in=other_in,
        other_out=other_out,
        state_names=state_names,
        command_names=command_names,
    )


def calculate_horizontal_port_area_height(
    node_item: Any,
    *,
    groups: HorizontalPortLayoutGroups,
    inner_width: float,
    base_port_height: float,
    spacing: float,
    group_gap: float,
) -> float:
    ports_h = 0.0

    def add_group_rows(rows: int) -> None:
        nonlocal ports_h
        if rows <= 0:
            return
        if ports_h > 0:
            ports_h += group_gap
        ports_h += (rows * base_port_height) + (max(0, rows - 1) * spacing)

    add_group_rows(max(len(groups.exec_in), len(groups.exec_out)))
    add_group_rows(max(len(groups.data_in), len(groups.data_out)))

    if groups.state_names:
        if ports_h > 0:
            ports_h += group_gap
        for state_name in groups.state_names:
            metric = measure_state_panel_metric(node_item, state_name, inner_width)
            panel_height = float(max(metric.height, base_port_height))
            ports_h += panel_height + spacing
        ports_h = max(0.0, ports_h - spacing)

    if groups.command_names:
        if ports_h > 0:
            ports_h += group_gap
        for command_name in groups.command_names:
            metric = measure_command_row_metric(node_item, command_name, inner_width)
            panel_height = float(max(metric.height, base_port_height))
            ports_h += panel_height + spacing
        ports_h = max(0.0, ports_h - spacing)

    add_group_rows(max(len(groups.standalone_command_in), len(groups.standalone_command_out)))
    add_group_rows(max(len(groups.other_in), len(groups.other_out)))
    return ports_h
