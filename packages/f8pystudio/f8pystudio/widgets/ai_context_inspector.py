from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

class AiContextInspectorDialog(QtWidgets.QDialog):
    """
    Dialog to inspect the raw context payload sent to the LLM.
    """
    def __init__(self, report_text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Context Inspector")
        self.resize(800, 600)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Heading
        header = QtWidgets.QLabel("<b>Current Context Payload (Markdown)</b>")
        layout.addWidget(header)
        
        # Text Area
        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(report_text)
        
        # Monospace font for the content
        font = QtGui.QFont("Consolas")
        if not font.fixedPitch():
            font = QtGui.QFont("Monospace")
        font.setPointSize(10)
        self.text_edit.setFont(font)
        
        layout.addWidget(self.text_edit)
        
        # Buttons
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        copy_btn = buttons.addButton("Copy to Clipboard", QtWidgets.QDialogButtonBox.ActionRole)
        copy_btn.clicked.connect(self._on_copy_clicked)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_copy_clicked(self) -> None:
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self.text_edit.toPlainText())
