from __future__ import annotations

import enum
from dataclasses import dataclass

from qtpy import QtGui, QtWidgets


class StudioColorRole(enum.Enum):
    WINDOW_BG = "window_bg"
    PANEL_BG = "panel_bg"
    PANEL_ALT_BG = "panel_alt_bg"
    FIELD_BG = "field_bg"
    TEXT_PRIMARY = "text_primary"
    TEXT_SECONDARY = "text_secondary"
    TEXT_MUTED = "text_muted"
    BORDER = "border"
    ACCENT = "accent"
    ERROR = "error"
    WARNING = "warning"
    SUCCESS = "success"


@dataclass(frozen=True)
class StudioPalette:
    window_bg: str
    panel_bg: str
    panel_alt_bg: str
    panel_raised_bg: str
    field_bg: str
    field_alt_bg: str
    button_bg: str
    button_hover_bg: str
    button_pressed_bg: str
    text_primary: str
    text_secondary: str
    text_muted: str
    text_disabled: str
    border_subtle: str
    border: str
    border_focus: str
    selection_bg: str
    selection_text: str
    tooltip_bg: str
    tooltip_text: str
    log_bg: str
    accent: str
    accent_hover: str
    info: str
    success: str
    warning: str
    error: str
    purple: str


@dataclass(frozen=True)
class StudioTheme:
    name: str
    palette: StudioPalette


_STUDIO_DARK_THEME = StudioTheme(
    name="F8StudioDark",
    palette=StudioPalette(
        window_bg="#161A20",
        panel_bg="#1D232B",
        panel_alt_bg="#242B35",
        panel_raised_bg="#28313D",
        field_bg="#101419",
        field_alt_bg="#1A2028",
        button_bg="#252D37",
        button_hover_bg="#303A47",
        button_pressed_bg="#394655",
        text_primary="#E6EDF3",
        text_secondary="#B8C2CC",
        text_muted="#8A96A3",
        text_disabled="#606B76",
        border_subtle="#2A333F",
        border="#3A4552",
        border_focus="#57A6FF",
        selection_bg="#2F6EA8",
        selection_text="#F8FBFF",
        tooltip_bg="#111820",
        tooltip_text="#EAF2FA",
        log_bg="#0F1318",
        accent="#57A6FF",
        accent_hover="#78BCFF",
        info="#57C7FF",
        success="#72D18B",
        warning="#F1C75B",
        error="#FF6B6B",
        purple="#C099FF",
    ),
)


def studio_dark_theme() -> StudioTheme:
    return _STUDIO_DARK_THEME


def qss_rgba(color: str, alpha: int) -> str:
    qcolor = QtGui.QColor(str(color or ""))
    if not qcolor.isValid():
        raise ValueError(f"Invalid theme color: {color!r}")
    alpha_value = max(0, min(255, int(alpha)))
    return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha_value})"


def _set_palette_color(
    palette: QtGui.QPalette,
    group: QtGui.QPalette.ColorGroup,
    role: QtGui.QPalette.ColorRole,
    color: str,
) -> None:
    palette.setColor(group, role, QtGui.QColor(color))


def palette_for_theme(theme: StudioTheme) -> QtGui.QPalette:
    p = theme.palette
    palette = QtGui.QPalette()

    for group in (QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorGroup.Inactive):
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Window, p.window_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.WindowText, p.text_primary)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Base, p.field_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.AlternateBase, p.panel_alt_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.ToolTipBase, p.tooltip_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.ToolTipText, p.tooltip_text)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Text, p.text_primary)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Button, p.button_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.ButtonText, p.text_primary)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.BrightText, p.selection_text)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Highlight, p.selection_bg)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.HighlightedText, p.selection_text)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.Link, p.accent)
        _set_palette_color(palette, group, QtGui.QPalette.ColorRole.PlaceholderText, p.text_muted)

    disabled = QtGui.QPalette.ColorGroup.Disabled
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.Window, p.window_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.WindowText, p.text_disabled)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.Base, p.field_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.AlternateBase, p.panel_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.ToolTipBase, p.tooltip_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.ToolTipText, p.tooltip_text)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.Text, p.text_disabled)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.Button, p.button_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.ButtonText, p.text_disabled)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.Highlight, p.panel_raised_bg)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.HighlightedText, p.text_disabled)
    _set_palette_color(palette, disabled, QtGui.QPalette.ColorRole.PlaceholderText, p.text_disabled)

    return palette


def qss_for_theme(theme: StudioTheme) -> str:
    p = theme.palette
    subtle_hover = qss_rgba(p.accent, 36)
    subtle_press = qss_rgba(p.accent, 58)
    disabled_bg = qss_rgba(p.field_bg, 170)
    return f"""
    QWidget {{
        color: {p.text_primary};
        selection-background-color: {p.selection_bg};
        selection-color: {p.selection_text};
    }}
    QMainWindow, QDialog {{
        background: {p.window_bg};
    }}
    QToolTip {{
        color: {p.tooltip_text};
        background-color: {p.tooltip_bg};
        border: 1px solid {p.border};
        padding: 4px 6px;
    }}
    QMenuBar {{
        color: {p.text_primary};
        background: {p.panel_bg};
        border-bottom: 1px solid {p.border_subtle};
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 4px 9px;
    }}
    QMenuBar::item:selected {{
        background: {p.button_hover_bg};
    }}
    QMenu {{
        color: {p.text_primary};
        background: {p.panel_bg};
        border: 1px solid {p.border};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 4px 22px 4px 22px;
        border-radius: 3px;
    }}
    QMenu::item:selected {{
        background: {p.selection_bg};
        color: {p.selection_text};
    }}
    QDockWidget {{
        color: {p.text_primary};
        background: {p.panel_bg};
        titlebar-close-icon: none;
        titlebar-normal-icon: none;
    }}
    QDockWidget::title {{
        background: {p.panel_alt_bg};
        border-bottom: 1px solid {p.border_subtle};
        padding: 5px 8px;
        text-align: left;
    }}
    QToolBar {{
        background: {p.panel_bg};
        border: 0;
        border-bottom: 1px solid {p.border_subtle};
        spacing: 3px;
        padding: 3px;
    }}
    QToolBar::separator {{
        background: {p.border};
        width: 1px;
        margin: 4px 5px;
    }}
    QToolButton {{
        color: {p.text_primary};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 4px;
    }}
    QToolButton:hover:enabled {{
        background: {p.button_hover_bg};
        border-color: {p.border};
    }}
    QToolButton:pressed:enabled, QToolButton:checked {{
        background: {p.button_pressed_bg};
        border-color: {p.border_focus};
    }}
    QToolButton:disabled {{
        color: {p.text_disabled};
    }}
    QPushButton {{
        color: {p.text_primary};
        background: {p.button_bg};
        border: 1px solid {p.border};
        border-radius: 5px;
        padding: 5px 10px;
    }}
    QPushButton:hover:enabled {{
        background: {p.button_hover_bg};
        border-color: {p.border_focus};
    }}
    QPushButton:pressed:enabled {{
        background: {p.button_pressed_bg};
    }}
    QPushButton:disabled {{
        color: {p.text_disabled};
        background: {disabled_bg};
        border-color: {p.border_subtle};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        color: {p.text_primary};
        background: {p.field_bg};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 3px 5px;
        selection-background-color: {p.selection_bg};
        selection-color: {p.selection_text};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {p.border_focus};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        color: {p.text_disabled};
        background: {disabled_bg};
    }}
    QComboBox::drop-down {{
        border: 0;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        color: {p.text_primary};
        background: {p.panel_bg};
        border: 1px solid {p.border};
        selection-background-color: {p.selection_bg};
        selection-color: {p.selection_text};
        outline: 0;
    }}
    QTreeView, QListView, QTableView {{
        color: {p.text_primary};
        background: {p.panel_bg};
        alternate-background-color: {p.panel_alt_bg};
        border: 1px solid {p.border_subtle};
        selection-background-color: {p.selection_bg};
        selection-color: {p.selection_text};
        outline: 0;
    }}
    QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{
        background: {subtle_hover};
    }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
        background: {p.selection_bg};
        color: {p.selection_text};
    }}
    QHeaderView::section {{
        color: {p.text_secondary};
        background: {p.panel_alt_bg};
        border: 0;
        border-right: 1px solid {p.border_subtle};
        border-bottom: 1px solid {p.border_subtle};
        padding: 4px 6px;
    }}
    QTabWidget::pane {{
        border: 1px solid {p.border_subtle};
        background: {p.panel_bg};
    }}
    QTabBar::tab {{
        color: {p.text_secondary};
        background: {p.panel_alt_bg};
        border: 1px solid {p.border_subtle};
        border-bottom-color: {p.border_subtle};
        padding: 5px 9px;
    }}
    QTabBar::tab:hover {{
        color: {p.text_primary};
        background: {p.button_hover_bg};
    }}
    QTabBar::tab:selected {{
        color: {p.text_primary};
        background: {p.panel_raised_bg};
        border-color: {p.border};
        border-bottom-color: {p.panel_raised_bg};
    }}
    QCheckBox, QRadioButton {{
        color: {p.text_primary};
        spacing: 6px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 13px;
        height: 13px;
        border: 1px solid {p.border};
        background: {p.field_bg};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent_hover};
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: {p.window_bg};
        border: 0;
        margin: 0;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {p.panel_raised_bg};
        border-radius: 4px;
        min-height: 24px;
        min-width: 24px;
    }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
        background: {p.button_hover_bg};
    }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent;
        border: 0;
    }}
    QSplitter::handle {{
        background: {p.border_subtle};
    }}
    QStatusBar {{
        color: {p.text_secondary};
        background: {p.panel_bg};
        border-top: 1px solid {p.border_subtle};
    }}
    QFrame[frameShape="4"], QFrame[frameShape="5"] {{
        color: {p.border_subtle};
    }}
    QToolButton[_f8FlatIcon="true"] {{
        border: none;
        background: transparent;
    }}
    QToolButton[_f8FlatIcon="true"]:hover:enabled {{
        background: {p.button_hover_bg};
    }}
    QToolButton[_f8FlatIcon="true"]:pressed:enabled, QToolButton[_f8FlatIcon="true"]:checked {{
        background: {p.button_pressed_bg};
    }}
    QToolButton[_f8InlineAction="true"]:hover:enabled {{
        background: {subtle_hover};
        border-color: {p.border_focus};
    }}
    QToolButton[_f8InlineAction="true"]:pressed:enabled {{
        background: {subtle_press};
    }}
    """


def apply_studio_theme(app: QtWidgets.QApplication, theme: StudioTheme) -> None:
    app.setStyle("Fusion")
    app.setPalette(palette_for_theme(theme))
    app.setStyleSheet(qss_for_theme(theme))


def transparent_widget_qss() -> str:
    return "QWidget { background: transparent; border: 0px; }"


def transparent_background_qss() -> str:
    return "background: transparent;"


def transparent_header_qss() -> str:
    return transparent_background_qss()


def node_property_tabs_qss() -> str:
    p = studio_dark_theme().palette
    return f"""
    QTabWidget#f8NodePropTabs::pane {{
        border: 1px solid {qss_rgba(p.border, 120)};
        border-radius: 6px;
        background: {p.panel_bg};
        top: -1px;
    }}
    QTabWidget#f8NodePropTabs QTabBar {{
        qproperty-drawBase: 0;
    }}
    QTabWidget#f8NodePropTabs QTabBar::tab {{
        color: {p.text_secondary};
        background: {p.panel_alt_bg};
        border: 1px solid {p.border_subtle};
        border-bottom-color: {qss_rgba(p.border_subtle, 120)};
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
        padding: 4px 7px;
        margin-right: 1px;
        margin-top: 1px;
        min-width: 0px;
    }}
    QTabWidget#f8NodePropTabs QTabBar::tab:hover {{
        color: {p.text_primary};
        background: {p.button_hover_bg};
        border-color: {p.border};
    }}
    QTabWidget#f8NodePropTabs QTabBar::tab:selected {{
        color: {p.text_primary};
        background: {p.panel_raised_bg};
        border-color: {p.border};
        border-bottom-color: {p.panel_raised_bg};
        margin-top: 0px;
        padding-top: 5px;
        padding-bottom: 5px;
    }}
    QTabWidget#f8NodePropTabs QTabBar::tab:!selected {{
        margin-top: 1px;
    }}
    """


def inline_header_button_qss() -> str:
    p = studio_dark_theme().palette
    return f"""
    QToolButton {{
        color: {p.text_primary};
        background: transparent;
        border: 1px solid {qss_rgba(p.border, 80)};
        border-radius: 4px;
        padding: 2px 8px;
        text-align: left;
    }}
    QToolButton:hover {{ background: transparent; }}
    QToolButton:checked {{ background: transparent; }}
    """


def inline_control_qss() -> str:
    p = studio_dark_theme().palette
    return f"""
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        color: {p.text_primary};
        background: {qss_rgba(p.log_bg, 150)};
        border: 1px solid {qss_rgba(p.text_primary, 62)};
        border-radius: 3px;
        padding: 1px 4px;
    }}
    QPlainTextEdit, QTextEdit {{
        selection-background-color: {p.selection_bg};
    }}
    QComboBox::drop-down {{ border: 0px; }}
    QComboBox QAbstractItemView {{
        color: {p.text_primary};
        background: {p.panel_bg};
        selection-background-color: {p.selection_bg};
    }}
    QCheckBox {{ color: {p.text_primary}; }}
    QCheckBox::indicator {{
        width: 13px;
        height: 13px;
        border: 1px solid {qss_rgba(p.text_primary, 95)};
        background: {qss_rgba(p.log_bg, 130)};
        border-radius: 2px;
    }}
    QCheckBox::indicator:checked {{ background: {qss_rgba(p.accent, 110)}; }}
    """


def inline_action_button_qss(*, accent_color: str) -> str:
    p = studio_dark_theme().palette
    return f"""
    QPushButton, QToolButton {{
        color: {p.text_primary};
        background: {qss_rgba(p.log_bg, 140)};
        border: 1px solid {qss_rgba(accent_color, 95)};
        border-radius: 6px;
        padding: 6px 10px;
        text-align: center;
        font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {qss_rgba(accent_color, 30)};
        border-color: {qss_rgba(accent_color, 150)};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {qss_rgba(accent_color, 46)};
        border-color: {qss_rgba(accent_color, 180)};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {qss_rgba(p.text_primary, 110)};
        background: {qss_rgba(p.log_bg, 85)};
        border-color: {qss_rgba(p.text_primary, 32)};
    }}
    """


def inline_command_button_qss() -> str:
    return inline_action_button_qss(accent_color=studio_dark_theme().palette.accent)


def service_process_toolbar_qss() -> str:
    p = studio_dark_theme().palette
    return f"""
    ServiceProcessToolbar {{
      background: {qss_rgba(p.panel_alt_bg, 215)};
      border: 1px solid {qss_rgba(p.text_primary, 34)};
      border-radius: 6px;
      padding: 1px;
    }}
    ServiceProcessToolbar QToolButton {{
      background: transparent;
      border: 0px;
      padding: 2px;
    }}
    ServiceProcessToolbar QToolButton:hover {{
      background: {qss_rgba(p.text_primary, 28)};
      border-radius: 4px;
    }}
    """


def performance_overlay_qss() -> str:
    p = studio_dark_theme().palette
    return f"""
    QLabel#f8PerfOverlay {{
        color: {p.text_primary};
        background: {qss_rgba(p.log_bg, 220)};
        border: 1px solid {qss_rgba(p.accent, 105)};
        border-radius: 8px;
        font-family: monospace;
        font-size: 11px;
    }}
    """


def ai_context_button_qss(*, text_color: str, include_background: bool) -> str:
    p = studio_dark_theme().palette
    background = "background: transparent;" if include_background else ""
    return (
        f"QToolButton {{ color: {text_color}; border: none; padding: 0 4px; {background} font-size: 10pt; }}"
        f"QToolButton:hover {{ color: {p.text_primary}; }}"
    )


def ai_status_label_qss(*, text_color: str) -> str:
    p = studio_dark_theme().palette
    return (
        f"QLabel {{ color: {text_color}; font-size: 9pt; background: {p.field_alt_bg}; "
        f"border: 1px solid {p.border_subtle}; border-radius: 5px; padding: 1px 6px; }}"
    )


def icon_tool_button_qss(*, accent_color: str) -> str:
    p = studio_dark_theme().palette
    return (
        "QToolButton {"
        f" color: {accent_color};"
        " border: none;"
        " border-radius: 6px;"
        " padding: 0;"
        " background: transparent;"
        "}"
        f"QToolButton:hover:enabled {{ background: {p.button_hover_bg}; }}"
        f"QToolButton:pressed:enabled {{ background: {p.button_pressed_bg}; }}"
        f"QToolButton:checked {{ background: {p.button_hover_bg}; }}"
        f"QToolButton:disabled {{ color: {p.text_disabled}; }}"
    )


def ai_quick_panel_qss() -> str:
    p = studio_dark_theme().palette
    return (
        "#aiQuickPanel { "
        f"  background: {p.panel_bg}; "
        f"  border: 1px solid {p.border}; "
        "  border-radius: 6px; "
        "}"
    )


def label_qss(*, color: str, font_size_px: int | None = None, bold: bool = False, margin_top_px: int = 0) -> str:
    font_size = "" if font_size_px is None else f" font-size: {int(font_size_px)}px;"
    font_weight = " font-weight: bold;" if bold else ""
    margin_top = "" if margin_top_px <= 0 else f" margin-top: {int(margin_top_px)}px;"
    return f"color: {color};{font_size}{font_weight}{margin_top}"


def flat_link_button_qss() -> str:
    p = studio_dark_theme().palette
    return f"color: {p.accent_hover}; text-decoration: underline; text-align: left;"
