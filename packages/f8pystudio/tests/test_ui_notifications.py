from __future__ import annotations

from qtpy import QtCore, QtGui, QtTest, QtWidgets

from f8pystudio.monitoring import alerts
from f8pystudio.monitoring.alerts import MonitorAlertNotifier
from f8pystudio.ui.support.ui_notifications import (
    _ACTIVE_TOASTS,
    _INFO_STYLE,
    _StudioToast,
    _TOAST_DURATION_MS,
    _TOAST_SPACING,
    _clear_notification_history_for_tests,
    _rich_text_message,
    _use_safe_toast_window_mode,
    export_recent_notifications,
    show_error,
    show_keyed_warning,
    show_warning,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _monitor_payload(
    *,
    service_id: str = "svcA",
    node_id: str = "node1",
    code: str = "E_TEST",
    message: str = "boom",
    severity: str = "error",
    fingerprint: str = "fp-a",
    repeat_count: int = 1,
    ts_ms: int = 1000,
) -> dict[str, object]:
    return {
        "schemaVersion": "f8monitor/1",
        "serviceId": service_id,
        "serviceClass": "f8.tests",
        "nodeId": service_id,
        "tsMs": ts_ms,
        "error": {
            "countWindow": repeat_count,
            "lastNodeId": node_id,
            "lastCode": code,
            "lastMessage": message,
            "lastSeverity": severity,
            "lastFingerprint": fingerprint,
            "lastRepeatCount": repeat_count,
            "lastTsMs": ts_ms,
            "currentNodeId": node_id,
            "currentCode": code,
            "currentMessage": message,
            "currentSeverity": severity,
            "currentTsMs": ts_ms,
        },
    }


def test_monitor_alert_notifier_toasts_and_debounces(monkeypatch) -> None:
    shown: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerts, "now_ms", lambda: 100_000)
    monkeypatch.setattr(
        alerts,
        "show_keyed_warning",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("warning", title, message)),
    )
    monkeypatch.setattr(
        alerts,
        "show_keyed_error",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("error", title, message)),
    )
    notifier = MonitorAlertNotifier(debounce_ms=10_000)

    assert notifier.handle_snapshot(_monitor_payload(severity="warning", ts_ms=1000), parent=None) is True
    assert shown == [("warning", "svcA/node1 warning", "E_TEST: boom")]

    assert notifier.handle_snapshot(_monitor_payload(severity="warning", repeat_count=2, ts_ms=1001), parent=None) is False
    assert len(shown) == 1

    assert notifier.handle_snapshot(
        _monitor_payload(severity="warning", fingerprint="fp-b", repeat_count=1, ts_ms=1002),
        parent=None,
    ) is True
    assert shown[-1] == ("warning", "svcA/node1 warning", "E_TEST: boom")


def test_monitor_alert_notifier_allows_repeat_summary(monkeypatch) -> None:
    shown: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerts, "now_ms", lambda: 100_000)
    monkeypatch.setattr(
        alerts,
        "show_keyed_warning",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("warning", title, message)),
    )
    monkeypatch.setattr(
        alerts,
        "show_keyed_error",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("error", title, message)),
    )
    notifier = MonitorAlertNotifier(debounce_ms=10_000)

    assert notifier.handle_snapshot(_monitor_payload(severity="error", repeat_count=1, ts_ms=1000), parent=None) is True
    assert notifier.handle_snapshot(_monitor_payload(severity="error", repeat_count=2, ts_ms=1001), parent=None) is False
    assert notifier.handle_snapshot(_monitor_payload(severity="error", repeat_count=10, ts_ms=1002), parent=None) is True

    assert shown[0][0] == "error"
    assert shown[1][0] == "error"
    assert "Repeated 10 times." in shown[1][2]


def test_monitor_alert_notifier_ignores_info_and_clear_snapshots(monkeypatch) -> None:
    shown: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerts, "now_ms", lambda: 100_000)
    monkeypatch.setattr(
        alerts,
        "show_keyed_warning",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("warning", title, message)),
    )
    monkeypatch.setattr(
        alerts,
        "show_keyed_error",
        lambda parent, key, title, message, *, repeat_count=1: shown.append(("error", title, message)),
    )
    notifier = MonitorAlertNotifier(debounce_ms=10_000)

    assert notifier.handle_snapshot(_monitor_payload(severity="info", ts_ms=1000), parent=None) is False

    error_payload = _monitor_payload(severity="critical", ts_ms=1001)
    assert notifier.handle_snapshot(error_payload, parent=None) is True
    clear_payload = dict(error_payload)
    clear_payload["error"] = dict(error_payload["error"])  # type: ignore[arg-type]
    clear_payload["error"]["currentNodeId"] = ""  # type: ignore[index]
    clear_payload["error"]["currentCode"] = ""  # type: ignore[index]
    clear_payload["error"]["currentMessage"] = ""  # type: ignore[index]
    clear_payload["error"]["currentSeverity"] = ""  # type: ignore[index]
    clear_payload["error"]["currentTsMs"] = None  # type: ignore[index]

    assert notifier.handle_snapshot(clear_payload, parent=None) is False
    assert len(shown) == 1


def _close_active_toasts() -> None:
    for toast in list(_ACTIVE_TOASTS):
        toast.close()
    QtWidgets.QApplication.processEvents()


def test_notification_history_exports_recent_warning_toasts() -> None:
    _ensure_app()
    _close_active_toasts()
    _clear_notification_history_for_tests()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    show_warning(parent, "Container required", "Operator nodes must be placed within a service container.")
    QtWidgets.QApplication.processEvents()

    payload = export_recent_notifications(limit=10, minimum_severity="WARNING")

    assert payload["count"] == 1
    assert payload["storedCount"] == 1
    assert payload["minimumSeverity"] == "WARNING"
    entry = payload["entries"][0]
    assert entry["severity"] == "WARNING"
    assert entry["title"] == "Container required"
    assert entry["message"] == "Operator nodes must be placed within a service container."
    assert entry["repeatCount"] == 1
    assert entry["createdAt"]
    assert entry["updatedAt"]

    _close_active_toasts()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_notification_history_merges_repeated_and_keyed_warnings() -> None:
    _ensure_app()
    _close_active_toasts()
    _clear_notification_history_for_tests()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    show_warning(parent, "Refresh failed", "network timeout")
    show_warning(parent, "Refresh failed", "network timeout")
    show_keyed_warning(parent, "monitor:svc:node:fp", "svc/node warning", "first", repeat_count=1)
    show_keyed_warning(parent, "monitor:svc:node:fp", "svc/node warning", "second", repeat_count=7)
    QtWidgets.QApplication.processEvents()

    payload = export_recent_notifications(limit=10, minimum_severity="")

    assert payload["count"] == 2
    repeated_entry = payload["entries"][0]
    keyed_entry = payload["entries"][1]
    assert repeated_entry["title"] == "Refresh failed"
    assert repeated_entry["repeatCount"] == 2
    assert keyed_entry["title"] == "svc/node warning"
    assert keyed_entry["message"] == "second"
    assert keyed_entry["repeatCount"] == 7
    assert keyed_entry["dedupeKey"] == "monitor:svc:node:fp"

    _close_active_toasts()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_rich_text_message_preserves_newlines_and_wrap_hints() -> None:
    html_message = _rich_text_message("Saved to:\n/tmp/very-long_name.json")

    assert "Saved to:" in html_message
    assert "<br/>" in html_message
    assert "/<wbr/>tmp/<wbr/>" in html_message
    assert "_<wbr/>" in html_message
    assert ".<wbr/>json" in html_message


def test_toast_uses_safe_window_mode_under_pytest() -> None:
    _ensure_app()
    _close_active_toasts()

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
    _close_active_toasts()
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
    _close_active_toasts()
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
    _close_active_toasts()
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
    _close_active_toasts()
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


def test_warning_toast_stays_visible_until_acknowledged() -> None:
    _ensure_app()
    _close_active_toasts()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    show_warning(parent, "Deploy failed", "RuntimeError: boom")
    QtWidgets.QApplication.processEvents()

    assert len(_ACTIVE_TOASTS) == 1
    toast = _ACTIVE_TOASTS[0]
    assert toast._duration_ms == 0

    QtTest.QTest.qWait(_TOAST_DURATION_MS + 160)
    QtWidgets.QApplication.processEvents()

    assert toast in _ACTIVE_TOASTS
    assert toast.isVisible()

    toast.close_animated()
    QtWidgets.QApplication.processEvents()

    assert toast not in _ACTIVE_TOASTS

    parent.close()
    QtWidgets.QApplication.processEvents()


def test_warning_toast_is_clickable_child_of_modal_dialog(monkeypatch) -> None:
    _ensure_app()
    _close_active_toasts()
    dialog = QtWidgets.QDialog()
    dialog.setGeometry(100, 120, 420, 260)
    dialog.setModal(True)
    dialog.show()
    QtWidgets.QApplication.processEvents()

    shown: list[tuple[QtWidgets.QWidget | None, str, str]] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda parent, title, message: shown.append((parent, str(title), str(message))),
    )

    show_warning(dialog, "Invalid name", "Name already exists.")
    QtWidgets.QApplication.processEvents()

    assert shown == []
    assert len(_ACTIVE_TOASTS) == 1
    toast = _ACTIVE_TOASTS[0]
    assert toast.parentWidget() is dialog

    close_button = toast.findChild(QtWidgets.QToolButton, "studio-toast-close")
    assert close_button is not None
    close_button.click()
    QtWidgets.QApplication.processEvents()

    assert toast not in _ACTIVE_TOASTS

    dialog.close()
    QtWidgets.QApplication.processEvents()


def test_toast_is_child_overlay_not_global_always_on_top() -> None:
    _ensure_app()
    _close_active_toasts()
    window = QtWidgets.QWidget()
    window.setGeometry(100, 120, 640, 360)
    window.show()
    QtWidgets.QApplication.processEvents()

    show_warning(window, "Deploy failed", "RuntimeError: boom")
    QtWidgets.QApplication.processEvents()

    assert len(_ACTIVE_TOASTS) == 1
    toast = _ACTIVE_TOASTS[0]
    assert toast.parentWidget() is window
    assert not bool(toast.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint)
    assert toast.geometry().right() <= window.rect().right()
    assert toast.geometry().bottom() <= window.rect().bottom()

    window.hide()
    QtWidgets.QApplication.processEvents()

    assert not toast.isVisible()

    _close_active_toasts()
    window.close()
    QtWidgets.QApplication.processEvents()


def test_error_toast_copy_button_copies_debuggable_text() -> None:
    _ensure_app()
    _close_active_toasts()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    clipboard = QtGui.QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("")

    show_error(parent, "Publish failed", "ValueError: bad payload")
    QtWidgets.QApplication.processEvents()

    toast = _ACTIVE_TOASTS[0]
    copy_button = toast.findChild(QtWidgets.QToolButton, "studio-toast-copy")
    assert copy_button is not None

    copy_button.click()

    copied = clipboard.text()
    assert "Severity: ERROR" in copied
    assert "Title: Publish failed" in copied
    assert "Created:" in copied
    assert "Message:\nValueError: bad payload" in copied

    toast.close()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_repeated_warning_updates_existing_sticky_toast() -> None:
    _ensure_app()
    _close_active_toasts()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    show_warning(parent, "Refresh failed", "network timeout")
    show_warning(parent, "Refresh failed", "network timeout")
    QtWidgets.QApplication.processEvents()

    assert len(_ACTIVE_TOASTS) == 1
    toast = _ACTIVE_TOASTS[0]
    title_label = toast.findChild(QtWidgets.QLabel, "studio-toast-title")
    assert title_label is not None
    assert "x2" in title_label.text()

    clipboard = QtGui.QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("")
    copy_button = toast.findChild(QtWidgets.QToolButton, "studio-toast-copy")
    assert copy_button is not None
    copy_button.click()

    copied = clipboard.text()
    assert "Repeat count: 2" in copied
    assert "network timeout" in copied

    toast.close()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_keyed_warning_updates_existing_toast_content() -> None:
    _ensure_app()
    _close_active_toasts()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    show_keyed_warning(parent, "monitor:svcA:node1:fp", "svcA/node1 error", "first", repeat_count=1)
    show_keyed_warning(parent, "monitor:svcA:node1:fp", "svcA/node1 error", "second", repeat_count=7)
    QtWidgets.QApplication.processEvents()

    assert len(_ACTIVE_TOASTS) == 1
    toast = _ACTIVE_TOASTS[0]
    title_label = toast.findChild(QtWidgets.QLabel, "studio-toast-title")
    message_label = toast.findChild(QtWidgets.QLabel, "studio-toast-message")
    assert title_label is not None
    assert message_label is not None
    assert "x7" in title_label.text()
    assert "second" in message_label.text()

    clipboard = QtGui.QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("")
    copy_button = toast.findChild(QtWidgets.QToolButton, "studio-toast-copy")
    assert copy_button is not None
    copy_button.click()

    copied = clipboard.text()
    assert "Repeat count: 7" in copied
    assert "second" in copied

    toast.close()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_distinct_sticky_toasts_roll_up_after_three_details() -> None:
    _ensure_app()
    _close_active_toasts()
    parent = QtWidgets.QWidget()
    parent.setGeometry(100, 120, 920, 720)
    parent.show()

    for index in range(5):
        show_warning(parent, f"Warning {index}", f"message {index}")
    QtWidgets.QApplication.processEvents()

    visible_toasts = [toast for toast in _ACTIVE_TOASTS if toast.isVisible()]
    detail_toasts = [toast for toast in visible_toasts if not toast._is_rollup]
    rollup_toasts = [toast for toast in visible_toasts if toast._is_rollup]

    assert len(visible_toasts) == 4
    assert len(detail_toasts) == 3
    assert len(rollup_toasts) == 1

    rollup = rollup_toasts[0]
    rollup_title = rollup.findChild(QtWidgets.QLabel, "studio-toast-title")
    assert rollup_title is not None
    assert "More notifications (2)" in rollup_title.text()

    clipboard = QtGui.QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("")
    copy_button = rollup.findChild(QtWidgets.QToolButton, "studio-toast-copy")
    assert copy_button is not None
    copy_button.click()

    copied = clipboard.text()
    assert "Title: Warning 0" in copied
    assert "message 0" in copied
    assert "Title: Warning 1" in copied
    assert "message 1" in copied

    _close_active_toasts()
    parent.close()
    QtWidgets.QApplication.processEvents()


def test_warning_toast_logs_button_opens_service_logs_dock() -> None:
    _ensure_app()
    _close_active_toasts()
    window = QtWidgets.QMainWindow()
    window.setGeometry(100, 120, 920, 720)
    dock = QtWidgets.QDockWidget("Service Logs", window)
    dock.setObjectName("ServiceLogsDock")
    dock.setWidget(QtWidgets.QPlainTextEdit())
    window.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock)
    window.show()
    dock.setVisible(False)
    QtWidgets.QApplication.processEvents()

    show_warning(window, "Deploy failed", "compile failed")
    QtWidgets.QApplication.processEvents()

    toast = _ACTIVE_TOASTS[0]
    logs_button = toast.findChild(QtWidgets.QToolButton, "studio-toast-logs")
    assert logs_button is not None
    assert logs_button.isVisible()
    assert not dock.isVisible()

    logs_button.click()
    QtWidgets.QApplication.processEvents()

    assert dock.isVisible()

    _close_active_toasts()
    window.close()
    QtWidgets.QApplication.processEvents()
