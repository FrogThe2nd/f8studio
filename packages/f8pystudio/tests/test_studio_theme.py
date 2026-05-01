from __future__ import annotations

from qtpy import QtGui, QtWidgets

from f8pystudio.ui.mainwin.node_library_widget import F8StudioNodeLibraryWidget
from f8pystudio.ui.support.studio_theme import apply_studio_theme, qss_for_theme, studio_dark_theme
from f8pystudio.ui.widgets.service_log_widget import ServiceLogView


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def _color_name(color: QtGui.QColor) -> str:
    return str(color.name()).upper()


def test_apply_studio_theme_sets_core_application_palette() -> None:
    app = _ensure_app()
    theme = studio_dark_theme()

    apply_studio_theme(app, theme)

    p = theme.palette
    palette = app.palette()
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.Window)) == p.window_bg
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.Base)) == p.field_bg
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.Text)) == p.text_primary
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.Button)) == p.button_bg
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.Highlight)) == p.selection_bg
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.ToolTipBase)) == p.tooltip_bg
    assert _color_name(palette.color(QtGui.QPalette.ColorRole.ToolTipText)) == p.tooltip_text


def test_theme_qss_contains_core_widget_rules() -> None:
    qss = qss_for_theme(studio_dark_theme())

    assert "QMainWindow" in qss
    assert "QToolTip" in qss
    assert "QDockWidget" in qss
    assert "QTreeView" in qss
    assert "QLineEdit" in qss
    assert "QToolButton" in qss


def test_key_widgets_inherit_dark_theme_palette() -> None:
    app = _ensure_app()
    theme = studio_dark_theme()
    apply_studio_theme(app, theme)
    p = theme.palette

    log_view = ServiceLogView()
    node_library = F8StudioNodeLibraryWidget(node_graph=None)

    log_palette = log_view.palette()
    assert _color_name(log_palette.color(QtGui.QPalette.ColorRole.Base)) == p.log_bg
    assert _color_name(log_palette.color(QtGui.QPalette.ColorRole.Text)) == p.text_primary

    library_palette = node_library.palette()
    assert _color_name(library_palette.color(QtGui.QPalette.ColorRole.Window)) == p.window_bg
    assert _color_name(library_palette.color(QtGui.QPalette.ColorRole.WindowText)) == p.text_primary
