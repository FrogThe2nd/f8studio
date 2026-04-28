from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.ui.components.controls import F8OptionCombo


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_option_combo_destroy_after_popup_deleted_does_not_raise() -> None:
    _ensure_app()
    combo = F8OptionCombo()
    combo.set_options(["a", "b"], labels=["A", "B"])

    popup = combo._ensure_popup()
    popup.deleteLater()
    QtWidgets.QApplication.processEvents()

    combo.deleteLater()
    QtWidgets.QApplication.processEvents()
