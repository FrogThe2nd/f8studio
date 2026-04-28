from __future__ import annotations

import logging

from qtpy import QtWidgets

from f8pystudio.ui.widgets.service_log_widget import ServiceLogDock


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_service_log_dock_filters_spdlog_lines_by_minimum_level() -> None:
    _ensure_app()
    dock = ServiceLogDock()
    dock.set_minimum_level(logging.WARNING)

    dock.append("tracker", "[2026-04-18 16:20:43.756] [console] [info] cvkit_tracking started\n")
    dock.append("tracker", "[2026-04-18 16:20:46.716] [console] [error] cvkit_tracking failed\n")

    view = dock._views["tracker"]
    text = view.toPlainText()
    assert "started" not in text
    assert "failed" in text


def test_service_log_dock_rebuilds_hidden_lines_when_level_changes() -> None:
    _ensure_app()
    dock = ServiceLogDock()
    dock.set_minimum_level(logging.WARNING)

    dock.append("studio", "[service] stop requested: tracker\n")

    view = dock._views["studio"]
    assert view.toPlainText() == ""

    dock.set_minimum_level(logging.INFO)

    assert "[service] stop requested: tracker" in view.toPlainText()


def test_service_log_dock_keeps_traceback_lines_visible_for_errors() -> None:
    _ensure_app()
    dock = ServiceLogDock()
    dock.set_minimum_level(logging.ERROR)

    dock.append("studio", "[ERROR] deploy compile failed: RuntimeError: boom\n")
    dock.append("studio", "Traceback (most recent call last):\n")
    dock.append("studio", "  File \"main.py\", line 12, in deploy\n")
    dock.append("studio", "RuntimeError: boom\n")

    text = dock._views["studio"].toPlainText()
    assert "deploy compile failed" in text
    assert "Traceback (most recent call last):" in text
    assert "File \"main.py\", line 12" in text
    assert "RuntimeError: boom" in text
