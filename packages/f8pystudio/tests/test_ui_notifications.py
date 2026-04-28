from __future__ import annotations

from qtpy import QtTest, QtWidgets

from f8pystudio.ui.support.ui_notifications import (
    _ACTIVE_TOASTS,
    _INFO_STYLE,
    _StudioToast,
    _TOAST_SPACING,
    _rich_text_message,
    _use_safe_toast_window_mode,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_rich_text_message_preserves_newlines_and_wrap_hints() -> None:
    html_message = _rich_text_message("Saved to:\n/tmp/very-long_name.json")

    assert "Saved to:" in html_message
    assert "<br/>" in html_message
    assert "/<wbr/>tmp/<wbr/>" in html_message
    assert "_<wbr/>" in html_message
    assert ".<wbr/>json" in html_message


def test_toast_uses_safe_window_mode_under_pytest() -> None:
    _ensure_app()

    toast = _StudioToast(
        anchor=None,
        title="Session saved",
        message="Saved to:\n/tmp/test-session.json",
        style=_INFO_STYLE,
        duration_ms=0,
    )

    assert _use_safe_toast_window_mode() is True
    assert toast._safe_window_mode is True
    assert toast.graphicsEffect() is None

    toast.close()
    QtWidgets.QApplication.processEvents()


def test_studio_toast_expands_for_multiline_path_message() -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    long_path = "/" + "/".join(f"segment-{index:02d}_with_extra_length" for index in range(12)) + ".json"
    toast = _StudioToast(
        anchor=parent,
        title="Session saved",
        message=f"Saved to:\n{long_path}",
        style=_INFO_STYLE,
        duration_ms=0,
    )
    toast.show_with_animation()
    QtWidgets.QApplication.processEvents()

    badge = toast.findChild(QtWidgets.QWidget, "studio-toast-badge")
    message_label = toast.findChild(QtWidgets.QLabel, "studio-toast-message")

    assert badge is not None
    assert message_label is not None
    assert message_label.wordWrap()
    assert toast.width() <= 520
    assert message_label.height() >= message_label.fontMetrics().lineSpacing() * 3

    toast.close()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_studio_toast_auto_closes_after_duration() -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    toast = _StudioToast(
        anchor=parent,
        title="Session saved",
        message="Saved to:\n/tmp/test-session.json",
        style=_INFO_STYLE,
        duration_ms=180,
    )
    closed_toasts: list[object] = []
    toast.closed.connect(closed_toasts.append)

    toast.show_with_animation()
    QtTest.QTest.qWait(80)
    QtWidgets.QApplication.processEvents()

    assert 0.0 < toast._progress_fraction < 1.0

    QtTest.QTest.qWait(340)
    QtWidgets.QApplication.processEvents()

    assert closed_toasts == [toast]
    assert toast not in _ACTIVE_TOASTS

    parent.close()
    QtWidgets.QApplication.processEvents()


def test_studio_toast_reflows_without_overlap_after_bottom_toast_closes() -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    toast_bottom = _StudioToast(
        anchor=parent,
        title="First",
        message="Bottom toast",
        style=_INFO_STYLE,
        duration_ms=0,
    )
    toast_middle = _StudioToast(
        anchor=parent,
        title="Second",
        message="Middle toast",
        style=_INFO_STYLE,
        duration_ms=0,
    )
    toast_top = _StudioToast(
        anchor=parent,
        title="Third",
        message="Top toast",
        style=_INFO_STYLE,
        duration_ms=0,
    )

    _ACTIVE_TOASTS.append(toast_bottom)
    _ACTIVE_TOASTS.append(toast_middle)
    _ACTIVE_TOASTS.append(toast_top)
    toast_bottom.show_with_animation()
    toast_middle.show_with_animation()
    toast_top.show_with_animation()
    QtWidgets.QApplication.processEvents()

    assert toast_bottom.y() > toast_middle.y() > toast_top.y()

    toast_bottom.close_animated()
    QtTest.QTest.qWait(260)
    QtWidgets.QApplication.processEvents()

    assert toast_bottom not in _ACTIVE_TOASTS
    assert toast_middle in _ACTIVE_TOASTS
    assert toast_top in _ACTIVE_TOASTS
    assert toast_middle.y() > toast_top.y()
    assert toast_middle.y() >= toast_top.y() + toast_top.height() + _TOAST_SPACING

    toast_middle.close()
    toast_top.close()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_studio_toast_falls_back_to_screen_geometry_after_anchor_deletion() -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    toast_bottom = _StudioToast(
        anchor=parent,
        title="First",
        message="Bottom toast",
        style=_INFO_STYLE,
        duration_ms=0,
    )
    toast_top = _StudioToast(
        anchor=parent,
        title="Second",
        message="Top toast",
        style=_INFO_STYLE,
        duration_ms=0,
    )

    _ACTIVE_TOASTS.append(toast_bottom)
    _ACTIVE_TOASTS.append(toast_top)
    toast_bottom.show_with_animation()
    toast_top.show_with_animation()
    QtWidgets.QApplication.processEvents()

    parent.close()
    parent.deleteLater()
    QtWidgets.QApplication.processEvents()

    geometry = toast_top._target_geometry()
    assert geometry.width() > 0
    assert geometry.height() > 0

    toast_bottom.close()
    QtWidgets.QApplication.processEvents()

    assert toast_top in _ACTIVE_TOASTS

    toast_top.close()
    QtWidgets.QApplication.processEvents()
