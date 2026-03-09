from __future__ import annotations

from qtpy import QtCore, QtWidgets
from NodeGraphQt.nodes.base_node import NodeBaseWidget

from ..nodegraph.items.embedded_resize_contract import clamp_content_size
from ..nodegraph.note_nodeitem import F8StudioNoteNodeItem
from ..nodegraph.operator_basenode import F8StudioOperatorBaseNode

_WIDGET_NAME = "__note_markdown"
_STATE_CONTENT = "content"
_MIN_CONTENT_SIZE = (220, 150)


class _NoteTextEdit(QtWidgets.QTextEdit):
    """
    Always consume wheel events inside the note editor so graph zoom does not
    trigger while the pointer is over text editing area.
    """

    def wheelEvent(self, event):  # type: ignore[override]
        super().wheelEvent(event)
        event.accept()


class _NoteWidget(NodeBaseWidget):
    def __init__(self, parent=None, name: str = _WIDGET_NAME, label: str = "") -> None:
        super().__init__(parent=parent, name=name, label=label)
        self._raw_text = ""
        self._editor = _NoteTextEdit()
        self._editor.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("Write markdown notes...")
        self._editor.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self._editor.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._editor.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._editor.setMinimumSize(0, 0)
        self._editor.setMaximumSize(16_777_215, 16_777_215)

        self._preview_switch = QtWidgets.QCheckBox("Preview")
        self._preview_switch.setChecked(True)
        self._preview_switch.toggled.connect(self._on_preview_toggled)

        self._pane = QtWidgets.QWidget()
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addStretch()
        top.addWidget(self._preview_switch)

        pane_layout = QtWidgets.QVBoxLayout(self._pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(4)
        pane_layout.addLayout(top)
        pane_layout.addWidget(self._editor, 1)

        self._pane.setMinimumSize(0, 0)
        self._pane.setMaximumSize(16_777_215, 16_777_215)
        self.set_custom_widget(self._pane)
        group = self.widget()
        if group is not None:
            group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            group.setMinimumSize(0, 0)
            group.setMaximumSize(16_777_215, 16_777_215)
        self.apply_content_rect(*_MIN_CONTENT_SIZE)
        self._apply_dark_style()
        self._apply_preview_mode()

    def editor(self) -> QtWidgets.QTextEdit:
        return self._editor

    def raw_text(self) -> str:
        return self._raw_text

    def update_raw_text(self, value: str) -> None:
        self._raw_text = str(value or "")

    def preview_enabled(self) -> bool:
        return bool(self._preview_switch.isChecked())

    def set_preview_enabled(self, enabled: bool) -> None:
        if self._preview_switch.isChecked() == bool(enabled):
            return
        self._preview_switch.setChecked(bool(enabled))

    def minimum_content_size(self) -> tuple[int, int]:
        return _MIN_CONTENT_SIZE

    def apply_content_rect(self, width: int, height: int) -> None:
        target_w, target_h = clamp_content_size(
            width=float(width),
            height=float(height),
            minimum=self.minimum_content_size(),
        )
        group = self.widget()
        inner_w = target_w
        inner_h = target_h
        if group is not None:
            group.setFixedSize(target_w, target_h)
            layout = group.layout()
            if layout is not None:
                margins = layout.contentsMargins()
                inner_w = max(1, target_w - int(margins.left()) - int(margins.right()))
                inner_h = max(1, target_h - int(margins.top()) - int(margins.bottom()))
        self._pane.setFixedSize(inner_w, inner_h)
        self._editor.updateGeometry()
        self._pane.updateGeometry()

    def _apply_dark_style(self) -> None:
        self._pane.setStyleSheet(
            """
            QCheckBox {
                color: rgb(225, 225, 225);
            }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
                border: 1px solid rgba(255, 255, 255, 90);
                background: rgba(0, 0, 0, 35);
                border-radius: 2px;
            }
            QCheckBox::indicator:checked { background: rgba(120, 200, 255, 90); }
            QTextEdit {
                color: rgb(228, 228, 228);
                background: rgba(0, 0, 0, 38);
                border: 1px solid rgba(255, 255, 255, 36);
                border-radius: 4px;
                padding: 6px;
            }
            """
        )

    def _apply_preview_mode(self) -> None:
        blocker = QtCore.QSignalBlocker(self._editor)
        self._editor.setReadOnly(True)
        self._editor.setMarkdown(self._raw_text)
        del blocker

    def _apply_edit_mode(self) -> None:
        blocker = QtCore.QSignalBlocker(self._editor)
        self._editor.setReadOnly(False)
        self._editor.setPlainText(self._raw_text)
        del blocker

    def _on_preview_toggled(self, enabled: bool) -> None:
        if enabled:
            self._raw_text = self._editor.toPlainText()
            self._apply_preview_mode()
            return
        self._apply_edit_mode()

    def get_value(self) -> object:
        return self._raw_text

    def set_value(self, value: object) -> None:
        self._raw_text = str(value) if value is not None else ""
        if self.preview_enabled():
            self._apply_preview_mode()
            return
        if self._editor.isReadOnly() or self._editor.toPlainText() != self._raw_text:
            blocker = QtCore.QSignalBlocker(self._editor)
            self._editor.setReadOnly(False)
            self._editor.setPlainText(self._raw_text)
            del blocker


class NoteRenderNode(F8StudioOperatorBaseNode):
    """Markdown note renderer for `f8.note`."""

    def __init__(self):
        super().__init__(qgraphics_item=F8StudioNoteNodeItem)
        self.add_ephemeral_widget(_NoteWidget(self.view, name=_WIDGET_NAME, label=""))
        editor = self._editor_widget()
        if editor is not None:
            editor.textChanged.connect(self._on_text_changed)
        self._sync_editor_from_state()

    def sync_from_spec(self) -> None:
        super().sync_from_spec()
        self._sync_editor_from_state()

    def set_property(self, name, value, push_undo=True):  # type: ignore[override]
        super().set_property(name, value, push_undo=push_undo)
        if str(name or "").strip() == _STATE_CONTENT:
            self._sync_editor_from_state()

    def _widget(self) -> _NoteWidget | None:
        return self.widget_by_name(_WIDGET_NAME, _NoteWidget)

    def _editor_widget(self) -> QtWidgets.QTextEdit | None:
        widget = self._widget()
        if widget is None:
            return None
        return widget.editor()

    def _sync_editor_from_state(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        value = self.get_property(_STATE_CONTENT)
        text = str(value) if value is not None else ""
        if widget.raw_text() == text:
            return
        widget.set_value(text)

    def _on_text_changed(self) -> None:
        widget = self._widget()
        editor = self._editor_widget()
        if widget is None or editor is None:
            return
        if editor.isReadOnly():
            return
        widget.update_raw_text(editor.toPlainText())
        self.set_property(_STATE_CONTENT, widget.raw_text(), push_undo=False)
