from __future__ import annotations

import uuid

from qtpy import QtCore, QtGui, QtWidgets

from ....ui.support.ui_icons import StudioIcon, icon_for
from .common import _TAB_PANEL_MARGIN, _TAB_PANEL_SPACING


class _F8StateContainer(QtWidgets.QWidget):
    """
    Node properties container widget that displays nodes properties under
    a tab in the ``NodePropWidget`` widget.
    """

    class _ElideLabel(QtWidgets.QLabel):
        def __init__(self, text: str, parent: QtWidgets.QWidget | None = None):
            super().__init__("", parent)
            self._full_text = str(text or "")
            self.setText(self._full_text)

        def setText(self, text: str) -> None:  # type: ignore[override]
            self._full_text = str(text or "")
            self._update_elide()

        def resizeEvent(self, event):  # type: ignore[override]
            super().resizeEvent(event)
            self._update_elide()

        def _update_elide(self) -> None:
            try:
                fm = QtGui.QFontMetrics(self.font())
                elided = fm.elidedText(self._full_text, QtCore.Qt.ElideRight, max(10, int(self.width())))
                super().setText(elided)
            except Exception:
                super().setText(self._full_text)

    def __init__(self, parent=None):
        super(_F8StateContainer, self).__init__(parent)
        self.__layout = QtWidgets.QGridLayout()
        self.__layout.setColumnStretch(1, 1)
        self.__layout.setSpacing(4)
        self.__layout.setColumnMinimumWidth(0, 90)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        layout.setSpacing(_TAB_PANEL_SPACING)
        layout.setAlignment(QtCore.Qt.AlignTop)
        layout.addLayout(self.__layout)
        layout.addStretch(1)

        self.__property_widgets = {}

    def __repr__(self):
        return "<{} object at {}>".format(self.__class__.__name__, hex(id(self)))

    def add_widget(self, name, widget, value, label=None, tooltip=None):
        """
        Add a property widget to the window.

        Args:
            name (str): property name to be displayed.
            widget (BaseProperty): property widget.
            value (object): property value.
            label (str): custom label to display.
            tooltip (str): custom tooltip.
        """
        label = label or name
        label_widget = _F8StateContainer._ElideLabel(label, self)
        # Keep the label column bounded so value widgets (eg. sliders) remain usable
        # in narrow PropertiesBin panels.
        label_widget.setMaximumWidth(150)
        if tooltip:
            widget.setToolTip("{}\n{}".format(name, tooltip))
            label_widget.setToolTip("{}\n{}".format(name, tooltip))
        else:
            widget.setToolTip(name)
            label_widget.setToolTip(name)
        widget.set_value(value)
        row = len(self.__property_widgets)

        label_flags = QtCore.Qt.AlignCenter | QtCore.Qt.AlignRight
        if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)) or widget.__class__.__name__ == "PropTextEdit":
            label_flags = label_flags | QtCore.Qt.AlignTop

        self.__layout.addWidget(label_widget, row, 0, label_flags)
        self.__layout.addWidget(widget, row, 1)
        self.__property_widgets[name] = widget

    def get_widget(self, name):
        """
        Returns the property widget from the name.

        Args:
            name (str): property name.

        Returns:
            QtWidgets.QWidget: property widget.
        """
        return self.__property_widgets.get(name)

    def get_all_widgets(self):
        """
        Returns the node property widgets.

        Returns:
            dict: {name: widget}
        """
        return self.__property_widgets


class _F8StateStackContainer(QtWidgets.QWidget):
    """
    State tab container with vertical layout:
      row1: name + edit stateField button
      row2: editor widget (full width)

    This avoids squeezing value widgets into a narrow 2-column grid.
    """

    edit_state_field_requested = QtCore.Signal(str)
    delete_state_field_requested = QtCore.Signal(str)
    add_state_field_requested = QtCore.Signal()
    toggle_state_field_show_on_node_requested = QtCore.Signal(str, bool)
    state_field_order_changed = QtCore.Signal(list)

    class _ElideLabel(QtWidgets.QLabel):
        def __init__(self, text: str, parent: QtWidgets.QWidget | None = None):
            super().__init__("", parent)
            self._full_text = str(text or "")
            self.setText(self._full_text)

        def setText(self, text: str) -> None:  # type: ignore[override]
            self._full_text = str(text or "")
            self._update_elide()

        def resizeEvent(self, event):  # type: ignore[override]
            super().resizeEvent(event)
            self._update_elide()

        def _update_elide(self) -> None:
            try:
                fm = QtGui.QFontMetrics(self.font())
                elided = fm.elidedText(self._full_text, QtCore.Qt.ElideRight, max(10, int(self.width())))
                super().setText(elided)
            except Exception:
                super().setText(self._full_text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.__property_widgets: dict[str, QtWidgets.QWidget] = {}
        self._sec = _F8SpecListSection(self, title="State Fields")
        self._sec.add_clicked.connect(self.add_state_field_requested.emit)
        self._sec.rows_reordered.connect(lambda names: self.state_field_order_changed.emit(list(names)))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(QtCore.Qt.AlignTop)
        layout.addWidget(self._sec, 0, QtCore.Qt.AlignTop)
        layout.addStretch(1)

    def set_add_visible(self, visible: bool) -> None:
        self._sec.set_add_visible(bool(visible))

    def set_drag_enabled(self, enabled: bool) -> None:
        self._sec.set_drag_enabled(bool(enabled))

    def add_widget(
        self,
        name,
        widget,
        value,
        label=None,
        tooltip=None,
        *,
        allow_delete: bool = False,
        show_on_node: bool = True,
    ):
        label = label or name
        row = _F8StateFieldRow(
            self,
            name=str(name or ""),
            label=str(label or name or ""),
            widget=widget,
            value=value,
            tooltip=str(tooltip or ""),
            allow_delete=bool(allow_delete),
            show_on_node=bool(show_on_node),
        )
        row.edit_clicked.connect(self.edit_state_field_requested)
        row.delete_clicked.connect(self.delete_state_field_requested)
        row.show_on_node_changed.connect(self.toggle_state_field_show_on_node_requested)
        self._sec.add_row(row)
        self.__property_widgets[name] = widget

    def get_widget(self, name):
        return self.__property_widgets.get(name)

    def get_all_widgets(self):
        return self.__property_widgets


class _F8LabeledStackContainer(QtWidgets.QWidget):
    """
    Generic stacked property container:
      row1: label
      row2: editor widget

    Used for tabs like "Node" where we want the same vertical rhythm as
    State/Commands/Port without the extra state-field action buttons.
    """

    class _ElideLabel(QtWidgets.QLabel):
        def __init__(self, text: str, parent: QtWidgets.QWidget | None = None):
            super().__init__("", parent)
            self._full_text = str(text or "")
            self.setText(self._full_text)

        def setText(self, text: str) -> None:  # type: ignore[override]
            self._full_text = str(text or "")
            self._update_elide()

        def resizeEvent(self, event):  # type: ignore[override]
            super().resizeEvent(event)
            self._update_elide()

        def _update_elide(self) -> None:
            try:
                fm = QtGui.QFontMetrics(self.font())
                elided = fm.elidedText(self._full_text, QtCore.Qt.ElideRight, max(10, int(self.width())))
                super().setText(elided)
            except Exception:
                super().setText(self._full_text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.__property_widgets: dict[str, QtWidgets.QWidget] = {}

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        self._layout.setSpacing(_TAB_PANEL_SPACING)
        self._layout.setAlignment(QtCore.Qt.AlignTop)
        self._layout.addStretch(1)

    def add_widget(self, name, widget, value, label=None, tooltip=None):
        label = str(label or name or "")
        name_text = str(name or "")
        section = QtWidgets.QWidget(self)
        section.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label_widget = _F8LabeledStackContainer._ElideLabel(label, section)
        font = label_widget.font()
        font.setBold(True)
        label_widget.setFont(font)
        label_widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

        if tooltip:
            tip = f"{name_text}\n{tooltip}"
            label_widget.setToolTip(tip)
            widget.setToolTip(tip)
        else:
            label_widget.setToolTip(name_text)
            widget.setToolTip(name_text)

        widget.set_value(value)
        layout.addWidget(label_widget, 0, QtCore.Qt.AlignTop)
        layout.addWidget(widget, 0, QtCore.Qt.AlignTop)

        insert_at = max(0, self._layout.count() - 1)
        self._layout.insertWidget(insert_at, section, 0, QtCore.Qt.AlignTop)
        self.__property_widgets[name] = widget

    def get_widget(self, name):
        return self.__property_widgets.get(name)

    def get_all_widgets(self):
        return self.__property_widgets


def _icon_from_style(
    widget: QtWidgets.QWidget, style_icon: QtWidgets.QStyle.StandardPixmap, fallback: str
) -> QtGui.QIcon:
    icon = widget.style().standardIcon(style_icon)
    if not icon.isNull():
        return icon
    icon = QtGui.QIcon.fromTheme(fallback)
    if not icon.isNull():
        return icon
    return QtGui.QIcon()


def _set_icon(
    button: QtWidgets.QAbstractButton,
    *,
    token: StudioIcon,
) -> None:
    button.setIcon(icon_for(button, token))


class _F8DragHandle(QtWidgets.QToolButton):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._press_pos = QtCore.QPoint()
        self.setAutoRaise(True)
        self.setText("::")
        self.setToolTip("Drag to reorder")
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setStyleSheet("QToolButton { padding: 0; margin: 0; }")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        try:
            self._press_pos = event.pos()
        except Exception:
            self._press_pos = QtCore.QPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if not bool(event.buttons() & QtCore.Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        try:
            delta = (event.pos() - self._press_pos).manhattanLength()
        except Exception:
            delta = 0
        if delta < QtWidgets.QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        if isinstance(parent, _F8ReorderCard):
            parent.start_drag()
            return
        super().mouseMoveEvent(event)


class _F8ReorderCard(QtWidgets.QFrame):
    MIME_TYPE = "application/x-f8-spec-section-row"

    def __init__(self, parent: QtWidgets.QWidget | None, *, row: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._row = row
        self._token = uuid.uuid4().hex
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setProperty("_reorder_token", self._token)

        self._drag_handle = _F8DragHandle(self)
        self._drag_handle.setFixedWidth(18)
        self._drag_handle.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)

        row.setParent(self)
        row.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        layout.addWidget(self._drag_handle, 0)
        layout.addWidget(row, 1)

    def content_widget(self) -> QtWidgets.QWidget:
        return self._row

    def token(self) -> str:
        return self._token

    def order_key(self) -> str:
        return str(self._row.property("_order_key") or "").strip()

    def set_drag_enabled(self, enabled: bool) -> None:
        self._drag_handle.setVisible(bool(enabled))
        self._drag_handle.setEnabled(bool(enabled))

    def start_drag(self) -> None:
        host = self.parentWidget()
        if not isinstance(host, _F8ReorderList):
            return
        if not host.drag_enabled():
            return
        drag = QtGui.QDrag(self._drag_handle)
        mime = QtCore.QMimeData()
        mime.setData(self.MIME_TYPE, self._token.encode("utf-8"))
        drag.setMimeData(mime)
        try:
            drag.setPixmap(self.grab())
        except Exception:
            pass
        host.set_active_drag_token(self._token)
        try:
            self._drag_handle.setCursor(QtCore.Qt.ClosedHandCursor)
            drag.exec_(QtCore.Qt.MoveAction)
        finally:
            self._drag_handle.setCursor(QtCore.Qt.OpenHandCursor)
            host.set_active_drag_token("")


class _F8ReorderList(QtWidgets.QWidget):
    rows_reordered = QtCore.Signal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_enabled = True
        self._active_drag_token = ""
        self._cards_by_row: dict[QtWidgets.QWidget, _F8ReorderCard] = {}
        self._cards_by_token: dict[str, _F8ReorderCard] = {}
        self.setAcceptDrops(True)

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

    def drag_enabled(self) -> bool:
        return bool(self._drag_enabled)

    def set_drag_enabled(self, enabled: bool) -> None:
        self._drag_enabled = bool(enabled)
        self._refresh_drag_handles()

    def set_active_drag_token(self, token: str) -> None:
        self._active_drag_token = str(token or "").strip()

    def add_row(self, row: QtWidgets.QWidget) -> None:
        card = _F8ReorderCard(self, row=row)
        self._cards_by_row[row] = card
        self._cards_by_token[card.token()] = card
        self._layout.addWidget(card)
        self._refresh_drag_handles()

    def remove_row(self, row: QtWidgets.QWidget) -> None:
        card = self._cards_by_row.pop(row, None)
        if card is None:
            return
        self._cards_by_token.pop(card.token(), None)
        self._layout.removeWidget(card)
        try:
            card.setVisible(False)
        except (AttributeError, RuntimeError, TypeError):
            pass
        card.deleteLater()
        self._refresh_drag_handles()

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards_by_row.clear()
        self._cards_by_token.clear()

    def rows(self) -> list[QtWidgets.QWidget]:
        rows: list[QtWidgets.QWidget] = []
        for index in range(self._layout.count()):
            widget = self._layout.itemAt(index).widget()
            if not isinstance(widget, _F8ReorderCard):
                continue
            rows.append(widget.content_widget())
        return rows

    def order_keys(self) -> list[str]:
        return [key for key in [str(row.property("_order_key") or "").strip() for row in self.rows()] if key]

    def _refresh_drag_handles(self) -> None:
        cards = [card for card in self._cards_by_token.values()]
        show_handles = bool(self._drag_enabled and len(cards) > 1)
        for card in cards:
            card.set_drag_enabled(show_handles)

    @staticmethod
    def _event_y(event: QtGui.QDropEvent | QtGui.QDragMoveEvent) -> float:
        try:
            return float(event.position().y())  # type: ignore[attr-defined]
        except AttributeError:
            return float(event.pos().y())  # type: ignore[attr-defined]

    def _drop_index_for_y(self, y_pos: float) -> int:
        cards = self.rows()
        if not cards:
            return 0
        for index in range(self._layout.count()):
            widget = self._layout.itemAt(index).widget()
            if not isinstance(widget, _F8ReorderCard):
                continue
            midpoint = float(widget.geometry().top() + (widget.geometry().height() / 2.0))
            if y_pos < midpoint:
                return index
        return self._layout.count()

    def _card_for_event(self, event: QtGui.QDropEvent | QtGui.QDragMoveEvent | QtGui.QDragEnterEvent) -> _F8ReorderCard | None:
        mime = event.mimeData()
        if mime is None or not mime.hasFormat(_F8ReorderCard.MIME_TYPE):
            return None
        try:
            token = bytes(mime.data(_F8ReorderCard.MIME_TYPE)).decode("utf-8").strip()
        except Exception:
            token = ""
        if not token:
            token = self._active_drag_token
        return self._cards_by_token.get(token)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        if not self._drag_enabled or self._card_for_event(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        if not self._drag_enabled or self._card_for_event(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        if not self._drag_enabled:
            event.ignore()
            return
        card = self._card_for_event(event)
        if card is None:
            event.ignore()
            return
        cards_in_order = [widget for widget in [self._layout.itemAt(i).widget() for i in range(self._layout.count())] if isinstance(widget, _F8ReorderCard)]
        if card not in cards_in_order:
            event.ignore()
            return
        old_index = cards_in_order.index(card)
        new_index = self._drop_index_for_y(self._event_y(event))
        if new_index > old_index:
            new_index -= 1
        if new_index < 0:
            new_index = 0
        if new_index != old_index:
            self._layout.removeWidget(card)
            self._layout.insertWidget(new_index, card)
            self.rows_reordered.emit(self.order_keys())
        event.acceptProposedAction()


class _F8StateFieldRow(QtWidgets.QWidget):
    edit_clicked = QtCore.Signal(str)
    delete_clicked = QtCore.Signal(str)
    show_on_node_changed = QtCore.Signal(str, bool)

    def __init__(
        self,
        parent=None,
        *,
        name: str,
        label: str,
        widget: QtWidgets.QWidget,
        value: object,
        tooltip: str,
        allow_delete: bool,
        show_on_node: bool,
    ) -> None:
        super().__init__(parent)
        field_name = str(name or "").strip()
        self.setProperty("_order_key", field_name)

        header = QtWidgets.QWidget(self)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(3)

        label_widget = _F8StateStackContainer._ElideLabel(label, header)
        label_font = label_widget.font()
        label_font.setBold(True)
        label_widget.setFont(label_font)

        edit_btn = QtWidgets.QToolButton(header)
        edit_btn.setAutoRaise(True)
        edit_btn.setToolTip("Edit stateField...")
        edit_btn.setIcon(_icon_from_style(edit_btn, QtWidgets.QStyle.SP_FileDialogDetailedView, "document-edit"))
        edit_btn.clicked.connect(lambda _checked=False, _name=field_name: self.edit_clicked.emit(_name))

        eye_btn = QtWidgets.QToolButton(header)
        eye_btn.setAutoRaise(True)
        eye_btn.setCheckable(True)
        eye_btn.setToolTip("Show on node")
        with QtCore.QSignalBlocker(eye_btn):
            eye_btn.setChecked(bool(show_on_node))
        _set_icon(eye_btn, token=StudioIcon.EYE if bool(show_on_node) else StudioIcon.EYE_SLASH)
        eye_btn.toggled.connect(
            lambda checked, _btn=eye_btn, _name=field_name: self._emit_eye_changed(_btn, _name, bool(checked))
        )  # type: ignore[attr-defined]

        del_btn = QtWidgets.QToolButton(header)
        del_btn.setAutoRaise(True)
        del_btn.setToolTip("Delete stateField")
        del_btn.setIcon(_icon_from_style(del_btn, QtWidgets.QStyle.SP_TrashIcon, "edit-delete"))
        del_btn.setVisible(bool(allow_delete))
        del_btn.clicked.connect(lambda _checked=False, _name=field_name: self.delete_clicked.emit(_name))

        header_layout.addWidget(label_widget, 1)
        header_layout.addWidget(edit_btn, 0)
        header_layout.addWidget(eye_btn, 0)
        header_layout.addWidget(del_btn, 0)

        widget.set_value(value)
        if tooltip:
            full_tip = f"{field_name}\n{tooltip}"
            label_widget.setToolTip(full_tip)
            edit_btn.setToolTip("Edit stateField...\n" + full_tip)
            del_btn.setToolTip("Delete stateField\n" + full_tip)
            widget.setToolTip(full_tip)
        else:
            label_widget.setToolTip(field_name)
            widget.setToolTip(field_name)

        body = QtWidgets.QWidget(self)
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(widget)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(header)
        layout.addWidget(body)

    def _emit_eye_changed(self, button: QtWidgets.QAbstractButton, name: str, checked: bool) -> None:
        _set_icon(button, token=StudioIcon.EYE if bool(checked) else StudioIcon.EYE_SLASH)
        self.show_on_node_changed.emit(str(name or "").strip(), bool(checked))


class _F8SpecListSection(QtWidgets.QWidget):
    """
    Sidebar-friendly list group with a header and a "+" add button.
    """

    add_clicked = QtCore.Signal()
    rows_reordered = QtCore.Signal(list)

    def __init__(self, parent=None, *, title: str):
        super().__init__(parent)
        self._title = title

        header_label = QtWidgets.QLabel(title, self)
        f = header_label.font()
        f.setBold(True)
        header_label.setFont(f)

        self._add_btn = QtWidgets.QToolButton(self)
        self._add_btn.setAutoRaise(True)
        self._add_btn.setToolTip("Add")
        self._add_btn.setIcon(_icon_from_style(self._add_btn, QtWidgets.QStyle.SP_FileDialogNewFolder, "list-add"))
        self._add_btn.clicked.connect(self.add_clicked.emit)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(header_label)
        header.addStretch(1)
        header.addWidget(self._add_btn)

        self._list = _F8ReorderList(self)
        self._list.rows_reordered.connect(lambda names: self.rows_reordered.emit(list(names)))

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)
        outer.addLayout(header)
        outer.addWidget(self._list)

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)

    def set_add_visible(self, visible: bool) -> None:
        self._add_btn.setVisible(bool(visible))

    def set_drag_enabled(self, enabled: bool) -> None:
        self._list.set_drag_enabled(bool(enabled))

    def clear(self) -> None:
        self._list.clear()

    def add_row(self, row: QtWidgets.QWidget) -> None:
        self._list.add_row(row)

    def remove_row(self, row: QtWidgets.QWidget) -> None:
        self._list.remove_row(row)

    def rows(self) -> list[QtWidgets.QWidget]:
        return self._list.rows()

    def order_keys(self) -> list[str]:
        return self._list.order_keys()


class _F8SpecNameRow(QtWidgets.QWidget):
    edit_clicked = QtCore.Signal()
    delete_clicked = QtCore.Signal()
    name_committed = QtCore.Signal(str)
    show_on_node_changed = QtCore.Signal(bool)

    def __init__(self, parent=None, *, name: str, placeholder: str, show_eye: bool = False):
        super().__init__(parent)
        self.set_order_key(str(name or ""))

        self.name_edit = QtWidgets.QLineEdit(name, self)
        self.name_edit.setPlaceholderText(placeholder)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.textChanged.connect(self.set_order_key)  # type: ignore[attr-defined]
        self.name_edit.editingFinished.connect(self._emit_commit)

        self.edit_btn = QtWidgets.QToolButton(self)
        self.edit_btn.setAutoRaise(True)
        self.edit_btn.setToolTip("Edit")
        self.edit_btn.setIcon(
            _icon_from_style(self.edit_btn, QtWidgets.QStyle.SP_FileDialogDetailedView, "document-edit")
        )
        self.edit_btn.clicked.connect(self.edit_clicked.emit)

        self.eye_btn = QtWidgets.QToolButton(self)
        self.eye_btn.setAutoRaise(True)
        self.eye_btn.setCheckable(True)
        self.eye_btn.setToolTip("Show on node")
        self.eye_btn.toggled.connect(self._on_eye_toggled)  # type: ignore[attr-defined]
        self._update_eye_icon(True)

        self.del_btn = QtWidgets.QToolButton(self)
        self.del_btn.setAutoRaise(True)
        self.del_btn.setToolTip("Delete")
        self.del_btn.setIcon(_icon_from_style(self.del_btn, QtWidgets.QStyle.SP_TrashIcon, "edit-delete"))
        self.del_btn.clicked.connect(self.delete_clicked.emit)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.name_edit, 1)
        layout.addWidget(self.edit_btn)
        layout.addWidget(self.eye_btn)
        layout.addWidget(self.del_btn)
        self.eye_btn.setVisible(bool(show_eye))
        self.eye_btn.setEnabled(bool(show_eye))

    def set_order_key(self, name: str) -> None:
        self.setProperty("_order_key", str(name or "").strip())

    def set_row_editable(self, *, allow_rename: bool, allow_delete: bool, allow_edit: bool = True) -> None:
        self.name_edit.setReadOnly(not bool(allow_rename))
        self.del_btn.setVisible(bool(allow_delete))
        self.edit_btn.setVisible(bool(allow_edit))
        self.edit_btn.setEnabled(bool(allow_edit))

    def set_show_on_node(self, show: bool) -> None:
        with QtCore.QSignalBlocker(self.eye_btn):
            self.eye_btn.setChecked(bool(show))
        self._update_eye_icon(bool(show))

    def _update_eye_icon(self, show: bool) -> None:
        token = StudioIcon.EYE if bool(show) else StudioIcon.EYE_SLASH
        _set_icon(self.eye_btn, token=token)

    def _on_eye_toggled(self, checked: bool) -> None:
        self._update_eye_icon(bool(checked))
        self.show_on_node_changed.emit(bool(checked))

    def _emit_commit(self) -> None:
        self.name_committed.emit(str(self.name_edit.text() or "").strip())

