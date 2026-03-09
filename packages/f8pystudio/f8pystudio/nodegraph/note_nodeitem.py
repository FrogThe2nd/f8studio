from __future__ import annotations

from qtpy import QtWidgets

from NodeGraphQt.constants import NodeEnum
from NodeGraphQt.qgraphics.node_abstract import AbstractNodeItem
from NodeGraphQt.qgraphics.node_backdrop import BackdropSizer

from .items.embedded_resize_contract import (
    ResizableEmbeddedWidget,
    clamp_content_size,
)
from .service_basenode import F8StudioServiceNodeItem
from .viz_operator_nodeitem import F8StudioVizOperatorNodeItem

_NOTE_WIDGET_NAME = "__note_markdown"
_DEFAULT_MIN_SIZE = (260.0, 180.0)
_SIZER_SIZE = 16.0


class F8StudioNoteNodeItem(F8StudioVizOperatorNodeItem):
    """Viz operator node item with a bottom-right resizer."""

    def __init__(self, name: str = "node", parent=None):
        super().__init__(name=name, parent=parent)
        self._base_min_size = _DEFAULT_MIN_SIZE
        self._min_size = self._base_min_size
        self._user_width: float | None = None
        self._user_height: float | None = None
        self._syncing_sizer = False
        self._sizer = BackdropSizer(self, _SIZER_SIZE)
        self._sync_sizer_pos()

    @property
    def minimum_size(self) -> tuple[float, float]:
        return self._min_size

    @minimum_size.setter
    def minimum_size(self, size: tuple[float, float] = _DEFAULT_MIN_SIZE) -> None:
        self._min_size = (float(size[0]), float(size[1]))
        self._sync_sizer_pos()

    def _note_widget_proxy(self) -> ResizableEmbeddedWidget | None:
        named = self._widgets.get(_NOTE_WIDGET_NAME)
        if isinstance(named, ResizableEmbeddedWidget):
            return named
        for widget in self._widgets.values():
            if isinstance(widget, ResizableEmbeddedWidget):
                return widget
        return None

    def _dynamic_minimum_size(self) -> tuple[float, float]:
        min_w, min_h = self._base_min_size
        proxy = self._note_widget_proxy()
        if proxy is None:
            return min_w, min_h

        content_min_w, content_min_h = proxy.minimum_content_size()
        header_h = float(self._text_item.boundingRect().height() + 4.0)
        content_top = float(self._ports_end_y or header_h) + 6.0
        reserved_h = max(10.0, content_top + 4.0)
        min_w = max(min_w, float(content_min_w) + 8.0)
        min_h = max(min_h, float(content_min_h) + reserved_h)
        return min_w, min_h

    def _update_dynamic_minimum_size(self) -> None:
        self._min_size = self._dynamic_minimum_size()

    def _calc_size_horizontal(self):  # type: ignore[override]
        """
        Note node width must not be derived from embedded widget width.

        The widget is intentionally stretched to follow the node content rect;
        if widget width participates in node width calculation, each redraw can
        create positive feedback (node grows continuously).
        """
        text_w = float(self._text_item.boundingRect().width())
        text_h = float(self._text_item.boundingRect().height())

        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            try:
                widget_height += float(widget.boundingRect().height())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

        width = max(float(NodeEnum.WIDTH.value), float(text_w + 18.0))
        base_widget_h = widget_height + 10.0 if widget_height > 0.0 else 0.0
        height = max(float(NodeEnum.HEIGHT.value), float(text_h), float(base_widget_h))
        return width, height

    @AbstractNodeItem.width.setter
    def width(self, width=0.0):
        F8StudioServiceNodeItem.width.fset(self, float(width))
        self._user_width = float(self._width)
        self._sync_sizer_pos()

    @AbstractNodeItem.height.setter
    def height(self, height=0.0):
        F8StudioServiceNodeItem.height.fset(self, float(height))
        self._user_height = float(self._height)
        self._sync_sizer_pos()

    def _sync_sizer_pos(self) -> None:
        if self._syncing_sizer:
            return
        self._syncing_sizer = True
        try:
            self._sizer.set_pos(float(self._width), float(self._height))
        finally:
            self._syncing_sizer = False

    def _apply_size(self, *, width: float, height: float) -> None:
        if abs(width - self._width) <= 0.01 and abs(height - self._height) <= 0.01:
            return
        self.prepareGeometryChange()
        self._width = width
        self._height = height

    def _resize_note_content_widget(self) -> None:
        proxy = self._note_widget_proxy()
        if proxy is None:
            return
        rect = self.boundingRect()
        header_h = float(self._text_item.boundingRect().height() + 4.0)
        content_top = float(self._ports_end_y or (rect.y() + header_h)) + 6.0
        content_rect = self._content_rect_for_widgets(top_y=content_top)
        content_min = proxy.minimum_content_size()
        target_w, target_h = clamp_content_size(
            width=float(content_rect[2]),
            height=float(content_rect[3]),
            minimum=content_min,
        )
        try:
            proxy.prepareGeometryChange()
        except (AttributeError, RuntimeError, TypeError):
            pass
        proxy.apply_content_rect(target_w, target_h)
        try:
            proxy.update()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def on_sizer_pos_changed(self, pos) -> None:
        if self._syncing_sizer:
            return
        self._update_dynamic_minimum_size()
        min_width, min_height = self.minimum_size
        width = max(min_width, float(pos.x()) + float(self._sizer.size))
        height = max(min_height, float(pos.y()) + float(self._sizer.size))
        self._user_width = width
        self._user_height = height
        self._apply_size(width=width, height=height)
        self._resize_note_content_widget()
        self.draw_node()

    def on_sizer_pos_mouse_release(self) -> None:
        pass

    def on_sizer_double_clicked(self) -> None:
        self._user_width = None
        self._user_height = None
        self.draw_node()

    def _set_base_size(self, add_w=0.0, add_h=0.0):
        auto_w, auto_h = self.calc_size(add_w, add_h)
        self._update_dynamic_minimum_size()
        min_width, min_height = self.minimum_size
        target_width = max(float(auto_w), float(NodeEnum.WIDTH.value), min_width)
        target_height = max(float(auto_h), float(NodeEnum.HEIGHT.value), min_height)

        if self._user_width is not None:
            target_width = max(target_width, float(self._user_width))
        if self._user_height is not None:
            target_height = max(target_height, float(self._user_height))

        self._apply_size(width=target_width, height=target_height)
        self._sync_sizer_pos()

    def draw_node(self):
        super().draw_node()
        self._update_dynamic_minimum_size()
        self._resize_note_content_widget()
        self._sync_sizer_pos()

    def post_init(self, viewer=None, pos=None):
        super().post_init(viewer=viewer, pos=pos)
        self._sync_sizer_pos()

    def itemChange(self, change, value):  # type: ignore[override]
        out = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.ItemVisibleChange:
            self._sync_sizer_pos()
        return out
