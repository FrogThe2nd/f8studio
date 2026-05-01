from __future__ import annotations

from datetime import datetime
import json
import logging
import time
from typing import Any, Protocol

from qtpy import QtCore, QtWidgets

logger = logging.getLogger(__name__)


class MonitorStreamBridge(Protocol):
    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, Any]]: ...


class MonitorStreamDialog(QtWidgets.QDialog):
    _MAX_SAMPLES = 500
    _POLL_INTERVAL_MS = 1000
    _HEADERS = (
        "Time",
        "Age",
        "Alive",
        "Ready",
        "Active",
        "CPU%",
        "RAM(MB)",
        "LatencyP95(ms)",
        "Errors",
        "Message",
    )

    def __init__(
        self,
        *,
        bridge: MonitorStreamBridge,
        service_id: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._service_id = str(service_id or "").strip()
        self._snapshots: list[dict[str, Any]] = []
        self._last_error_signature = ""

        self.setWindowTitle(f"Monitor Stream - {self._service_id or '<service>'}")
        self.resize(980, 680)

        self._status_label = QtWidgets.QLabel(self)
        self._status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        self._refresh_btn = QtWidgets.QPushButton("Refresh", self)
        self._auto_refresh = QtWidgets.QCheckBox("Auto refresh", self)
        self._auto_refresh.setChecked(True)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self._status_label, 1)
        header_layout.addWidget(self._auto_refresh)
        header_layout.addWidget(self._refresh_btn)

        self._table = QtWidgets.QTableWidget(self)
        self._table.setColumnCount(len(self._HEADERS))
        self._table.setHorizontalHeaderLabels(list(self._HEADERS))
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        self._table.setAlternatingRowColors(True)

        self._json_edit = QtWidgets.QPlainTextEdit(self)
        self._json_edit.setReadOnly(True)
        self._json_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, self)
        splitter.addWidget(self._table)
        splitter.addWidget(self._json_edit)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self._copy_json_btn = QtWidgets.QPushButton("Copy JSON", self)
        close_btn = QtWidgets.QPushButton("Close", self)
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self._copy_json_btn)
        button_layout.addStretch(1)
        button_layout.addWidget(close_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(header_layout)
        layout.addWidget(splitter, 1)
        layout.addLayout(button_layout)

        self._refresh_btn.clicked.connect(self.refresh)  # type: ignore[attr-defined]
        self._auto_refresh.toggled.connect(self._on_auto_refresh_toggled)  # type: ignore[attr-defined]
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)  # type: ignore[attr-defined]
        self._copy_json_btn.clicked.connect(self._copy_selected_json)  # type: ignore[attr-defined]
        close_btn.clicked.connect(self.close)  # type: ignore[attr-defined]

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self._POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)  # type: ignore[attr-defined]
        self._timer.start()

        self.refresh()

    @staticmethod
    def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
        value = payload.get(name)
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _str_value(value: Any) -> str:
        if value is None:
            return "-"
        text = str(value)
        if not text:
            return "-"
        return text

    @staticmethod
    def _bool_text(value: Any) -> str:
        if value is None:
            return "-"
        return "true" if bool(value) else "false"

    @staticmethod
    def _float_text(value: Any, *, digits: int = 1) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.{int(digits)}f}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _int_text(value: Any) -> str:
        if value is None:
            return "-"
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return "-"

    @classmethod
    def _ram_mb_text(cls, value: Any) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value) / 1024.0 / 1024.0:.1f}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _timestamp_ms(payload: dict[str, Any]) -> int:
        raw_ts = payload.get("tsMs")
        try:
            return int(raw_ts)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _time_text(ts_ms: int) -> str:
        if ts_ms <= 0:
            return "-"
        dt = datetime.fromtimestamp(float(ts_ms) / 1000.0)
        return dt.strftime("%H:%M:%S.%f")[:-3]

    @staticmethod
    def _age_text(ts_ms: int) -> str:
        if ts_ms <= 0:
            return "-"
        age_ms = max(0, int(time.time() * 1000.0) - int(ts_ms))
        if age_ms < 1000:
            return f"{age_ms} ms"
        if age_ms < 60_000:
            return f"{age_ms / 1000.0:.1f} s"
        return f"{age_ms / 60_000.0:.1f} min"

    @classmethod
    def _row_texts(cls, payload: dict[str, Any]) -> tuple[str, ...]:
        ts_ms = cls._timestamp_ms(payload)
        memory = cls._section(payload, "memory")
        cpu = cls._section(payload, "cpu")
        timing = cls._section(payload, "timing")
        error = cls._section(payload, "error")
        return (
            cls._time_text(ts_ms),
            cls._age_text(ts_ms),
            cls._bool_text(payload.get("alive")),
            cls._bool_text(payload.get("ready")),
            cls._bool_text(payload.get("active")),
            cls._float_text(cpu.get("processPercent"), digits=1),
            cls._ram_mb_text(memory.get("rssBytes")),
            cls._float_text(timing.get("latencyMsP95"), digits=2),
            cls._int_text(error.get("countWindow")),
            cls._str_value(error.get("currentMessage") or error.get("lastMessage")),
        )

    def _selected_ts_ms(self) -> int:
        row = int(self._table.currentRow())
        if row < 0 or row >= len(self._snapshots):
            return 0
        return self._timestamp_ms(self._snapshots[row])

    def _set_status_for_samples(self) -> None:
        count = len(self._snapshots)
        if count <= 0:
            self._status_label.setText(f"{self._service_id}: no monitor samples")
            return
        latest_ts = self._timestamp_ms(self._snapshots[-1])
        self._status_label.setText(
            f"{self._service_id}: {count} sample(s), latest {self._time_text(latest_ts)} ({self._age_text(latest_ts)})"
        )

    def _populate_table(self, previous_ts_ms: int) -> None:
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(self._snapshots))
            for row_index, payload in enumerate(self._snapshots):
                for column_index, text in enumerate(self._row_texts(payload)):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
                    self._table.setItem(row_index, column_index, item)

            target_row = len(self._snapshots) - 1
            if previous_ts_ms > 0:
                for row_index, payload in enumerate(self._snapshots):
                    if self._timestamp_ms(payload) == previous_ts_ms:
                        target_row = row_index
                        break
            if target_row >= 0:
                self._table.selectRow(target_row)
            else:
                self._json_edit.setPlainText("")
        finally:
            self._table.setUpdatesEnabled(True)

    @QtCore.Slot()
    def refresh(self) -> None:
        if not self._service_id:
            self._status_label.setText("Missing service id")
            return
        previous_ts_ms = self._selected_ts_ms()
        try:
            self._snapshots = self._bridge.get_monitor_snapshot_stream(
                self._service_id,
                limit=self._MAX_SAMPLES,
            )
        except Exception as exc:
            signature = f"{type(exc).__name__}:{exc}"
            if signature != self._last_error_signature:
                logger.exception("Failed to refresh monitor stream for serviceId=%s", self._service_id)
                self._last_error_signature = signature
            self._status_label.setText(f"{self._service_id}: monitor stream unavailable ({type(exc).__name__})")
            return

        self._last_error_signature = ""
        self._populate_table(previous_ts_ms)
        self._set_status_for_samples()
        self._on_table_selection_changed()

    @QtCore.Slot(bool)
    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        if bool(checked):
            self._timer.start()
            return
        self._timer.stop()

    @QtCore.Slot()
    def _on_table_selection_changed(self) -> None:
        row = int(self._table.currentRow())
        if row < 0 or row >= len(self._snapshots):
            self._json_edit.setPlainText("")
            return
        self._json_edit.setPlainText(json.dumps(self._snapshots[row], indent=2, sort_keys=True, ensure_ascii=False))

    @QtCore.Slot()
    def _copy_selected_json(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self._json_edit.toPlainText())


_OPEN_DIALOGS: dict[tuple[int, int, str], MonitorStreamDialog] = {}


def open_monitor_stream_dialog(
    *,
    parent: QtWidgets.QWidget | None,
    bridge: MonitorStreamBridge,
    service_id: str,
) -> MonitorStreamDialog:
    sid = str(service_id or "").strip()
    key = (id(parent) if parent is not None else 0, id(bridge), sid)
    existing = _OPEN_DIALOGS.get(key)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing

    dialog = MonitorStreamDialog(parent=parent, bridge=bridge, service_id=sid)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def _drop_dialog(_obj: object | None = None, *, dialog_key: tuple[int, int, str] = key) -> None:
        _ = _obj
        _OPEN_DIALOGS.pop(dialog_key, None)

    dialog.destroyed.connect(_drop_dialog)  # type: ignore[attr-defined]
    _OPEN_DIALOGS[key] = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


__all__ = ["MonitorStreamBridge", "MonitorStreamDialog", "open_monitor_stream_dialog"]
