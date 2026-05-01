from __future__ import annotations

import os
import sys

from qtpy import QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.ui.dialogs.monitor_stream_dialog import MonitorStreamDialog  # noqa: E402


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeBridge:
    def __init__(self) -> None:
        self.requests: list[tuple[str, int]] = []

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        self.requests.append((str(service_id), int(limit)))
        return [
            {
                "schemaVersion": "f8monitor/1",
                "serviceId": service_id,
                "serviceClass": "f8.tests.a",
                "nodeId": service_id,
                "tsMs": 1_700_000_000_000,
                "alive": True,
                "ready": False,
                "active": True,
                "cpu": {"processPercent": 12.5},
                "memory": {"rssBytes": 64 * 1024 * 1024},
                "timing": {"latencyMsP95": 3.25},
                "error": {"countWindow": 2, "lastMessage": "last error"},
            }
        ]


def test_monitor_stream_dialog_populates_table_and_json() -> None:
    _ensure_app()
    bridge = _FakeBridge()

    dialog = MonitorStreamDialog(bridge=bridge, service_id="svcA")
    dialog._timer.stop()

    assert bridge.requests == [("svcA", 500)]
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 2).text() == "true"
    assert dialog._table.item(0, 5).text() == "12.5"
    assert '"serviceId": "svcA"' in dialog._json_edit.toPlainText()

    dialog.close()
