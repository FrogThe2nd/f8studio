from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from qtpy import QtCore, QtWidgets

from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge
from f8pystudio.monitoring.service_rows import ServiceMonitorRow
from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio
from f8pystudio.ui.support.studio_theme import label_qss, studio_dark_theme
from f8pystudio.ui.support.ui_icons import StudioIcon, icon_for

logger = logging.getLogger(__name__)
_DEBUG_WIDGET_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True)
class AgentDebugServiceRow:
    service_id: str
    service_class: str
    running: bool
    alive: bool | None
    ready: bool | None
    active: bool | None
    latency_ms_p95: float | None
    errors: int | None
    current_error: str


class AgentDebugServiceTableModel(QtCore.QAbstractTableModel):
    _HEADERS = ("Service", "Class", "Run", "Alive", "Ready", "Active", "P95", "Err", "Current Error")

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[AgentDebugServiceRow] = []

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._HEADERS)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole) -> str | None:
        if role not in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            return None
        if not index.isValid():
            return None
        row_index = int(index.row())
        col = int(index.column())
        if row_index < 0 or row_index >= len(self._rows):
            return None
        if col < 0 or col >= len(self._HEADERS):
            return None
        return self._cell_text(self._rows[row_index], col)

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

    def update_rows(self, rows: list[AgentDebugServiceRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def service_id_at(self, row_index: int) -> str:
        if row_index < 0 or row_index >= len(self._rows):
            return ""
        return self._rows[row_index].service_id

    @staticmethod
    def _cell_text(row: AgentDebugServiceRow, col: int) -> str:
        if col == 0:
            return row.service_id
        if col == 1:
            return row.service_class or "-"
        if col == 2:
            return _bool_text(row.running)
        if col == 3:
            return _optional_bool_text(row.alive)
        if col == 4:
            return _optional_bool_text(row.ready)
        if col == 5:
            return _optional_bool_text(row.active)
        if col == 6:
            return "-" if row.latency_ms_p95 is None else f"{float(row.latency_ms_p95):.2f}"
        if col == 7:
            return "-" if row.errors is None else str(int(row.errors))
        if col == 8:
            return row.current_error or "-"
        return ""


class AgentDebugWidget(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        bridge: PyStudioServiceBridge,
        studio_graph: object,
        deploy_requested: Callable[[], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._studio_graph = studio_graph
        self._deploy_requested = deploy_requested
        self._selected_service_id = ""

        self._summary = QtWidgets.QLabel("Agent debug surface is ready.")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted, font_size_px=11))

        self._refresh_btn = QtWidgets.QToolButton(self)
        self._compile_btn = QtWidgets.QToolButton(self)
        self._deploy_btn = QtWidgets.QToolButton(self)
        for button in (self._refresh_btn, self._compile_btn, self._deploy_btn):
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self._refresh_btn.setText("Refresh")
        self._refresh_btn.setIcon(icon_for(self._refresh_btn, StudioIcon.REFRESH))
        self._refresh_btn.setToolTip("Refresh compile and runtime evidence")

        self._compile_btn.setText("Compile")
        self._compile_btn.setIcon(icon_for(self._compile_btn, StudioIcon.CODE))
        self._compile_btn.setToolTip("Compile current graph for agent debugging")

        self._deploy_btn.setText("Deploy")
        self._deploy_btn.setIcon(icon_for(self._deploy_btn, StudioIcon.SEND))
        self._deploy_btn.setToolTip("Deploy graph through the Studio runtime action")

        self._model = AgentDebugServiceTableModel(self)
        self._table = QtWidgets.QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._evidence = QtWidgets.QPlainTextEdit(self)
        self._evidence.setReadOnly(True)
        self._evidence.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self._evidence.setPlaceholderText("Compile/runtime evidence will appear here.")

        controls = QtWidgets.QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._compile_btn)
        controls.addWidget(self._deploy_btn)
        controls.addStretch(1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, self)
        splitter.addWidget(self._table)
        splitter.addWidget(self._evidence)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(controls)
        layout.addWidget(self._summary)
        layout.addWidget(splitter, 1)

        self._refresh_btn.clicked.connect(self.refresh)  # type: ignore[attr-defined]
        self._compile_btn.clicked.connect(self.compile_graph)  # type: ignore[attr-defined]
        self._deploy_btn.clicked.connect(self._deploy_requested)  # type: ignore[attr-defined]
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]

        self.refresh()

    @QtCore.Slot()
    def refresh(self) -> None:
        rows = [_row_from_service_monitor(row) for row in self._bridge.list_service_monitor_rows()]
        self._model.update_rows(rows)
        if rows and not self._selected_service_id:
            self._selected_service_id = rows[0].service_id
            self._table.selectRow(0)
        self._refresh_evidence()

    @QtCore.Slot()
    def compile_graph(self) -> None:
        try:
            compiled = compile_runtime_graphs_from_studio(self._studio_graph)
        except ValueError as exc:
            self._summary.setText(f"Compile blocked: {exc}")
            self._evidence.setPlainText(_json_text({"compile": {"valid": False, "error": str(exc)}}))
            return
        except _DEBUG_WIDGET_ERRORS as exc:
            logger.exception("Agent debug compile failed")
            self._summary.setText(f"Compile failed: {type(exc).__name__}: {exc}")
            self._evidence.setPlainText(_json_text({"compile": {"valid": False, "error": f"{type(exc).__name__}: {exc}"}}))
            return
        per_service = dict(compiled.per_service)
        service_ids = sorted(str(service_id) for service_id in per_service)
        global_graph = compiled.global_graph
        warnings = list(compiled.warnings)
        per_service_summaries = [
            {
                "serviceId": service_id,
                "nodeCount": len(list(per_service[service_id].nodes or ())),
                "edgeCount": len(list(per_service[service_id].edges or ())),
            }
            for service_id in service_ids
        ]
        payload = {
            "compile": {
                "valid": True,
                "warnings": warnings,
                "global": {
                    "serviceCount": len(list(global_graph.services or ())),
                    "nodeCount": len(list(global_graph.nodes or ())),
                    "edgeCount": len(list(global_graph.edges or ())),
                },
                "perServiceCount": len(per_service),
                "serviceIds": service_ids,
                "perService": per_service_summaries,
            }
        }
        self._summary.setText(f"Compile ok: {len(per_service)} service graph(s), {len(warnings)} warning(s).")
        self._evidence.setPlainText(_json_text(payload))

    @QtCore.Slot()
    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        if selected:
            self._selected_service_id = self._model.service_id_at(int(selected[0].row()))
        self._refresh_evidence()

    def _refresh_evidence(self) -> None:
        service_id = str(self._selected_service_id or "").strip()
        if not service_id:
            self._summary.setText("No service selected.")
            self._evidence.setPlainText(_json_text({"service": None}))
            return
        try:
            latest = self._bridge.get_latest_monitor_snapshot(service_id)
            stream = self._bridge.get_monitor_snapshot_stream(service_id, limit=12)
            payload = {
                "service": {
                    "serviceId": service_id,
                    "serviceClass": str(self._bridge.get_service_class(service_id)),
                    "running": bool(self._bridge.is_service_running(service_id)),
                    "active": self._bridge.get_cached_service_active(service_id),
                    "latestMonitor": latest,
                    "recentMonitorCount": len(stream),
                    "recentMonitor": stream,
                }
            }
        except _DEBUG_WIDGET_ERRORS as exc:
            logger.exception("Agent debug evidence refresh failed serviceId=%s", service_id)
            payload = {"service": {"serviceId": service_id, "error": f"{type(exc).__name__}: {exc}"}}
        self._summary.setText(f"Debug evidence for service `{service_id}`.")
        self._evidence.setPlainText(_json_text(payload))


def _row_from_service_monitor(row: ServiceMonitorRow) -> AgentDebugServiceRow:
    return AgentDebugServiceRow(
        service_id=str(row.service_id or ""),
        service_class=str(row.service_class or ""),
        running=bool(row.running),
        alive=row.alive,
        ready=row.ready,
        active=row.active,
        latency_ms_p95=row.latency_ms_p95,
        errors=row.error_count_window,
        current_error=_current_error_text(row),
    )


def _current_error_text(row: ServiceMonitorRow) -> str:
    message = str(row.current_error_message or "").strip()
    if not message:
        return ""
    node_id = str(row.current_error_node_id or "").strip()
    code = str(row.current_error_code or "").strip()
    prefix_parts = [part for part in (node_id, code) if part]
    prefix = " ".join(prefix_parts)
    return f"{prefix}: {message}" if prefix else message


def _bool_text(value: bool) -> str:
    return "yes" if bool(value) else "no"


def _optional_bool_text(value: bool | None) -> str:
    if value is None:
        return "-"
    return _bool_text(value)


def _json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
