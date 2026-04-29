from __future__ import annotations

from qtpy import QtTest, QtWidgets

from f8pystudio.monitoring import alerts
from f8pystudio.monitoring.alerts import MonitorAlertNotifier
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
    monkeypatch.setattr(alerts, "show_warning", lambda parent, title, message: shown.append(("warning", title, message)))
    monkeypatch.setattr(alerts, "show_error", lambda parent, title, message: shown.append(("error", title, message)))
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
    monkeypatch.setattr(alerts, "show_warning", lambda parent, title, message: shown.append(("warning", title, message)))
    monkeypatch.setattr(alerts, "show_error", lambda parent, title, message: shown.append(("error", title, message)))
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
    monkeypatch.setattr(alerts, "show_warning", lambda parent, title, message: shown.append(("warning", title, message)))
    monkeypatch.setattr(alerts, "show_error", lambda parent, title, message: shown.append(("error", title, message)))
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
