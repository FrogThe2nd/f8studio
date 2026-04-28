from __future__ import annotations

from typing import Any

from qtpy import QtCore

from f8pystudio.ai_assist.state_store import AiPanelStateStore

_SETTINGS_GROUP = "monaco_editor/ai_panel/v1"


class QtAiPanelStateStore(AiPanelStateStore):
    def set_value(self, key: str, value: Any) -> None:
        settings = QtCore.QSettings()
        settings.beginGroup(_SETTINGS_GROUP)
        try:
            settings.setValue(str(key), value)
            settings.sync()
        finally:
            settings.endGroup()


    def get_value(self, key: str, default: Any = None) -> Any:
        settings = QtCore.QSettings()
        settings.beginGroup(_SETTINGS_GROUP)
        try:
            return settings.value(str(key), default)
        finally:
            settings.endGroup()
