from __future__ import annotations

from typing import Any

from NodeGraphQt.constants import NodeEnum, PortEnum

from .operator_basenode import F8StudioOperatorNodeItem


def _required_non_state_port_region_height(*, port_height: float, in_count: int, out_count: int) -> float:
    max_count = max(0, int(in_count), int(out_count))
    if max_count <= 0:
        return 0.0
    base_port_h = max(float(port_height), float(PortEnum.SIZE.value))
    row_gap = 4.0
    pad = 8.0
    return (base_port_h * float(max_count)) + (row_gap * float(max_count - 1)) + (pad * 2.0)


class F8StudioVizOperatorNodeItem(F8StudioOperatorNodeItem):
    """
    Viz-style operator node layout.

    Intended for render/viz nodes where:
    - state controls are placed first (top-to-bottom)
    - command rows follow visible state rows
    - embedded widgets come after state
    - data/exec/other ports are aligned alongside the widget region (no port rows)
    """

    @staticmethod
    def _port_name(port) -> str:
        try:
            return str(port.name() or "")
        except (AttributeError, TypeError):
            try:
                return str(port.name or "")
            except (AttributeError, TypeError):
                return ""

    @staticmethod
    def _state_field_name_if_visible(state_field) -> str | None:
        """
        Best-effort, explicit access for both dict-style and typed specs.
        """
        if isinstance(state_field, dict):
            if not bool(state_field.get("showOnNode") or False):
                return None
            name = str(state_field.get("name") or "").strip()
            return name or None
        try:
            if not bool(state_field.showOnNode):
                return None
        except (AttributeError, TypeError):
            return None
        try:
            name = str(state_field.name or "").strip()
        except (AttributeError, TypeError):
            return None
        return name or None

    def _calc_size_horizontal(self):  # type: ignore[override]
        # width, height from node name text.
        text_w = self._text_item.boundingRect().width()
        text_h = self._text_item.boundingRect().height()

        # Determine base port geometry.
        port_width = 0.0
        port_height = 0.0
        for p in list(self.inputs) + list(self.outputs):
            try:
                if not p.isVisible():
                    continue
                port_width = float(p.boundingRect().width())
                port_height = float(p.boundingRect().height())
                break
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        # State inline panel heights (span node width).
        state_h = 0.0
        command_h = 0.0
        spacing = 1.0
        group_gap = 6.0

        try:
            self._ensure_state_inline_controls()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._prepare_layout_metrics()

        state_names: list[str] = []
        try:
            node = self._backend_node()
            eff_states = list(node.effective_state_fields() or []) if node is not None else []
        except Exception:
            eff_states = []
        for s in eff_states:
            nm = self._state_field_name_if_visible(s)
            if nm:
                state_names.append(nm)
        if not state_names:
            state_names = self._visible_state_names_for_layout()

        if state_names:
            inner_width = max(float(NodeEnum.WIDTH.value) - 8.0, 10.0)
            for sname in state_names:
                metric = self._measure_state_panel_metric(sname, inner_width)
                panel_h = float(max(metric.height, port_height or float(PortEnum.SIZE.value)))
                state_h += panel_h + spacing
            state_h = max(0.0, state_h - spacing) + group_gap

        command_names = self._visible_command_names_for_layout()
        if command_names:
            inner_width = max(float(NodeEnum.WIDTH.value) - 8.0, 10.0)
            for command_name in command_names:
                metric = self._measure_command_row_metric(command_name, inner_width)
                panel_h = float(max(metric.height, port_height or float(PortEnum.SIZE.value)))
                command_h += panel_h + spacing
            command_h = max(0.0, command_h - spacing) + group_gap

        # Embedded widgets (eg. plot canvas).
        widget_width = 0.0
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible() and not self._proxy_mode:
                continue
            metric = self._measure_embedded_widget(widget)
            widget_width = max(widget_width, float(metric.width))
            widget_height += float(metric.height)

        in_non_state_count = 0
        out_non_state_count = 0
        for port in self.inputs:
            try:
                if not port.isVisible():
                    continue
                if self._port_group(self._port_name(port)) == "state":
                    continue
                in_non_state_count += 1
            except (AttributeError, RuntimeError, TypeError):
                continue
        for port in self.outputs:
            try:
                if not port.isVisible():
                    continue
                if self._port_group(self._port_name(port)) == "state":
                    continue
                out_non_state_count += 1
            except (AttributeError, RuntimeError, TypeError):
                continue

        required_port_region_h = _required_non_state_port_region_height(
            port_height=port_height,
            in_count=in_non_state_count,
            out_count=out_non_state_count,
        )

        side_padding = 10.0 if widget_width else 0.0
        width = max(float(NodeEnum.WIDTH.value), float(text_w + 18.0), float(widget_width + side_padding))

        port_region_h = state_h + command_h + max(widget_height, required_port_region_h)
        height = max(float(NodeEnum.HEIGHT.value), float(text_h), float(port_region_h))
        if widget_height:
            height += 10.0

        return width, height

    def _build_state_inline_control(
        self,
        state_field: Any,
        *,
        widget_parent=None,
    ):  # type: ignore[override]
        """
        Override a couple of fields for viz nodes:
        - minVal/maxVal: allow blank (auto) via QLineEdit (stores None/float)
        """
        nm = self._state_field_name_if_visible(state_field)
        name = nm or ""
        if name not in {"minVal", "maxVal"}:
            return super()._build_state_inline_control(state_field, widget_parent=widget_parent)

        from qtpy import QtCore, QtWidgets

        from ..ui.support.studio_theme import inline_control_qss

        def _common_style(w: QtWidgets.QWidget) -> None:
            w.setStyleSheet(inline_control_qss())

        def _set_node_value(value: Any, *, push_undo: bool) -> None:
            node = self._backend_node()
            if node is None or not name:
                return
            try:
                node.set_property(name, value, push_undo=push_undo)
            except TypeError:
                node.set_property(name, value)

        def _get_node_value() -> Any:
            node = self._backend_node()
            if node is None or not name:
                return None
            try:
                return node.get_property(name)
            except KeyError:
                return None

        line = QtWidgets.QLineEdit(widget_parent)
        line.setMinimumWidth(90)
        line.setPlaceholderText("auto")
        _common_style(line)

        def _apply_value(v: Any) -> None:
            s = "" if v is None else str(v)
            with QtCore.QSignalBlocker(line):
                line.setText(s)

        _apply_value(_get_node_value())
        self._state_inline_updaters[name] = _apply_value

        def _commit() -> None:
            raw = str(line.text() or "").strip()
            if not raw:
                _set_node_value(None, push_undo=True)
                return
            try:
                v = float(raw)
            except ValueError:
                # Keep previous value if parsing fails.
                _apply_value(_get_node_value())
                return
            if v != v:  # NaN
                _set_node_value(None, push_undo=True)
                return
            _set_node_value(v, push_undo=True)

        line.editingFinished.connect(_commit)
        return line

    def _set_port_text_visibility(self, *, visible: bool) -> None:  # type: ignore[override]
        _ = visible
        for text in self._input_items.values():
            text.setVisible(False)
        for text in self._output_items.values():
            text.setVisible(False)

    def _ordered_non_state_ports_for_widget_region(self, *, is_in: bool) -> list[Any]:
        ports = list(self.inputs if bool(is_in) else self.outputs)
        visible_by_name: dict[str, Any] = {}
        visible_names_in_insertion_order: list[str] = []
        for port in ports:
            try:
                if not port.isVisible():
                    continue
                port_name = self._port_name(port)
            except (AttributeError, RuntimeError, TypeError):
                continue
            if not port_name or self._port_group(port_name) == "state":
                continue
            visible_names_in_insertion_order.append(port_name)
            visible_by_name[port_name] = port

        ordered_names: list[str] = []
        ordered_names.extend(self._ordered_exec_port_names_for_layout(is_in=bool(is_in)))
        ordered_names.extend(self._ordered_data_port_names_for_layout(is_in=bool(is_in)))
        ordered_names.extend(self._ordered_command_port_names_for_layout(is_in=bool(is_in)))

        ordered_ports: list[Any] = []
        seen_names: set[str] = set()
        for name in ordered_names:
            normalized_name = str(name or "").strip()
            if not normalized_name or normalized_name in seen_names:
                continue
            port = visible_by_name.get(normalized_name)
            if port is None:
                continue
            ordered_ports.append(port)
            seen_names.add(normalized_name)

        for name in visible_names_in_insertion_order:
            if name in seen_names:
                continue
            port = visible_by_name.get(name)
            if port is None:
                continue
            ordered_ports.append(port)
            seen_names.add(name)
        return ordered_ports

    def _align_viz_state(self, v_offset: float) -> float:
        """
        Align visible state rows first, then visible command rows, and return the y
        position after the inline-row block.
        """
        width = float(self._width)
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
            except Exception:
                spec = None
        try:
            eff_states = list(node.effective_state_fields() or []) if node is not None else []
        except Exception:
            if spec is None:
                eff_states = []
            else:
                try:
                    eff_states = list(spec.stateFields or [])
                except Exception:
                    eff_states = []

        state_names: list[str] = []
        for s in eff_states:
            nm = self._state_field_name_if_visible(s)
            if nm:
                state_names.append(nm)
        command_names = self._visible_command_names_for_layout()

        inputs_by_name = {self._port_name(p): p for p in self.inputs if p.isVisible()}
        outputs_by_name = {self._port_name(p): p for p in self.outputs if p.isVisible()}

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
        inner_x = float(rect.left() + 4.0)
        inner_w = float(max(10.0, rect.width() - 8.0))

        def place_row(in_name: str | None, out_name: str | None, *, y: float) -> None:
            if in_name:
                p = inputs_by_name.get(in_name)
                if p is not None:
                    p.setPos(in_x, y)
            if out_name:
                p = outputs_by_name.get(out_name)
                if p is not None:
                    p.setPos(out_x, y)

        y = float(v_offset)
        for sname in state_names:
            in_name = f"[S]{sname}"
            out_name = f"{sname}[S]"

            panel_proxy = self._state_inline_proxies.get(sname)
            metric = self._measure_state_panel_metric(sname, inner_w)
            header_h = float(max(port_height or float(PortEnum.SIZE.value), metric.header_height))
            panel_h = float(max(header_h, metric.height))
            if panel_proxy is not None:
                panel_w = float(max(metric.width, inner_w))
                panel_x = rect.left() + (rect.width() - panel_w) / 2.0
                min_x = float(inner_x)
                max_x = float(rect.right() - 4.0 - panel_w)
                if max_x < min_x:
                    panel_x = min_x
                else:
                    panel_x = max(min_x, min(panel_x, max_x))
                try:
                    panel_proxy.setPos(panel_x, y)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass

            port_y = y + (header_h - port_height) / 2.0 if port_height else y
            place_row(in_name, out_name, y=port_y)

            y += panel_h + spacing

        if state_names:
            y += group_gap

        for command_name in command_names:
            in_name = f"[C]{command_name}"
            out_name = f"{command_name}[C]"

            panel_proxy = self._command_inline_proxies.get(command_name)
            metric = self._measure_command_row_metric(command_name, inner_w)
            header_h = float(max(port_height or float(PortEnum.SIZE.value), metric.header_height))
            panel_h = float(max(header_h, metric.height))
            if panel_proxy is not None:
                panel_w = float(max(metric.width, inner_w))
                panel_x = rect.left() + (rect.width() - panel_w) / 2.0
                min_x = float(inner_x)
                max_x = float(rect.right() - 4.0 - panel_w)
                if max_x < min_x:
                    panel_x = min_x
                else:
                    panel_x = max(min_x, min(panel_x, max_x))
                try:
                    panel_proxy.setPos(panel_x, y)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass

            port_y = y + (header_h - port_height) / 2.0 if port_height else y
            place_row(in_name, out_name, y=port_y)
            y += panel_h + spacing

        if command_names:
            y += group_gap

        self._ports_end_y = y
        return y

    def _align_viz_ports_to_widgets(self, v_offset: float) -> None:
        """
        Align non-state ports along the widget region (left/right of embedded widgets).
        """
        rect = self.boundingRect()

        widget_items = [w for w in self._widgets.values()]
        if not widget_items:
            return

        # Widget area in local node coords.
        top = float("inf")
        bottom = float("-inf")
        for w in widget_items:
            try:
                pos_y = float(w.pos().y())
                metric = self._measure_embedded_widget(w)
                h = float(max(w.boundingRect().height(), metric.height))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            top = min(top, pos_y)
            bottom = max(bottom, pos_y + h)
        if not (top < float("inf") and bottom > float("-inf") and bottom > top):
            return

        # Non-state ports.
        in_ports = self._ordered_non_state_ports_for_widget_region(is_in=True)
        out_ports = self._ordered_non_state_ports_for_widget_region(is_in=False)

        if not in_ports and not out_ports:
            return

        # Port geometry.
        port_width = 0.0
        port_height = 0.0
        for p in (in_ports + out_ports):
            try:
                port_width = float(p.boundingRect().width())
                port_height = float(p.boundingRect().height())
                break
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        width = float(self._width)
        in_x = (port_width / 2.0) * -1.0
        out_x = width - (port_width / 2.0)

        required_region_h = _required_non_state_port_region_height(
            port_height=port_height,
            in_count=len(in_ports),
            out_count=len(out_ports),
        )
        current_region_h = float(max(0.0, bottom - top))
        if required_region_h > current_region_h:
            candidate_top = float(self._ports_end_y or top)
            candidate_bottom = float(self._height - 6.0)
            if candidate_bottom > candidate_top:
                top = candidate_top
                bottom = candidate_bottom

        pad = 8.0
        min_cy = top + pad
        max_cy = bottom - pad
        if max_cy <= min_cy:
            min_cy = top
            max_cy = bottom

        def y_for(index: int, count: int) -> float:
            if count <= 0:
                return min_cy
            t = (index + 1) / (count + 1)
            cy = min_cy + (max_cy - min_cy) * t
            return cy - (port_height / 2.0 if port_height else 0.0)

        for i, p in enumerate(in_ports):
            try:
                p.setPos(in_x, y_for(i, len(in_ports)))
            except (AttributeError, RuntimeError, TypeError):
                continue
        for i, p in enumerate(out_ports):
            try:
                p.setPos(out_x, y_for(i, len(out_ports)))
            except (AttributeError, RuntimeError, TypeError):
                continue

        # Ensure any visible text items follow (but we typically keep them hidden for viz nodes).
        txt_offset = PortEnum.CLICK_FALLOFF.value - 2
        try:
            for port, text in self._input_items.items():
                if port.isVisible():
                    txt_x = port.boundingRect().width() / 2 - txt_offset
                    text.setPos(txt_x, port.y() - 1.5)
            for port, text in self._output_items.items():
                if port.isVisible():
                    txt_width = text.boundingRect().width() - txt_offset
                    txt_x = port.x() - txt_width
                    text.setPos(txt_x, port.y() - 1.5)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _draw_node_horizontal(self):  # type: ignore[override]
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

        header_h = float(self._text_item.boundingRect().height() + 4.0)
        target_proxy_mode = self._should_enable_proxy_mode()

        # setup initial base size.
        self._set_base_size(add_h=header_h)
        self._set_text_color(self.text_color)
        self._tooltip_disable(self.disabled)

        self.align_label()
        self.align_icon(h_offset=2.0, v_offset=1.0)

        # 1) state first
        self._align_viz_state(v_offset=header_h)
        # 2) then widgets (uses _ports_end_y to avoid overlap)
        self.align_widgets(v_offset=header_h)
        # 3) ports aligned to widget region
        self._align_viz_ports_to_widgets(v_offset=header_h)
        self.sync_proxy_mode(force=True)

        if self._proxy_mode != target_proxy_mode:
            self._align_viz_state(v_offset=header_h)
            self.align_widgets(v_offset=header_h)
            self._align_viz_ports_to_widgets(v_offset=header_h)
            self.sync_proxy_mode(force=True)

        self.update()
