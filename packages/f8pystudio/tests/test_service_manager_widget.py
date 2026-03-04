from __future__ import annotations

import os
import sys

from qtpy import QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.pystudio_service_bridge import ServiceMonitorRow  # noqa: E402
from f8pystudio.widgets.service_manager_widget import ServiceManagerWidget  # noqa: E402


class _FakeBridge:
    def __init__(self, rows: list[ServiceMonitorRow]) -> None:
        self._rows = list(rows)
        self.status_requests: list[str] = []
        self.start_calls: list[tuple[str, str]] = []
        self.active_calls: list[tuple[str, bool]] = []
        self.deploy_calls: list[str] = []

    def list_service_monitor_rows(self) -> list[ServiceMonitorRow]:
        return list(self._rows)

    def request_service_status(self, service_id: str) -> None:
        self.status_requests.append(str(service_id))

    def is_service_running(self, service_id: str) -> bool:
        sid = str(service_id)
        for row in self._rows:
            if row.service_id == sid:
                return bool(row.running)
        return False

    def get_cached_service_active(self, service_id: str) -> bool | None:
        sid = str(service_id)
        for row in self._rows:
            if row.service_id == sid:
                return row.active
        return None

    def start_service_and_deploy(self, service_id: str, *, service_class: str) -> None:
        self.start_calls.append((str(service_id), str(service_class)))

    def stop_service(self, service_id: str) -> None:
        _ = service_id

    def restart_service_and_deploy(self, service_id: str, *, service_class: str) -> None:
        _ = (service_id, service_class)

    def set_service_active(self, service_id: str, active: bool) -> None:
        self.active_calls.append((str(service_id), bool(active)))

    def deploy_service_rungraph(self, service_id: str) -> None:
        self.deploy_calls.append(str(service_id))


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _row(*, running: bool, active: bool | None, service_class: str = "") -> ServiceMonitorRow:
    return ServiceMonitorRow(
        service_id="svcA",
        service_class=service_class,
        running=running,
        alive=None,
        ready=None,
        active=active,
        cpu_process_percent=None,
        memory_rss_bytes=None,
        gpu_util_percent=None,
        latency_ms_p95=None,
        wait_ms_p95=None,
        error_count_window=None,
        latest_snapshot=None,
    )


def test_service_class_falls_back_to_declared_graph_service() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=False, active=None, service_class="")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    assert widget._table.rowCount() == 1
    class_cell = widget._table.item(0, 1)
    assert class_cell is not None
    assert class_cell.text() == "f8.tests.a"
    assert widget._toggle_btn.isEnabled() is True
    assert widget._stop_btn.isEnabled() is False
    assert widget._deploy_btn.isEnabled() is False
    assert widget._restart_btn.isEnabled() is False
    assert widget._toggle_btn.toolTip() == "Start service (deploy + activate)"


def test_table_columns_are_resizable_with_compact_defaults() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=False, active=None, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    header = widget._table.horizontalHeader()
    assert header.sectionResizeMode(widget._COL_SERVICE_ID) == QtWidgets.QHeaderView.Interactive
    assert header.sectionResizeMode(widget._COL_SERVICE_CLASS) == QtWidgets.QHeaderView.Interactive
    assert widget._table.columnWidth(widget._COL_SERVICE_ID) <= 220
    assert widget._table.columnWidth(widget._COL_SERVICE_CLASS) <= 240


def test_running_row_button_state_matches_toolbar_semantics() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=True, active=True, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    assert widget._toggle_btn.isEnabled() is True
    assert widget._stop_btn.isEnabled() is True
    assert widget._deploy_btn.isEnabled() is True
    assert widget._restart_btn.isEnabled() is True
    assert widget._toggle_btn.toolTip() == "Deactivate service"


def test_toggle_button_starts_service_when_not_running() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=False, active=None, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    widget._on_toggle_clicked()
    assert bridge.start_calls == [("svcA", "f8.tests.a")]
    assert bridge.active_calls == []


def test_toggle_button_activates_when_running_inactive() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=True, active=False, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    assert widget._toggle_btn.toolTip() == "Activate service"
    widget._on_toggle_clicked()
    assert bridge.start_calls == []
    assert bridge.active_calls == [("svcA", True)]


def test_toggle_button_deactivates_when_running_active() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=True, active=True, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    assert widget._toggle_btn.toolTip() == "Deactivate service"
    widget._on_toggle_clicked()
    assert bridge.start_calls == []
    assert bridge.active_calls == [("svcA", False)]


def test_deploy_button_deploys_current_rungraph_for_running_service() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=True, active=True, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    widget._on_deploy_clicked()
    assert bridge.deploy_calls == ["svcA"]


def test_adaptive_column_widths_return_defaults_when_space_is_enough() -> None:
    available_width = sum(ServiceManagerWidget._DEFAULT_COLUMN_WIDTHS)
    widths = ServiceManagerWidget._target_column_widths_for_available_width(available_width)
    assert widths == ServiceManagerWidget._DEFAULT_COLUMN_WIDTHS


def test_adaptive_column_widths_return_minimums_when_space_is_too_small() -> None:
    available_width = sum(ServiceManagerWidget._MIN_COLUMN_WIDTHS) - 1
    widths = ServiceManagerWidget._target_column_widths_for_available_width(available_width)
    assert widths == ServiceManagerWidget._MIN_COLUMN_WIDTHS


def test_adaptive_column_widths_shrink_proportionally_between_limits() -> None:
    default_total = sum(ServiceManagerWidget._DEFAULT_COLUMN_WIDTHS)
    minimum_total = sum(ServiceManagerWidget._MIN_COLUMN_WIDTHS)
    available_width = (default_total + minimum_total) // 2
    widths = ServiceManagerWidget._target_column_widths_for_available_width(available_width)

    assert sum(widths) == available_width
    assert widths != ServiceManagerWidget._DEFAULT_COLUMN_WIDTHS
    assert widths != ServiceManagerWidget._MIN_COLUMN_WIDTHS

    for index, width in enumerate(widths):
        assert width <= ServiceManagerWidget._DEFAULT_COLUMN_WIDTHS[index]
        assert width >= ServiceManagerWidget._MIN_COLUMN_WIDTHS[index]
