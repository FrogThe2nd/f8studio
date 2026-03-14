from __future__ import annotations

from typing import Any

from qtpy import QtCore

_SETTINGS_GROUP = "monaco_editor/ai_panel/v1"


def save_ai_panel_state(key: str, value: Any) -> None:
    settings = QtCore.QSettings()
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        settings.setValue(str(key), value)
        settings.sync()
    finally:
        settings.endGroup()


def load_ai_panel_state(key: str, default: Any = None) -> Any:
    settings = QtCore.QSettings()
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        return settings.value(str(key), default)
    finally:
        settings.endGroup()
