from __future__ import annotations

from collections.abc import Callable
import threading
import time

from qtpy import QtCore


class BackgroundCallWorker(QtCore.QObject):
    succeeded = QtCore.Signal(int, object, float)
    failed = QtCore.Signal(int, object, float)

    def __init__(self, *, request_id: int, task: Callable[[], object]) -> None:
        super().__init__(None)
        self._request_id = int(request_id)
        self._task = task
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        worker_thread = threading.Thread(
            target=self._run,
            name=f"f8pystudio-background-call-{self._request_id}",
            daemon=True,
        )
        self._thread = worker_thread
        worker_thread.start()

    def _run(self) -> None:
        started_at = time.perf_counter()
        try:
            result = self._task()
        except Exception as exc:
            self.failed.emit(self._request_id, exc, time.perf_counter() - started_at)
            return
        self.succeeded.emit(self._request_id, result, time.perf_counter() - started_at)


__all__ = ["BackgroundCallWorker"]
