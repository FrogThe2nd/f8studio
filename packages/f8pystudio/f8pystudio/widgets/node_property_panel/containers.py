from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from ...ui_icons import StudioIcon, icon_for
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

        self._header = QtWidgets.QWidget(self)
        h = QtWidgets.QHBoxLayout(self._header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        title = QtWidgets.QLabel("State Fields", self._header)
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        self._btn_add = QtWidgets.QToolButton(self._header)
        self._btn_add.setAutoRaise(True)
        self._btn_add.setToolTip("Add state field")
        self._btn_add.setIcon(_icon_from_style(self._btn_add, QtWidgets.QStyle.SP_FileDialogNewFolder, "list-add"))
        self._btn_add.clicked.connect(self.add_state_field_requested.emit)
        h.addWidget(title, 1)
        h.addWidget(self._btn_add, 0)
        self._layout.addWidget(self._header)

    def set_add_visible(self, visible: bool) -> None:
        self._btn_add.setVisible(bool(visible))

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

        section = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(section)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        header = QtWidgets.QWidget(section)
        h = QtWidgets.QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        label_widget = _F8StateStackContainer._ElideLabel(label, header)
        f = label_widget.font()
        f.setBold(True)
        label_widget.setFont(f)

        edit_btn = QtWidgets.QToolButton(header)
        edit_btn.setAutoRaise(True)
        edit_btn.setToolTip("Edit stateField...")
        edit_btn.setIcon(_icon_from_style(edit_btn, QtWidgets.QStyle.SP_FileDialogDetailedView, "document-edit"))
        edit_btn.setProperty("_state_field_name", str(name or "").strip())
        edit_btn.clicked.connect(self._on_edit_clicked)

        del_btn = QtWidgets.QToolButton(header)
        del_btn.setAutoRaise(True)
        del_btn.setToolTip("Delete stateField")
        del_btn.setIcon(_icon_from_style(del_btn, QtWidgets.QStyle.SP_TrashIcon, "edit-delete"))
        del_btn.setVisible(bool(allow_delete))
        del_btn.setProperty("_state_field_name", str(name or "").strip())
        del_btn.clicked.connect(self._on_delete_clicked)

        eye_btn = QtWidgets.QToolButton(header)
        eye_btn.setAutoRaise(True)
        eye_btn.setCheckable(True)
        eye_btn.setChecked(bool(show_on_node))
        eye_btn.setToolTip("Show on node")
        token = StudioIcon.EYE if bool(show_on_node) else StudioIcon.EYE_SLASH
        _set_icon(eye_btn, token=token)
        eye_btn.setProperty("_state_field_name", str(name or "").strip())
        eye_btn.toggled.connect(self._on_eye_toggled)  # type: ignore[attr-defined]

        h.addWidget(label_widget, 1)
        h.addWidget(edit_btn, 0)
        h.addWidget(eye_btn, 0)
        h.addWidget(del_btn, 0)

        if tooltip:
            tip = "{}\n{}".format(name, tooltip)
            label_widget.setToolTip(tip)
            edit_btn.setToolTip("Edit stateField...\n" + tip)
            del_btn.setToolTip("Delete stateField\n" + tip)
            widget.setToolTip(tip)
        else:
            label_widget.setToolTip(str(name))
            widget.setToolTip(str(name))

        widget.set_value(value)
        v.addWidget(header)

        body = QtWidgets.QWidget(section)
        body_l = QtWidgets.QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(0)
        body_l.addWidget(widget)
        v.addWidget(body)

        self._layout.addWidget(section)
        self.__property_widgets[name] = widget

    def _on_edit_clicked(self, _checked: bool = False) -> None:
        btn = self.sender()
        name = str(btn.property("_state_field_name") or "").strip() if btn is not None else ""
        if name:
            self.edit_state_field_requested.emit(name)

    def _on_delete_clicked(self, _checked: bool = False) -> None:
        btn = self.sender()
        name = str(btn.property("_state_field_name") or "").strip() if btn is not None else ""
        if name:
            self.delete_state_field_requested.emit(name)

    def _on_eye_toggled(self, checked: bool) -> None:
        btn = self.sender()
        name = str(btn.property("_state_field_name") or "").strip() if btn is not None else ""
        if not name:
            return
        token = StudioIcon.EYE if bool(checked) else StudioIcon.EYE_SLASH
        if isinstance(btn, QtWidgets.QAbstractButton):
            _set_icon(btn, token=token)
        self.toggle_state_field_show_on_node_requested.emit(name, bool(checked))

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


class _F8SpecListSection(QtWidgets.QWidget):
    """
    Sidebar-friendly list group with a header and a "+" add button.
    """

    add_clicked = QtCore.Signal()

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
        header.addWidget(header_label)
        header.addStretch(1)
        header.addWidget(self._add_btn)

        self._list_layout = QtWidgets.QVBoxLayout()
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(_TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN, _TAB_PANEL_MARGIN)
        outer.setSpacing(4)
        outer.addLayout(header)
        outer.addLayout(self._list_layout)

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)

    def set_add_visible(self, visible: bool) -> None:
        self._add_btn.setVisible(bool(visible))

    def clear(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def add_row(self, row: QtWidgets.QWidget) -> None:
        self._list_layout.addWidget(row)

    def remove_row(self, row: QtWidgets.QWidget) -> None:
        self._list_layout.removeWidget(row)
        try:
            row.setVisible(False)
        except (AttributeError, RuntimeError, TypeError):
            pass
        row.deleteLater()

    def rows(self) -> list[QtWidgets.QWidget]:
        out: list[QtWidgets.QWidget] = []
        for i in range(self._list_layout.count()):
            w = self._list_layout.itemAt(i).widget()
            if w is not None:
                out.append(w)
        return out


class _F8SpecNameRow(QtWidgets.QWidget):
    edit_clicked = QtCore.Signal()
    delete_clicked = QtCore.Signal()
    name_committed = QtCore.Signal(str)
    show_on_node_changed = QtCore.Signal(bool)

    def __init__(self, parent=None, *, name: str, placeholder: str, show_eye: bool = False):
        super().__init__(parent)

        self.name_edit = QtWidgets.QLineEdit(name, self)
        self.name_edit.setPlaceholderText(placeholder)
        self.name_edit.setClearButtonEnabled(True)
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

