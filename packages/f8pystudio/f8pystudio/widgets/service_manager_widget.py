from __future__ import annotations

from dataclasses import replace
from typing import Callable

from qtpy import QtCore, QtWidgets

from ..pystudio_service_bridge import PyStudioServiceBridge, ServiceMonitorRow
from ..ui_icons import StudioIcon, icon_for


class ServiceManagerWidget(QtWidgets.QWidget):
    _COL_SERVICE_ID = 0
    _COL_SERVICE_CLASS = 1
    _COL_RUNNING = 2
    _COL_ALIVE = 3
    _COL_READY = 4
    _COL_ACTIVE = 5
    _COL_CPU = 6
    _COL_RAM = 7
    _COL_GPU = 8
    _COL_WAIT_P95 = 9
    _COL_ERRORS = 10

    def __init__(
        self,
        *,
        bridge: PyStudioServiceBridge,
        get_declared_services: Callable[[], dict[str, str]] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._get_declared_services = get_declared_services
        self._rows_by_service_id: dict[str, ServiceMonitorRow] = {}
        self._refresh_queued = False

        self._refresh_btn = QtWidgets.QToolButton(self)
        self._toggle_btn = QtWidgets.QToolButton(self)
        self._stop_btn = QtWidgets.QToolButton(self)
        self._deploy_btn = QtWidgets.QToolButton(self)
        self._restart_btn = QtWidgets.QToolButton(self)

        self._refresh_btn.setAutoRaise(True)
        self._toggle_btn.setAutoRaise(True)
        self._stop_btn.setAutoRaise(True)
        self._deploy_btn.setAutoRaise(True)
        self._restart_btn.setAutoRaise(True)

        self._refresh_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._toggle_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._stop_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._deploy_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._restart_btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)

        self._refresh_btn.setIcon(icon_for(self, StudioIcon.REFRESH))
        self._toggle_btn.setIcon(icon_for(self, StudioIcon.PLAY))
        self._stop_btn.setIcon(icon_for(self, StudioIcon.STOP))
        self._deploy_btn.setIcon(icon_for(self, StudioIcon.TRANSFER))
        self._restart_btn.setIcon(icon_for(self, StudioIcon.RESTART))

        self._refresh_btn.setToolTip("Refresh service table")
        self._toggle_btn.setToolTip("Start service (deploy + activate)")
        self._stop_btn.setToolTip("Terminate service process")
        self._deploy_btn.setToolTip("Deploy current rungraph to service")
        self._restart_btn.setToolTip("Restart service (terminate + deploy + activate)")

        self._table = QtWidgets.QTableWidget(0, 11, self)
        self._table.setHorizontalHeaderLabels(
            [
                "Service ID",
                "Service Class",
                "Running",
                "Alive",
                "Ready",
                "Active",
                "CPU%",
                "RAM(MB)",
                "GPU%",
                "WaitP95(ms)",
                "Errors",
            ]
        )
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(52)
        self._apply_default_column_widths()

        controls = QtWidgets.QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._toggle_btn)
        controls.addWidget(self._stop_btn)
        controls.addWidget(self._deploy_btn)
        controls.addWidget(self._restart_btn)
        controls.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addLayout(controls)
        layout.addWidget(self._table)

        self._refresh_btn.clicked.connect(self.refresh)  # type: ignore[attr-defined]
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)  # type: ignore[attr-defined]
        self._stop_btn.clicked.connect(self._on_stop_clicked)  # type: ignore[attr-defined]
        self._deploy_btn.clicked.connect(self._on_deploy_clicked)  # type: ignore[attr-defined]
        self._restart_btn.clicked.connect(self._on_restart_clicked)  # type: ignore[attr-defined]
        self._table.itemSelectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self.refresh)  # type: ignore[attr-defined]
        self._poll_timer.start()

        self.refresh()

    def queue_refresh(self) -> None:
        if self._refresh_queued:
            return
        self._refresh_queued = True
        QtCore.QTimer.singleShot(0, self._flush_refresh_queue)

    def _flush_refresh_queue(self) -> None:
        self._refresh_queued = False
        self.refresh()

    @staticmethod
    def _format_bool(value: bool | None) -> str:
        if value is None:
            return "-"
        return "true" if bool(value) else "false"

    @staticmethod
    def _format_float(value: float | None, *, digits: int = 1) -> str:
        if value is None:
            return "-"
        return f"{float(value):.{int(digits)}f}"

    @staticmethod
    def _format_ram_mb(value: int | None) -> str:
        if value is None:
            return "-"
        return f"{float(value) / 1024.0 / 1024.0:.1f}"

    @staticmethod
    def _set_item(table: QtWidgets.QTableWidget, row: int, column: int, text: str) -> None:
        item = QtWidgets.QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        item.setToolTip(str(text))
        table.setItem(row, column, item)

    def _apply_default_column_widths(self) -> None:
        self._table.setColumnWidth(self._COL_SERVICE_ID, 180)
        self._table.setColumnWidth(self._COL_SERVICE_CLASS, 190)
        self._table.setColumnWidth(self._COL_RUNNING, 70)
        self._table.setColumnWidth(self._COL_ALIVE, 70)
        self._table.setColumnWidth(self._COL_READY, 70)
        self._table.setColumnWidth(self._COL_ACTIVE, 70)
        self._table.setColumnWidth(self._COL_CPU, 78)
        self._table.setColumnWidth(self._COL_RAM, 95)
        self._table.setColumnWidth(self._COL_GPU, 78)
        self._table.setColumnWidth(self._COL_WAIT_P95, 102)
        self._table.setColumnWidth(self._COL_ERRORS, 72)

    def _selected_service_id(self) -> str:
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            return ""
        row_index = int(selected[0].row())
        item = self._table.item(row_index, 0)
        if item is None:
            return ""
        return str(item.text() or "").strip()

    def _selected_row(self) -> ServiceMonitorRow | None:
        service_id = self._selected_service_id()
        if not service_id:
            return None
        return self._rows_by_service_id.get(service_id)

    def _declared_services(self) -> dict[str, str]:
        callback = self._get_declared_services
        if callback is None:
            return {}
        try:
            payload = callback()
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for service_id, service_class in payload.items():
            sid = str(service_id or "").strip()
            svc_class = str(service_class or "").strip()
            if sid:
                out[sid] = svc_class
        return out

    def _rows_for_declared_services(
        self,
        *,
        bridge_rows: list[ServiceMonitorRow],
        declared_services: dict[str, str],
    ) -> list[ServiceMonitorRow]:
        by_id: dict[str, ServiceMonitorRow] = {}
        for row in bridge_rows:
            by_id[row.service_id] = row

        rows: list[ServiceMonitorRow] = []
        for service_id in sorted(declared_services.keys()):
            service_class = str(declared_services[service_id] or "").strip()
            row = by_id.get(service_id)
            if row is None:
                rows.append(
                    ServiceMonitorRow(
                        service_id=service_id,
                        service_class=service_class,
                        running=bool(self._bridge.is_service_running(service_id)),
                        alive=None,
                        ready=None,
                        active=self._bridge.get_cached_service_active(service_id),
                        cpu_process_percent=None,
                        memory_rss_bytes=None,
                        gpu_util_percent=None,
                        wait_ms_p95=None,
                        error_count_window=None,
                        latest_snapshot=None,
                    )
                )
                continue
            if not row.service_class and service_class:
                row = replace(row, service_class=service_class)
            rows.append(row)
        return rows

    def _update_action_state(self) -> None:
        selected = self._selected_row()
        if selected is None:
            self._toggle_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._deploy_btn.setEnabled(False)
            self._restart_btn.setEnabled(False)
            return

        running = bool(selected.running)
        has_class = bool(str(selected.service_class or "").strip())
        active = selected.active

        self._toggle_btn.setEnabled(has_class or running)
        self._stop_btn.setEnabled(running)
        self._deploy_btn.setEnabled(running)
        self._restart_btn.setEnabled(running and has_class)
        if not running:
            self._toggle_btn.setIcon(icon_for(self, StudioIcon.PLAY))
            self._toggle_btn.setToolTip("Start service (deploy + activate)")
            return

        if active is False:
            self._toggle_btn.setIcon(icon_for(self, StudioIcon.PLAY))
            self._toggle_btn.setToolTip("Activate service")
            return

        self._toggle_btn.setIcon(icon_for(self, StudioIcon.PAUSE))
        if active is True:
            self._toggle_btn.setToolTip("Deactivate service")
            return
        self._toggle_btn.setToolTip("Deactivate service")

    @QtCore.Slot()
    def refresh(self) -> None:
        previous_service_id = self._selected_service_id()
        declared_services = self._declared_services()
        rows = self._rows_for_declared_services(
            bridge_rows=self._bridge.list_service_monitor_rows(),
            declared_services=declared_services,
        )
        self._rows_by_service_id = {row.service_id: row for row in rows}

        self._table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            self._bridge.request_service_status(row.service_id)
            self._set_item(self._table, row_index, self._COL_SERVICE_ID, row.service_id)
            self._set_item(self._table, row_index, self._COL_SERVICE_CLASS, row.service_class)
            self._set_item(self._table, row_index, self._COL_RUNNING, self._format_bool(row.running))
            self._set_item(self._table, row_index, self._COL_ALIVE, self._format_bool(row.alive))
            self._set_item(self._table, row_index, self._COL_READY, self._format_bool(row.ready))
            self._set_item(self._table, row_index, self._COL_ACTIVE, self._format_bool(row.active))
            self._set_item(self._table, row_index, self._COL_CPU, self._format_float(row.cpu_process_percent))
            self._set_item(self._table, row_index, self._COL_RAM, self._format_ram_mb(row.memory_rss_bytes))
            self._set_item(self._table, row_index, self._COL_GPU, self._format_float(row.gpu_util_percent))
            self._set_item(self._table, row_index, self._COL_WAIT_P95, self._format_float(row.wait_ms_p95, digits=2))
            error_count = "-" if row.error_count_window is None else str(int(row.error_count_window))
            self._set_item(self._table, row_index, self._COL_ERRORS, error_count)

        if previous_service_id:
            for row_index in range(self._table.rowCount()):
                row_item = self._table.item(row_index, self._COL_SERVICE_ID)
                if row_item is None:
                    continue
                if str(row_item.text() or "").strip() == previous_service_id:
                    self._table.selectRow(row_index)
                    break
        elif self._table.rowCount() > 0:
            self._table.selectRow(0)

        self._update_action_state()

    @QtCore.Slot()
    def _on_selection_changed(self) -> None:
        self._update_action_state()

    @QtCore.Slot()
    def _on_toggle_clicked(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        if not bool(row.running):
            if not row.service_class:
                return
            self._bridge.start_service_and_deploy(row.service_id, service_class=row.service_class)
            self.queue_refresh()
            return
        if row.active is False:
            self._bridge.set_service_active(row.service_id, True)
            self.queue_refresh()
            return
        self._bridge.set_service_active(row.service_id, False)
        self.queue_refresh()

    @QtCore.Slot()
    def _on_stop_clicked(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._bridge.stop_service(row.service_id)
        self.queue_refresh()

    @QtCore.Slot()
    def _on_deploy_clicked(self) -> None:
        row = self._selected_row()
        if row is None or not bool(row.running):
            return
        self._bridge.deploy_service_rungraph(row.service_id)
        self.queue_refresh()

    @QtCore.Slot()
    def _on_restart_clicked(self) -> None:
        row = self._selected_row()
        if row is None or not row.service_class:
            return
        self._bridge.restart_service_and_deploy(row.service_id, service_class=row.service_class)
        self.queue_refresh()
