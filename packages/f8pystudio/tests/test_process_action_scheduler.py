from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.bridge.process_action_scheduler import ServiceProcessActionScheduler


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_schedule_stop_grace_expired_stops_process_and_emits_down_state() -> None:
    _ensure_app()
    owner = QtCore.QObject()
    running_by_service = {"svc.a": True}
    stop_calls: list[str] = []
    emitted_states: list[tuple[str, bool]] = []
    start_calls: list[tuple[str, str | None]] = []
    log_lines: list[str] = []
    report_lines: list[str] = []

    def _is_running(service_id: str) -> bool:
        return bool(running_by_service.get(service_id, False))

    def _stop_once(service_id: str) -> bool:
        stop_calls.append(str(service_id))
        running_by_service[str(service_id)] = False
        return True

    scheduler = ServiceProcessActionScheduler(
        owner=owner,
        is_service_running=_is_running,
        stop_process_once=_stop_once,
        emit_service_process_state=lambda sid, running: emitted_states.append((str(sid), bool(running))),
        start_service=lambda sid, service_class: start_calls.append((str(sid), service_class)),
        emit_log=lambda line: log_lines.append(str(line)),
        report_exception=lambda context, exc: report_lines.append(f"{context}:{type(exc).__name__}"),
    )

    scheduler.schedule_stop(service_id="svc.a", grace_s=0.0)

    assert stop_calls == ["svc.a"]
    assert emitted_states == [("svc.a", False)]
    assert start_calls == []
    assert log_lines == []
    assert report_lines == []
    QtCore.QCoreApplication.sendPostedEvents(None, int(QtCore.QEvent.Type.DeferredDelete))
    QtWidgets.QApplication.processEvents()
    assert owner.findChildren(QtCore.QTimer) == []


def test_schedule_restart_when_already_stopped_relaunches_immediately() -> None:
    _ensure_app()
    owner = QtCore.QObject()
    running_by_service = {"svc.b": False}
    emitted_states: list[tuple[str, bool]] = []
    start_calls: list[tuple[str, str | None]] = []
    stop_calls: list[str] = []

    scheduler = ServiceProcessActionScheduler(
        owner=owner,
        is_service_running=lambda service_id: bool(running_by_service.get(service_id, False)),
        stop_process_once=lambda service_id: stop_calls.append(str(service_id)) or True,
        emit_service_process_state=lambda sid, running: emitted_states.append((str(sid), bool(running))),
        start_service=lambda sid, service_class: start_calls.append((str(sid), service_class)),
        emit_log=lambda line: None,
        report_exception=lambda context, exc: None,
    )

    scheduler.schedule_restart(service_id="svc.b", service_class="f8.tests.b", grace_s=2.0)

    assert stop_calls == []
    assert emitted_states == [("svc.b", False)]
    assert start_calls == [("svc.b", "f8.tests.b")]
    QtCore.QCoreApplication.sendPostedEvents(None, int(QtCore.QEvent.Type.DeferredDelete))
    QtWidgets.QApplication.processEvents()
    assert owner.findChildren(QtCore.QTimer) == []
