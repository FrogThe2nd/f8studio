from __future__ import annotations

from collections import OrderedDict

from NodeGraphQt.constants import PortEnum

from .operator_basenode import F8StudioOperatorNodeItem


class F8StudioPatchHubNodeItem(F8StudioOperatorNodeItem):
    """
    Compact patch-bay style operator item.

    Patch hubs only display paired data/state terminals and intentionally do
    not render inline state editors.
    """

    def _ensure_state_inline_controls(self) -> None:  # type: ignore[override]
        return

    def _ensure_inline_command_rows(self) -> None:  # type: ignore[override]
        return

    @staticmethod
    def _port_name(port) -> str:
        try:
            return str(port.name() or "")
        except (AttributeError, RuntimeError, TypeError):
            try:
                return str(port.name or "")
            except (AttributeError, RuntimeError, TypeError):
                return ""

    def _set_port_text_visibility(self, *, visible: bool) -> None:  # type: ignore[override]
        for port, text in self._input_items.items():
            port_name = self._port_name(port)
            show = bool(
                visible
                and port.isVisible()
                and (port_name.startswith("[D]") or port_name.startswith("[S]"))
                and bool(port.display_name)
            )
            text.setVisible(show)
        for text in self._output_items.values():
            text.setVisible(False)

    def _terminal_names(self, *, kind: str) -> list[str]:
        node = self._backend_node()
        if node is not None:
            if kind == "data":
                try:
                    current_names = {
                        self._port_name(port)
                        for port in [*list(self._input_items.keys()), *list(self._output_items.keys())]
                        if self._port_name(port)
                    }
                    ordered: list[str] = []
                    for port in list(node.spec.dataInPorts or []):
                        name = str(port.name or "").strip()
                        if not name:
                            continue
                        in_name = f"[D]{name}"
                        out_name = f"{name}[D]"
                        if in_name in current_names or out_name in current_names:
                            ordered.append(name)
                    if ordered:
                        return [value for value in list(OrderedDict.fromkeys(ordered).keys()) if value]
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            if kind == "state":
                try:
                    ordered = []
                    for field in list(node.effective_state_fields() or []):
                        name = str(field.name or "").strip()
                        if not name or not bool(field.showOnNode):
                            continue
                        ordered.append(name)
                    if ordered:
                        return [value for value in list(OrderedDict.fromkeys(ordered).keys()) if value]
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass

        names: list[str] = []
        for port in self._input_items.keys():
            port_name = self._port_name(port)
            if kind == "data" and port_name.startswith("[D]"):
                names.append(port_name[3:])
            elif kind == "state" and port_name.startswith("[S]"):
                names.append(port_name[3:])
        for port in self._output_items.keys():
            port_name = self._port_name(port)
            if kind == "data" and port_name.endswith("[D]"):
                names.append(port_name[:-3])
            elif kind == "state" and port_name.endswith("[S]"):
                names.append(port_name[:-3])
        return [value for value in list(OrderedDict.fromkeys(names).keys()) if value]

    def _calc_size_horizontal(self):  # type: ignore[override]
        text_w = float(self._text_item.boundingRect().width())
        text_h = float(self._text_item.boundingRect().height())
        label_w = 0.0
        for port, text in self._input_items.items():
            try:
                port_name = self._port_name(port)
                if port_name.startswith("[D]") or port_name.startswith("[S]"):
                    label_w = max(label_w, float(text.boundingRect().width()))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        port_width = 0.0
        port_height = 0.0
        for port in list(self.inputs) + list(self.outputs):
            try:
                if not port.isVisible():
                    continue
                port_width = float(port.boundingRect().width())
                port_height = float(port.boundingRect().height())
                break
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        row_height = max(float(PortEnum.SIZE.value), float(port_height or 0.0))
        row_gap = 4.0
        group_gap = 8.0
        data_rows = len(self._terminal_names(kind="data"))
        state_rows = len(self._terminal_names(kind="state"))
        total_rows = data_rows + state_rows
        group_count = int(bool(data_rows)) + int(bool(state_rows))

        body_height = (float(total_rows) * row_height) + (float(max(0, total_rows - 1)) * row_gap)
        if group_count > 1:
            body_height += float(group_count - 1) * group_gap

        width = max(132.0, float(text_w + 28.0), float(port_width + 108.0), float(label_w + 28.0))
        height = max(52.0, float(text_h + 18.0 + body_height))
        return width, height

    def _align_ports_horizontal(self, v_offset):  # type: ignore[override]
        width = float(self._width)
        row_height = float(PortEnum.SIZE.value)
        port_width = 0.0
        port_height = 0.0

        inputs_by_name = {}
        outputs_by_name = {}
        for port in self.inputs:
            try:
                if port.isVisible():
                    inputs_by_name[self._port_name(port)] = port
                    if not port_width:
                        port_width = float(port.boundingRect().width())
                        port_height = float(port.boundingRect().height())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        for port in self.outputs:
            try:
                if port.isVisible():
                    outputs_by_name[self._port_name(port)] = port
                    if not port_width:
                        port_width = float(port.boundingRect().width())
                        port_height = float(port.boundingRect().height())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        row_height = max(row_height, float(port_height or 0.0))
        row_gap = 4.0
        group_gap = 8.0
        in_x = (port_width / 2.0) * -1.0
        out_x = width - (port_width / 2.0)

        y = float(v_offset) + 6.0
        groups = (
            ("data", self._terminal_names(kind="data"), "[D]{}", "{}[D]"),
            ("state", self._terminal_names(kind="state"), "[S]{}", "{}[S]"),
        )
        wrote_group = False
        for _group_name, names, in_pattern, out_pattern in groups:
            if not names:
                continue
            if wrote_group:
                y += group_gap
            wrote_group = True
            for terminal_name in names:
                in_name = in_pattern.format(terminal_name)
                out_name = out_pattern.format(terminal_name)
                port_y = y + ((row_height - float(port_height or 0.0)) / 2.0 if port_height else 0.0)
                in_port = inputs_by_name.get(in_name)
                if in_port is not None:
                    in_port.setPos(in_x, port_y)
                out_port = outputs_by_name.get(out_name)
                if out_port is not None:
                    out_port.setPos(out_x, port_y)
                y += row_height + row_gap

        for port, text in self._input_items.items():
            port_name = self._port_name(port)
            if not (port_name.startswith("[D]") or port_name.startswith("[S]")):
                text.setVisible(False)
                continue
            text_x = (width - float(text.boundingRect().width())) / 2.0
            text_y = float(port.y()) - 1.5
            text.setPos(text_x, text_y)
        for text in self._output_items.values():
            text.setVisible(False)

        self._ports_end_y = y

    def _draw_node_horizontal(self):  # type: ignore[override]
        super()._draw_node_horizontal()
        self._icon_item.setVisible(False)
