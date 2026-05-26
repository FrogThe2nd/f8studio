from __future__ import annotations

from f8pystudio.ui.support.qt_lifecycle import qt_object_is_valid, qt_runtime_error_is_object_deleted


def test_qt_runtime_error_is_object_deleted_detects_deleted_wrapper_error() -> None:
    exc = RuntimeError("Internal C++ object (PySide6.QtWidgets.QListWidget) already deleted.")
    assert qt_runtime_error_is_object_deleted(exc) is True


def test_qt_runtime_error_is_object_deleted_detects_deleted_signal_source() -> None:
    exc = RuntimeError("Signal source has been deleted")
    assert qt_runtime_error_is_object_deleted(exc) is True


def test_qt_object_is_valid_returns_false_for_none() -> None:
    assert qt_object_is_valid(None) is False
