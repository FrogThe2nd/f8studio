from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal

from qtpy import QtCore


@dataclass(frozen=True)
class _PendingProcessAction:
    action: Literal["stop", "restart"]
    deadline_s: float
    service_class: str | None


class ServiceProcessActionScheduler:
    """
    Manage deferred stop/restart actions with graceful-exit polling.

    Behavior mirrors previous bridge-local logic:
    - wait for graceful terminate deadline
    - fallback to local hard stop
    - for restart: relaunch once process is confirmed down
    """

    def __init__(
        self,
        *,
        owner: QtCore.QObject,
        is_service_running: Callable[[str], bool],
        stop_process_once: Callable[[str], bool],
        emit_service_process_state: Callable[[str, bool], None],
        start_service: Callable[[str, str | None], None],
        emit_log: Callable[[str], None],
        report_exception: Callable[[str, BaseException], None],
    ) -> None:
        self._owner = owner
        self._is_service_running = is_service_running
        self._stop_process_once = stop_process_once
        self._emit_service_process_state = emit_service_process_state
        self._start_service = start_service
        self._emit_log = emit_log
        self._report_exception = report_exception
        self._actions: dict[str, _PendingProcessAction] = {}
        self._timers: dict[str, QtCore.QTimer] = {}

    def schedule_stop(self, *, service_id: str, grace_s: float) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self._actions[sid] = _PendingProcessAction(action="stop", deadline_s=time.monotonic() + float(grace_s), service_class=None)
        timer = self._ensure_timer(sid)
        if not timer.isActive():
            timer.start()
        self._poll(sid)

    def schedule_restart(self, *, service_id: str, service_class: str | None, grace_s: float) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        svc_class = str(service_class or "").strip() or None
        self._actions[sid] = _PendingProcessAction(
            action="restart",
            deadline_s=time.monotonic() + float(grace_s),
            service_class=svc_class,
        )
        timer = self._ensure_timer(sid)
        if not timer.isActive():
            timer.start()
        self._poll(sid)

    def cancel(self, *, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self._actions.pop(sid, None)
        timer = self._timers.pop(sid, None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError as exc:
                self._report_exception(f"stop proc-action timer failed serviceId={sid}", exc)
            try:
                timer.timeout.disconnect()
            except TypeError:
                pass
            except RuntimeError as exc:
                self._report_exception(f"disconnect proc-action timer failed serviceId={sid}", exc)
            try:
                timer.setParent(None)
            except RuntimeError as exc:
                self._report_exception(f"detach proc-action timer failed serviceId={sid}", exc)
            try:
                timer.deleteLater()
            except RuntimeError as exc:
                self._report_exception(f"delete proc-action timer failed serviceId={sid}", exc)
            self._flush_deferred_delete(timer=timer, service_id=sid)

    def cancel_all(self) -> None:
        for service_id in list(self._actions.keys()):
            self.cancel(service_id=service_id)
        for service_id in list(self._timers.keys()):
            self.cancel(service_id=service_id)

    def _ensure_timer(self, service_id: str) -> QtCore.QTimer:
        sid = str(service_id)
        existing = self._timers.get(sid)
        if existing is not None:
            return existing
        timer = QtCore.QTimer(self._owner)
        timer.setInterval(120)
        timer.timeout.connect(lambda _sid=sid: self._poll(_sid))
        self._timers[sid] = timer
        return timer

    def _flush_deferred_delete(self, *, timer: QtCore.QTimer, service_id: str) -> None:
        app = QtCore.QCoreApplication.instance()
        if app is None:
            return
        try:
            if timer.thread() is not app.thread():
                return
        except RuntimeError as exc:
            self._report_exception(f"inspect proc-action timer thread failed serviceId={service_id}", exc)
            return
        try:
            QtCore.QCoreApplication.sendPostedEvents(timer, int(QtCore.QEvent.Type.DeferredDelete))
        except RuntimeError as exc:
            self._report_exception(f"flush proc-action timer delete failed serviceId={service_id}", exc)

    def _poll(self, service_id: str) -> None:
        sid = str(service_id)
        action = self._actions.get(sid)
        if action is None:
            self.cancel(service_id=sid)
            return

        if not self._is_service_running(sid):
            self.cancel(service_id=sid)
            self._emit_service_process_state(sid, False)
            if action.action == "restart":
                self._start_service(sid, action.service_class)
            return

        if action.deadline_s and time.monotonic() < action.deadline_s:
            return

        stop_ok = self._stop_process_once(sid)
        still_running = bool(self._is_service_running(sid))
        if still_running:
            self._emit_log(f"stop_service incomplete (process still running): serviceId={sid}")
            self._actions[sid] = _PendingProcessAction(
                action=action.action,
                deadline_s=time.monotonic() + 1.0,
                service_class=action.service_class,
            )
            return

        self.cancel(service_id=sid)
        self._emit_service_process_state(sid, still_running)
        if not still_running and action.action == "restart":
            self._start_service(sid, action.service_class)
