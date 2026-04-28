from __future__ import annotations

from f8pystudio.assets.ui.background_tasks import _signal_source_deleted


def test_signal_source_deleted_matches_qt_runtime_error_text() -> None:
    assert _signal_source_deleted(RuntimeError("Signal source has been deleted")) is True
    assert _signal_source_deleted(RuntimeError("different runtime error")) is False
