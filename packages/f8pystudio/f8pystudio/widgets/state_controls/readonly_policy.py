from __future__ import annotations

from qtpy import QtCore, QtWidgets

from ..state_value_controls import (
    F8BoolSwitchEditor,
    F8CodeButtonEditor,
    F8IncrementButtonEditor,
    F8MultiSelectEditor,
    F8OptionComboEditor,
)


def set_widget_read_only(widget: QtWidgets.QWidget, *, read_only: bool) -> None:
    if isinstance(widget, F8OptionComboEditor):
        widget.set_read_only(bool(read_only))
        return
    if isinstance(widget, F8MultiSelectEditor):
        widget.set_read_only(bool(read_only))
        return
    if isinstance(widget, F8BoolSwitchEditor):
        widget.set_read_only(bool(read_only))
        return
    if isinstance(widget, F8CodeButtonEditor):
        widget.set_read_only(bool(read_only))
        return
    if isinstance(widget, F8IncrementButtonEditor):
        widget.set_read_only(bool(read_only))
        return

    if isinstance(widget, QtWidgets.QLineEdit):
        widget.setEnabled(True)
        widget.setReadOnly(bool(read_only))
        return
    if isinstance(widget, QtWidgets.QPlainTextEdit):
        widget.setEnabled(True)
        widget.setReadOnly(bool(read_only))
        return
    if isinstance(widget, QtWidgets.QTextEdit):
        widget.setEnabled(True)
        widget.setReadOnly(bool(read_only))
        if read_only:
            widget.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        else:
            widget.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextEditorInteraction)
        return
    if isinstance(widget, QtWidgets.QAbstractSpinBox):
        widget.setEnabled(True)
        widget.setReadOnly(bool(read_only))
        if read_only:
            widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        else:
            widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        return

    widget.setEnabled(not bool(read_only))
