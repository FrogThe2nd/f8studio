from __future__ import annotations

from qtpy import QtWidgets

from ...components.state_builders import set_control_read_only

def set_widget_read_only(widget: QtWidgets.QWidget, *, read_only: bool) -> None:
    set_control_read_only(widget, read_only=read_only)
