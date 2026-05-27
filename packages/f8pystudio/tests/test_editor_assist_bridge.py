from __future__ import annotations

import concurrent.futures

from f8pystudio.ui.support.editor_assist_bridge import PythonEditorAssistBridge


def _bridge_without_lsp() -> PythonEditorAssistBridge:
    bridge = PythonEditorAssistBridge.__new__(PythonEditorAssistBridge)
    bridge._last_error_sig = ""
    bridge._last_error_ts = 0.0
    return bridge


def test_worker_future_result_returns_completed_value() -> None:
    bridge = _bridge_without_lsp()
    future: concurrent.futures.Future[str] = concurrent.futures.Future()
    future.set_result("ready")

    assert bridge._worker_future_result(stage="completion", done_future=future, default="") == "ready"


def test_worker_future_result_logs_and_returns_default_on_failure(monkeypatch) -> None:
    bridge = _bridge_without_lsp()
    future: concurrent.futures.Future[str] = concurrent.futures.Future()
    future.set_exception(RuntimeError("boom"))
    logged: list[tuple[str, str]] = []

    monkeypatch.setattr(
        bridge,
        "_log_bridge_error",
        lambda stage, exc: logged.append((str(stage), str(exc))),
    )

    assert bridge._worker_future_result(stage="completion", done_future=future, default="") == ""
    assert logged == [("completion", "boom")]
