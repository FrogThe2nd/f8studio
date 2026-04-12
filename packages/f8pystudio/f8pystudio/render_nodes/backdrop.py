from __future__ import annotations

from typing import Any

from ..nodegraph.backdrop_nodeitem import F8StudioBackdropNodeItem
from ..nodegraph.node_base import F8StudioBaseNode
from ..operators.backdrop import BackdropRuntimeNode


class BackdropRenderNode(F8StudioBaseNode):
    """Studio-only grouping backdrop with editable title and resizable region."""
    SPEC_TEMPLATE = BackdropRuntimeNode.SPEC

    def __init__(self) -> None:
        super().__init__(qgraphics_item=F8StudioBackdropNodeItem)
        self.model.color = (90, 170, 210, 255)
        self.model.text_color = (235, 245, 250, 255)

    def _apply_backdrop_size(
        self,
        *,
        width: float,
        height: float,
        pos_x: float,
        pos_y: float,
        push_undo: bool,
    ) -> None:
        self.set_property("width", float(width), push_undo=push_undo)
        self.set_property("height", float(height), push_undo=push_undo)
        self.set_property("pos", [float(pos_x), float(pos_y)], push_undo=push_undo)

    def on_backdrop_updated(self, update_prop: str, value: object | None = None) -> None:
        if not isinstance(value, dict):
            return
        if update_prop not in {"sizer_mouse_release", "sizer_double_clicked"}:
            return
        width = value.get("width")
        height = value.get("height")
        pos = value.get("pos")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            return
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return
        pos_x = float(pos[0])
        pos_y = float(pos[1])
        if self.graph is not None:
            action = 'resized "{}"'.format(self.name()) if update_prop == "sizer_mouse_release" else '"{}" auto resize'.format(self.name())
            self.graph.begin_undo(action)
            self._apply_backdrop_size(width=float(width), height=float(height), pos_x=pos_x, pos_y=pos_y, push_undo=True)
            self.graph.end_undo()
            return
        self.view.width = float(width)
        self.view.height = float(height)
        self.model.width = float(width)
        self.model.height = float(height)
        self.set_pos(pos_x, pos_y)

    def auto_size(self) -> None:
        if self.graph is None:
            return
        self.graph.begin_undo('"{}" auto resize'.format(self.name()))
        size = self.view.calc_backdrop_size()
        self._apply_backdrop_size(
            width=float(size["width"]),
            height=float(size["height"]),
            pos_x=float(size["pos"][0]),
            pos_y=float(size["pos"][1]),
            push_undo=True,
        )
        self.graph.end_undo()

    def wrap_nodes(self, nodes: list[Any], *, push_undo: bool = True, begin_undo_macro: bool = True) -> None:
        if not nodes or self.graph is None:
            return
        size = self.view.calc_backdrop_size([node.view for node in nodes])
        if push_undo and begin_undo_macro:
            self.graph.begin_undo('"{}" wrap nodes'.format(self.name()))
        try:
            self._apply_backdrop_size(
                width=float(size["width"]),
                height=float(size["height"]),
                pos_x=float(size["pos"][0]),
                pos_y=float(size["pos"][1]),
                push_undo=push_undo,
            )
        finally:
            if push_undo and begin_undo_macro:
                self.graph.end_undo()

    def nodes(self) -> list[Any]:
        graph = self.graph
        if graph is None:
            return []
        wrapped_nodes: list[Any] = []
        for node_view in self.view.get_nodes():
            node_id = str(node_view.id or "").strip()
            if not node_id:
                continue
            node = graph.get_node_by_id(node_id)
            if node is not None:
                wrapped_nodes.append(node)
        return wrapped_nodes

    def set_title(self, title: str) -> None:
        normalized_title = str(title or "").strip()
        if not normalized_title:
            return
        self.set_name(normalized_title)

    def title(self) -> str:
        return str(self.name() or "")

    def set_size(self, width: float, height: float) -> None:
        normalized_width = float(width)
        normalized_height = float(height)
        if self.graph is not None:
            self.graph.begin_undo("backdrop size")
            self.set_property("width", normalized_width, push_undo=True)
            self.set_property("height", normalized_height, push_undo=True)
            self.graph.end_undo()
            return
        self.view.width = normalized_width
        self.view.height = normalized_height
        self.model.width = normalized_width
        self.model.height = normalized_height

    def size(self) -> tuple[float, float]:
        self.model.width = float(self.view.width)
        self.model.height = float(self.view.height)
        return float(self.model.width), float(self.model.height)

    def inputs(self):  # type: ignore[override]
        return

    def outputs(self):  # type: ignore[override]
        return
