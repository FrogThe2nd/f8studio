from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.nodegraph.items import inline_command_panel as icp
from f8pystudio.nodegraph.items import inline_state_panel as isp


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _assert_disposed_without_window_flash(dispose_fn) -> None:
    app = _ensure_app()
    widget = QtWidgets.QWidget()
    widget.setWindowFlag(QtCore.Qt.WindowType.Window, True)
    widget.setWindowTitle("temp")
    widget.show()
    app.processEvents()

    dispose_fn(widget)
    assert widget.isVisible() is False
    assert widget.testAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen) is True


def test_inline_state_dispose_proxy_widget_hides_without_top_level_window_flag() -> None:
    _assert_disposed_without_window_flash(isp._dispose_proxy_widget)


def test_inline_command_dispose_proxy_widget_hides_without_top_level_window_flag() -> None:
    _assert_disposed_without_window_flash(icp._dispose_proxy_widget)
