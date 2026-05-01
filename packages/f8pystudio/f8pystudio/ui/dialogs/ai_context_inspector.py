from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from ..support.studio_theme import inline_action_button_qss, label_qss, qss_rgba, studio_dark_theme


class AiContextInspectorDialog(QtWidgets.QDialog):
    """
    Dialog to inspect the raw context payload sent to the LLM.
    """
    def __init__(self, report_text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Context Inspector")
        self.resize(1000, 800)
        self._report_text = report_text
        p = studio_dark_theme().palette

        self.setStyleSheet(f"background-color: {p.window_bg};")
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Heading
        header = QtWidgets.QLabel("Current Context Payload")
        header.setStyleSheet(label_qss(color=p.text_primary, font_size_px=18, bold=True))
        layout.addWidget(header)
        
        # Text Area (Rendered Markdown)
        self.text_viewer = QtWidgets.QTextBrowser()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setOpenExternalLinks(True)
        
        self.text_viewer.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {p.field_bg};
                border: 1px solid {p.border};
                border-radius: 6px;
                padding: 12px;
                color: {p.text_primary};
                font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                font-size: 14px;
                line-height: 1.5;
            }}
        """)

        # Additional CSS for Markdown elements via default style sheet
        markdown_css = f"""
            h1, h2, h3, h4, h5, h6 {{ color: {p.accent}; font-weight: bold; margin-top: 24px; margin-bottom: 16px; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
            h1 {{ font-size: 2em; border-bottom: 1px solid {p.border_subtle}; padding-bottom: 0.3em; }}
            h2 {{ font-size: 1.5em; border-bottom: 1px solid {p.border_subtle}; padding-bottom: 0.3em; }}
            code {{ background-color: {qss_rgba(p.text_muted, 90)}; border-radius: 6px; padding: 0.2em 0.4em; font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 85%; }}
            pre {{ background-color: {p.panel_bg}; border-radius: 6px; padding: 16px; overflow: auto; font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 85%; line-height: 1.45; }}
            pre code {{ background-color: transparent; padding: 0; border-radius: 0; }}
            blockquote {{ border-left: 0.25em solid {p.border}; color: {p.text_muted}; padding: 0 1em; margin: 0; }}
            a {{ color: {p.accent}; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
            table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
            table th, table td {{ border: 1px solid {p.border}; padding: 6px 13px; }}
            table tr {{ background-color: {p.field_bg}; border-top: 1px solid {p.border_subtle}; }}
            table tr:nth-child(2n) {{ background-color: {p.panel_bg}; }}
            ul, ol {{ padding-left: 2em; }}
            li {{ margin: 0.25em 0; }}
            hr {{ height: 0.25em; padding: 0; margin: 24px 0; background-color: {p.border}; border: 0; }}
        """
        self.text_viewer.document().setDefaultStyleSheet(markdown_css)
        self.text_viewer.setMarkdown(report_text)
        
        layout.addWidget(self.text_viewer)
        
        # Action Bar
        action_layout = QtWidgets.QHBoxLayout()
        
        copy_btn = QtWidgets.QPushButton("Copy Raw Markdown")
        copy_btn.setMinimumHeight(32)
        copy_btn.setStyleSheet(inline_action_button_qss(accent_color=p.accent))
        copy_btn.clicked.connect(self._on_copy_clicked)
        
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setMinimumHeight(32)
        close_btn.setStyleSheet(inline_action_button_qss(accent_color=p.success))
        close_btn.clicked.connect(self.accept)
        
        action_layout.addWidget(copy_btn)
        action_layout.addStretch()
        action_layout.addWidget(close_btn)
        
        layout.addLayout(action_layout)

    def _on_copy_clicked(self) -> None:
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self._report_text)
