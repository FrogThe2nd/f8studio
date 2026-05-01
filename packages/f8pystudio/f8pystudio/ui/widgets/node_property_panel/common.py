from __future__ import annotations

import json
from typing import Any

from qtpy import QtCore, QtWidgets

from ...support.json_text_editor import attach_json_enhancements
from ...support.node_property_support import to_jsonable
from ...support.state_panel_controls import set_widget_read_only as _set_widget_read_only
from ...support.studio_theme import node_property_tabs_qss, transparent_background_qss

_PROPERTY_PANEL_MIN_WIDTH = 250
_TAB_PANEL_MARGIN = 4
_TAB_PANEL_SPACING = 5
_TAB_HEADER_STYLE = node_property_tabs_qss()


def _apply_read_only_widget(widget: QtWidgets.QWidget) -> None:
    _set_widget_read_only(widget, read_only=True)


def _set_read_only_widget(widget: QtWidgets.QWidget, *, read_only: bool) -> None:
    _set_widget_read_only(widget, read_only=bool(read_only))


def _wrap_tab_page(content: QtWidgets.QWidget) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget(content.parentWidget())
    layout = QtWidgets.QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    scroll = QtWidgets.QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
    try:
        scroll.viewport().setAutoFillBackground(False)
    except (AttributeError, RuntimeError, TypeError):
        pass
    content.setObjectName("f8TabPageContent")
    content.setStyleSheet(transparent_background_qss())
    scroll.setWidget(content)
    layout.addWidget(scroll)
    return page


class _F8JsonEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str, value: Any):
        super().__init__(parent)
        self.setWindowTitle(title)

        self._edit = QtWidgets.QPlainTextEdit()
        attach_json_enhancements(self._edit, read_only=False)
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)
        self._edit.setPlainText(text)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._edit, 1)
        layout.addWidget(buttons)

    def value(self) -> Any:
        text = self._edit.toPlainText().strip()
        if not text:
            return None
        return json.loads(text)
