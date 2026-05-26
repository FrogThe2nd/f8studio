from __future__ import annotations

from qtpy import QtCore


def qt_runtime_error_is_object_deleted(exc: RuntimeError) -> bool:
    """
    Return True when a PySide/PyQt RuntimeError indicates the wrapped C++ Qt
    object has already been destroyed.
    """
    message = str(exc).lower()
    return "already deleted" in message or "signal source has been deleted" in message


def qt_object_is_valid(obj: QtCore.QObject | None) -> bool:
    """
    Return True when the wrapped Qt/C++ instance is still alive.

    PySide6 can keep the Python wrapper around after the underlying C++ object
    has already been deleted. Accessing that wrapper raises
    `RuntimeError: Internal C++ object ... already deleted.`
    """
    if obj is None:
        return False
    try:
        import shiboken6  # type: ignore[import-not-found]
    except ImportError:
        shiboken6 = None

    if shiboken6 is not None:
        try:
            return bool(shiboken6.isValid(obj))
        except (RuntimeError, TypeError):
            return False

    try:
        obj.parent()
        return True
    except RuntimeError:
        return False
