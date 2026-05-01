from __future__ import annotations

from dataclasses import replace
from typing import Callable

from qtpy import QtCore, QtGui, QtWidgets

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID
from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge, ServiceMonitorRow
from ...ui.dialogs.monitor_stream_dialog import open_monitor_stream_dialog
from ...ui.support.ui_icons import StudioIcon, icon_for


class _ServiceMonitorTableModel(QtCore.QAbstractTableModel):
    SORT_ROLE = QtCore.Qt.UserRole + 1
    _HEADERS = (
        "Service ID",
        "Service Class",
        "Running",
        "Alive",
        "Ready",
        "Active",
        "CPU%",
        "RAM(MB)",
        "GPU%",
        "LatencyP95(ms)",
        "Errors",
        "Current Error",
    )

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ServiceMonitorRow] = []

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
    def _format_current_error(row: ServiceMonitorRow) -> str:
        if not row.current_error_message:
            return "-"
        severity = str(row.current_error_severity or "error").upper()
        code = str(row.current_error_code or "").strip()
        node_id = str(row.current_error_node_id or row.service_id).strip()
        prefix = f"{node_id} {severity}"
        if code:
            prefix = f"{prefix} {code}"
        message = str(row.current_error_message)
        if len(message) > 96:
            message = message[:93].rstrip() + "..."
        return f"{prefix}: {message}"

    @classmethod
    def row_texts(cls, row: ServiceMonitorRow) -> tuple[str, ...]:
        error_count = "-" if row.error_count_window is None else str(int(row.error_count_window))
        return (
            row.service_id,
            row.service_class,
            cls._format_bool(row.running),
            cls._format_bool(row.alive),
            cls._format_bool(row.ready),
            cls._format_bool(row.active),
            cls._format_float(row.cpu_process_percent),
            cls._format_ram_mb(row.memory_rss_bytes),
            cls._format_float(row.gpu_util_percent),
            cls._format_float(row.latency_ms_p95, digits=2),
            error_count,
            cls._format_current_error(row),
        )

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._HEADERS)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole) -> str | None:
        if not index.isValid():
            return None
        row_index = int(index.row())
        column_index = int(index.column())
        if row_index < 0 or row_index >= len(self._rows):
            return None
        if column_index < 0 or column_index >= len(self._HEADERS):
            return None
        text = self.row_texts(self._rows[row_index])[column_index]
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            return text
        if role == self.SORT_ROLE:
            return self._sort_value(self._rows[row_index], column_index)
        return None

    @staticmethod
    def _sort_value(row: ServiceMonitorRow, column_index: int) -> object:
        if column_index == 0:
            return str(row.service_id or "")
        if column_index == 1:
            return str(row.service_class or "")
        if column_index == 2:
            return _ServiceMonitorTableModel._sort_optional_bool(row.running)
        if column_index == 3:
            return _ServiceMonitorTableModel._sort_optional_bool(row.alive)
        if column_index == 4:
            return _ServiceMonitorTableModel._sort_optional_bool(row.ready)
        if column_index == 5:
            return _ServiceMonitorTableModel._sort_optional_bool(row.active)
        if column_index == 6:
            return _ServiceMonitorTableModel._sort_optional_float(row.cpu_process_percent)
        if column_index == 7:
            return _ServiceMonitorTableModel._sort_optional_int(row.memory_rss_bytes)
        if column_index == 8:
            return _ServiceMonitorTableModel._sort_optional_float(row.gpu_util_percent)
        if column_index == 9:
            return _ServiceMonitorTableModel._sort_optional_float(row.latency_ms_p95)
        if column_index == 10:
            return _ServiceMonitorTableModel._sort_optional_int(row.error_count_window)
        if column_index == 11:
            return str(row.current_error_message or "")
        return ""

    @staticmethod
    def _sort_optional_bool(value: bool | None) -> tuple[int, int]:
        if value is None:
            return (1, 0)
        return (0, int(bool(value)))

    @staticmethod
    def _sort_optional_int(value: int | None) -> tuple[int, int]:
        if value is None:
            return (1, 0)
        return (0, int(value))

    @staticmethod
    def _sort_optional_float(value: float | None) -> tuple[int, float]:
        if value is None:
            return (1, 0.0)
        return (0, float(value))

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.DisplayRole,
    ) -> str | None:
        if role != QtCore.Qt.DisplayRole:
            return None
        if orientation != QtCore.Qt.Horizontal:
            return None
        if section < 0 or section >= len(self._HEADERS):
            return None
        return self._HEADERS[section]

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlags:
        if not index.isValid():
            return QtCore.Qt.NoItemFlags
        return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable

    def row_for_service_id(self, service_id: str) -> ServiceMonitorRow | None:
        sid = str(service_id or "").strip()
        for row in self._rows:
            if row.service_id == sid:
                return row
        return None

    def service_id_at_row(self, row_index: int) -> str:
        if row_index < 0 or row_index >= len(self._rows):
            return ""
        return str(self._rows[row_index].service_id or "").strip()

    def update_rows(self, rows: list[ServiceMonitorRow]) -> None:
        desired_rows = list(rows)
        desired_service_ids = [row.service_id for row in desired_rows]
        desired_service_id_set = set(desired_service_ids)

        for row_index in range(len(self._rows) - 1, -1, -1):
            if self._rows[row_index].service_id in desired_service_id_set:
                continue
            self.beginRemoveRows(QtCore.QModelIndex(), row_index, row_index)
            del self._rows[row_index]
            self.endRemoveRows()

        for target_index, desired_row in enumerate(desired_rows):
            desired_service_id = desired_row.service_id
            if target_index < len(self._rows) and self._rows[target_index].service_id == desired_service_id:
                continue

            current_index = -1
            for scan_index, existing_row in enumerate(self._rows):
                if existing_row.service_id == desired_service_id:
                    current_index = scan_index
                    break

            if current_index < 0:
                self.beginInsertRows(QtCore.QModelIndex(), target_index, target_index)
                self._rows.insert(target_index, desired_row)
                self.endInsertRows()
                continue

            destination_child = target_index
            if current_index < target_index:
                destination_child = target_index + 1
            self.beginMoveRows(
                QtCore.QModelIndex(),
                current_index,
                current_index,
                QtCore.QModelIndex(),
                destination_child,
            )
            moved_row = self._rows.pop(current_index)
            self._rows.insert(target_index, moved_row)
            self.endMoveRows()

        for row_index, desired_row in enumerate(desired_rows):
            if row_index >= len(self._rows):
                break
            current_row = self._rows[row_index]
            if current_row == desired_row:
                continue
            current_texts = self.row_texts(current_row)
            desired_texts = self.row_texts(desired_row)
            self._rows[row_index] = desired_row

            changed_columns = [
                column_index
                for column_index, (current_text, desired_text) in enumerate(zip(current_texts, desired_texts))
                if current_text != desired_text
            ]
            if not changed_columns:
                continue
            first_column = min(changed_columns)
            last_column = max(changed_columns)
            top_left = self.index(row_index, first_column)
            bottom_right = self.index(row_index, last_column)
            self.dataChanged.emit(top_left, bottom_right, [QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole])


class _ServiceMonitorSortProxyModel(QtCore.QSortFilterProxyModel):
    def lessThan(self, source_left: QtCore.QModelIndex, source_right: QtCore.QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return super().lessThan(source_left, source_right)

        left_value = model.data(source_left, _ServiceMonitorTableModel.SORT_ROLE)
        right_value = model.data(source_right, _ServiceMonitorTableModel.SORT_ROLE)
        if left_value is None or right_value is None:
            return super().lessThan(source_left, source_right)
        return left_value < right_value


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
    _COL_LATENCY_P95 = 9
    _COL_ERRORS = 10
    _DEFAULT_COLUMN_WIDTHS = (180, 190, 70, 70, 70, 70, 78, 95, 78, 118, 72)
    _MIN_COLUMN_WIDTHS = (120, 130, 56, 56, 56, 56, 62, 72, 62, 84, 56)

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

        self._model = _ServiceMonitorTableModel(self)
        self._proxy_model = _ServiceMonitorSortProxyModel(self)
        self._proxy_model.setSourceModel(self._model)
        self._proxy_model.setSortRole(_ServiceMonitorTableModel.SORT_ROLE)
        self._proxy_model.setDynamicSortFilter(True)
        self._table = QtWidgets.QTableView(self)
        self._table.setModel(self._proxy_model)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(52)
        self._table.setSortingEnabled(True)
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
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]
        self._table.customContextMenuRequested.connect(self._on_table_context_menu_requested)  # type: ignore[attr-defined]

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

    def _apply_default_column_widths(self) -> None:
        for index, width in enumerate(self._DEFAULT_COLUMN_WIDTHS):
            self._table.setColumnWidth(index, width)

    @classmethod
    def _target_column_widths_for_available_width(cls, available_width: int) -> tuple[int, ...]:
        target_width = max(int(available_width), 0)
        defaults = cls._DEFAULT_COLUMN_WIDTHS
        minimums = cls._MIN_COLUMN_WIDTHS
        total_default = sum(defaults)
        if target_width >= total_default:
            return defaults
        total_minimum = sum(minimums)
        if target_width <= total_minimum:
            return minimums

        shrink_target = total_default - target_width
        capacities = [defaults[index] - minimums[index] for index in range(len(defaults))]
        total_capacity = sum(capacities)
        if total_capacity <= 0:
            return minimums

        shrink_values: list[int] = [0] * len(defaults)
        remainders_with_index: list[tuple[int, int]] = []
        assigned_shrink = 0
        for index, capacity in enumerate(capacities):
            scaled = shrink_target * capacity
            shrink = scaled // total_capacity
            remainder = scaled % total_capacity
            shrink_values[index] = int(shrink)
            assigned_shrink += int(shrink)
            remainders_with_index.append((int(remainder), index))

        remainders_with_index.sort(key=lambda item: item[0], reverse=True)
        remaining = shrink_target - assigned_shrink
        ranked_index = 0
        while remaining > 0:
            if ranked_index >= len(remainders_with_index):
                ranked_index = 0
            _, column_index = remainders_with_index[ranked_index]
            ranked_index += 1
            if shrink_values[column_index] >= capacities[column_index]:
                continue
            shrink_values[column_index] += 1
            remaining -= 1

        out: list[int] = []
        for index, default_width in enumerate(defaults):
            out.append(default_width - shrink_values[index])
        return tuple(out)

    def _apply_adaptive_column_widths(self) -> None:
        available_width = self._table.viewport().width()
        target_widths = self._target_column_widths_for_available_width(available_width)
        for index, width in enumerate(target_widths):
            if self._table.columnWidth(index) == width:
                continue
            self._table.setColumnWidth(index, width)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_adaptive_column_widths()

    def _selected_service_id(self) -> str:
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            return ""
        source_index = self._proxy_model.mapToSource(selected[0])
        if not source_index.isValid():
            return ""
        return self._model.service_id_at_row(int(source_index.row()))

    def _selected_row(self) -> ServiceMonitorRow | None:
        service_id = self._selected_service_id()
        if not service_id:
            return None
        return self._rows_by_service_id.get(service_id)

    def _select_table_row_at_pos(self, pos: QtCore.QPoint) -> ServiceMonitorRow | None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return self._selected_row()
        self._table.setCurrentIndex(index)
        self._table.selectionModel().select(
            index,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )
        return self._selected_row()

    def _build_table_context_menu(self, row: ServiceMonitorRow | None) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self._table)
        view_stream_action = menu.addAction(icon_for(self, StudioIcon.SERVICE_MONITOR), "View Monitor Stream...")
        view_stream_action.setEnabled(row is not None and bool(str(row.service_id or "").strip()))
        view_stream_action.triggered.connect(self._open_selected_monitor_stream)  # type: ignore[attr-defined]
        return menu

    @QtCore.Slot(QtCore.QPoint)
    def _on_table_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        row = self._select_table_row_at_pos(pos)
        menu = self._build_table_context_menu(row)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    @QtCore.Slot()
    def _open_selected_monitor_stream(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        service_id = str(row.service_id or "").strip()
        if not service_id:
            return
        open_monitor_stream_dialog(
            parent=self.window() if isinstance(self.window(), QtWidgets.QWidget) else self,
            bridge=self._bridge,
            service_id=service_id,
        )

    @staticmethod
    def _restore_scrollbar_value(scrollbar: QtWidgets.QScrollBar, value: int) -> None:
        clamped_value = max(0, min(int(value), scrollbar.maximum()))
        scrollbar.setValue(clamped_value)

    def _select_service_id(self, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        for row_index in range(self._model.rowCount()):
            if self._model.service_id_at_row(row_index) != sid:
                continue
            source_index = self._model.index(row_index, self._COL_SERVICE_ID)
            if not source_index.isValid():
                return
            index = self._proxy_model.mapFromSource(source_index)
            if not index.isValid():
                return
            selection_model = self._table.selectionModel()
            selection_model.select(
                index,
                QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
            )
            self._table.setCurrentIndex(index)
            return

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
        all_service_ids = set(by_id.keys())
        all_service_ids.update(declared_services.keys())
        for service_id in sorted(all_service_ids):
            service_class = str(declared_services.get(service_id, "") or "").strip()
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
                        latency_ms_p95=None,
                        wait_ms_p95=None,
                        error_count_window=None,
                        current_error_node_id="",
                        current_error_code="",
                        current_error_message="",
                        current_error_severity="",
                        current_error_ts_ms=None,
                        latest_snapshot=None,
                    )
                )
                continue
            if not row.service_class and service_class:
                row = replace(row, service_class=service_class)
            rows.append(row)
        return rows

    @staticmethod
    def _is_studio_service_row(row: ServiceMonitorRow | None) -> bool:
        if row is None:
            return False
        service_id = str(row.service_id or "").strip()
        service_class = str(row.service_class or "").strip()
        return service_id == STUDIO_SERVICE_ID or service_class == STUDIO_SERVICE_CLASS

    def _update_action_state(self) -> None:
        selected = self._selected_row()
        if selected is None:
            self._toggle_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._deploy_btn.setEnabled(False)
            self._restart_btn.setEnabled(False)
            return

        if self._is_studio_service_row(selected):
            self._toggle_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._deploy_btn.setEnabled(False)
            self._restart_btn.setEnabled(False)
            self._toggle_btn.setIcon(icon_for(self, StudioIcon.PLAY))
            self._toggle_btn.setToolTip("Built-in studio service is monitored here and cannot be controlled")
            self._stop_btn.setToolTip("Built-in studio service is monitored here and cannot be controlled")
            self._deploy_btn.setToolTip("Built-in studio service is monitored here and cannot be controlled")
            self._restart_btn.setToolTip("Built-in studio service is monitored here and cannot be controlled")
            return

        self._stop_btn.setToolTip("Terminate service process")
        self._deploy_btn.setToolTip("Deploy current rungraph to service")
        self._restart_btn.setToolTip("Restart service (terminate + deploy + activate)")
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
        vertical_scrollbar = self._table.verticalScrollBar()
        horizontal_scrollbar = self._table.horizontalScrollBar()
        previous_vertical_scroll = int(vertical_scrollbar.value())
        previous_horizontal_scroll = int(horizontal_scrollbar.value())
        declared_services = self._declared_services()
        rows = self._rows_for_declared_services(
            bridge_rows=self._bridge.list_service_monitor_rows(),
            declared_services=declared_services,
        )
        self._rows_by_service_id = {row.service_id: row for row in rows}

        self._table.setUpdatesEnabled(False)
        try:
            for row in rows:
                self._bridge.request_service_status(row.service_id)
            self._model.update_rows(rows)

            if previous_service_id:
                self._select_service_id(previous_service_id)
            elif self._proxy_model.rowCount() > 0:
                index = self._proxy_model.index(0, self._COL_SERVICE_ID)
                self._table.selectionModel().select(
                    index,
                    QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
                )
                self._table.setCurrentIndex(index)

            self._apply_adaptive_column_widths()
            self._restore_scrollbar_value(vertical_scrollbar, previous_vertical_scroll)
            self._restore_scrollbar_value(horizontal_scrollbar, previous_horizontal_scroll)
            self._update_action_state()
        finally:
            self._table.setUpdatesEnabled(True)

    @QtCore.Slot()
    def _on_selection_changed(self) -> None:
        self._update_action_state()

    @QtCore.Slot()
    def _on_toggle_clicked(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        if self._is_studio_service_row(row):
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
        if self._is_studio_service_row(row):
            return
        self._bridge.stop_service(row.service_id)
        self.queue_refresh()

    @QtCore.Slot()
    def _on_deploy_clicked(self) -> None:
        row = self._selected_row()
        if row is None or self._is_studio_service_row(row) or not bool(row.running):
            return
        self._bridge.deploy_service_rungraph(row.service_id)
        self.queue_refresh()

    @QtCore.Slot()
    def _on_restart_clicked(self) -> None:
        row = self._selected_row()
        if row is None or self._is_studio_service_row(row) or not row.service_class:
            return
        self._bridge.restart_service_and_deploy(row.service_id, service_class=row.service_class)
        self.queue_refresh()
