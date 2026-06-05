from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.ui.agents import AgentContextUsageButton, AgentSurfaceScope


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _Bridge(QtCore.QObject):
    context_usage_updated = QtCore.Signal(int, int)

    def get_context_breakdown(self) -> dict[str, object]:
        return {
            "system_tokens": 1000,
            "code_tokens": 2000,
            "chat_tokens": 3000,
            "used_tokens": 6000,
            "total_tokens": 12000,
        }


def test_context_usage_button_updates_text_and_tooltip() -> None:
    _ensure_app()
    bridge = _Bridge()
    button = AgentContextUsageButton(bridge, scope=AgentSurfaceScope.EDITOR)

    bridge.context_usage_updated.emit(6000, 12000)

    assert button.text() == "50% free"
    assert "Code: 2k tok" in button.toolTip()
    assert "Used: 6k / 12k tok" in button.toolTip()


def test_context_usage_button_ignores_invalid_total() -> None:
    _ensure_app()
    bridge = _Bridge()
    button = AgentContextUsageButton(bridge, scope=AgentSurfaceScope.GRAPH)

    bridge.context_usage_updated.emit(1, 0)

    assert button.text() == "100% free"
