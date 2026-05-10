from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from NodeGraphQt.constants import NodeEnum
from NodeGraphQt.qgraphics.node_abstract import AbstractNodeItem
from NodeGraphQt.qgraphics.node_backdrop import BackdropNodeItem
from NodeGraphQt.qgraphics.node_text_item import NodeTextItem

from .items.backdrop_sizer import F8StudioBackdropSizer


class F8StudioBackdropNodeItem(BackdropNodeItem):
    _FILL_ALPHA = 20
    _HEADER_ALPHA = 42
    _SELECTED_FILL_ALPHA = 10
    _TITLE_BAR_HEIGHT = 26.0
    _BORDER_WIDTH = 1.0
    _BORDER_STYLE = QtCore.Qt.DashLine
    _text_item: NodeTextItem | None = None

    def __init__(self, name: str = "backdrop", text: str = "", parent=None):
        super().__init__(name=name, text=text, parent=parent)
        self._replace_default_sizer()
        self._text_item = NodeTextItem(self.name, self)
        self._sync_title_text_item_text()
        self._sync_title_text_item_style()
        self._sync_title_text_item_geometry()

    def _replace_default_sizer(self) -> None:
        old_sizer = self._sizer
        sizer_x = float(old_sizer.pos().x()) + float(old_sizer.size)
        sizer_y = float(old_sizer.pos().y()) + float(old_sizer.size)
        if old_sizer.scene() is not None:
            old_sizer.scene().removeItem(old_sizer)
        old_sizer.setParentItem(None)
        self._sizer = F8StudioBackdropSizer(self, 26.0)
        self._sizer.set_pos(sizer_x, sizer_y)

    def _sync_title_text_item_text(self) -> None:
        text_item = self._text_item
        if text_item is None:
            return
        name = str(self.name or "")
        if name != text_item.toPlainText():
            text_item.setPlainText(name)

    def _sync_title_text_item_style(self) -> None:
        text_item = self._text_item
        if text_item is None:
            return
        text_item.setDefaultTextColor(QtGui.QColor(*self.text_color))

    def _sync_title_text_item_geometry(self) -> None:
        text_item = self._text_item
        if text_item is None:
            return
        text_rect = text_item.boundingRect()
        rect = self.boundingRect()
        x = rect.center().x() - (text_rect.width() / 2.0)
        y = rect.y() + ((self._TITLE_BAR_HEIGHT - text_rect.height()) / 2.0)
        text_item.setPos(x, y)

    def paint(self, painter, option, widget):  # type: ignore[override]
        del option, widget
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        margin = 1.0
        rect = self.boundingRect()
        rect = QtCore.QRectF(
            rect.left() + margin,
            rect.top() + margin,
            rect.width() - (margin * 2),
            rect.height() - (margin * 2),
        )
        radius = 3.0

        body_color = QtGui.QColor(self.color[0], self.color[1], self.color[2], self._FILL_ALPHA)
        header_color = QtGui.QColor(self.color[0], self.color[1], self.color[2], self._HEADER_ALPHA)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(body_color)
        painter.drawRoundedRect(rect, radius, radius)

        top_rect = QtCore.QRectF(rect.x(), rect.y(), rect.width(), self._TITLE_BAR_HEIGHT)
        painter.setBrush(header_color)
        painter.drawRoundedRect(top_rect, radius, radius)
        painter.drawRect(QtCore.QRectF(top_rect.left(), top_rect.bottom() - 5.0, 5.0, 5.0))
        painter.drawRect(QtCore.QRectF(top_rect.right() - 5.0, top_rect.bottom() - 5.0, 5.0, 5.0))

        if self.selected:
            selected_color = [value for value in NodeEnum.SELECTED_COLOR.value]
            selected_color[-1] = self._SELECTED_FILL_ALPHA
            painter.setBrush(QtGui.QColor(*selected_color))
            painter.drawRoundedRect(rect, radius, radius)

        border_color = self.color
        border_width = self._BORDER_WIDTH
        if self.selected and NodeEnum.SELECTED_BORDER_COLOR.value:
            border_color = NodeEnum.SELECTED_BORDER_COLOR.value
        border_pen = QtGui.QPen(QtGui.QColor(*border_color), border_width)
        border_pen.setStyle(self._BORDER_STYLE)
        border_pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()
        self._sync_title_text_item_geometry()

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton and not self.disabled:
            scene = self.scene()
            text_item = self._text_item
            if scene is not None and text_item is not None and text_item in scene.items(event.scenePos()):
                text_item.set_editable(True)
                text_item.setFocus()
                event.ignore()
                return
        viewer = self.viewer()
        if viewer:
            viewer.node_double_clicked.emit(self.id)
        super().mouseDoubleClickEvent(event)

    def from_dict(self, node_dict):  # type: ignore[override]
        super().from_dict(node_dict)
        self._sync_title_text_item_text()
        self._sync_title_text_item_style()
        self._sync_title_text_item_geometry()
        self.update(self.boundingRect())

    @property
    def name(self):
        return AbstractNodeItem.name.fget(self)

    @name.setter
    def name(self, name=""):
        AbstractNodeItem.name.fset(self, name)
        if self._text_item is None:
            return
        self._sync_title_text_item_text()
        self._sync_title_text_item_geometry()
        self.update()

    @property
    def text_color(self):
        return AbstractNodeItem.text_color.fget(self)

    @text_color.setter
    def text_color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.text_color.fset(self, color)
        if self._text_item is None:
            return
        self._sync_title_text_item_style()
        self.update()

    @property
    def text_item(self) -> NodeTextItem:
        text_item = self._text_item
        assert text_item is not None
        return text_item
