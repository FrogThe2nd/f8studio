from __future__ import annotations

import os
import sys

from qtpy import QtCore, QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS  # noqa: E402
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID  # noqa: E402
from f8pystudio.bridge.studio_bridge import ServiceMonitorRow  # noqa: E402
from f8pystudio.ui.mainwin import service_manager_widget as service_manager_widget_module  # noqa: E402
from f8pystudio.ui.mainwin.service_manager_widget import ServiceManagerWidget  # noqa: E402


class _FakeBridge:
    def __init__(self, rows: list[ServiceMonitorRow]) -> None:
        self._rows = list(rows)
        self.status_requests: list[str] = []
        self.start_calls: list[tuple[str, str]] = []
        self.active_calls: list[tuple[str, bool]] = []
        self.deploy_calls: list[str] = []
        self.stream_requests: list[tuple[str, int]] = []

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

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        self.stream_requests.append((str(service_id), int(limit)))
        return []


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


def _row_with_service_id(service_id: str) -> ServiceMonitorRow:
    return ServiceMonitorRow(
        service_id=service_id,
        service_class="f8.tests.a",
        running=True,
        alive=True,
        ready=True,
        active=True,
        cpu_process_percent=12.5,
        memory_rss_bytes=128 * 1024 * 1024,
        gpu_util_percent=3.0,
        latency_ms_p95=1.25,
        wait_ms_p95=None,
        error_count_window=0,
        latest_snapshot=None,
    )


def _cell_text(widget: ServiceManagerWidget, row: int, column: int) -> str | None:
    index = widget._proxy_model.index(row, column)
    if not index.isValid():
        return None
    value = widget._proxy_model.data(index, QtCore.Qt.DisplayRole)
    if value is None:
        return None
    return str(value)


def test_service_class_falls_back_to_declared_graph_service() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=False, active=None, service_class="")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    assert widget._model.rowCount() == 1
    assert _cell_text(widget, 0, 1) == "f8.tests.a"
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
    assert widget._table.isSortingEnabled() is True
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


def test_service_monitor_context_menu_includes_monitor_stream_action() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row(running=True, active=True, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    menu = widget._build_table_context_menu(widget._selected_row())

    actions = [action for action in menu.actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == ["View Monitor Stream..."]
    assert actions[0].isEnabled() is True


def test_service_monitor_context_menu_action_opens_selected_monitor_stream(monkeypatch) -> None:
    _ensure_app()
    opened: list[tuple[str, object]] = []
    bridge = _FakeBridge([_row(running=True, active=True, service_class="f8.tests.a")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svcA": "f8.tests.a"},
    )
    widget.refresh()

    def _open_dialog(*, parent: QtWidgets.QWidget | None, bridge: object, service_id: str) -> None:
        _ = parent
        opened.append((str(service_id), bridge))

    monkeypatch.setattr(service_manager_widget_module, "open_monitor_stream_dialog", _open_dialog)

    widget._open_selected_monitor_stream()

    assert opened == [("svcA", bridge)]


def test_bridge_only_rows_are_preserved_without_declared_graph_service() -> None:
    _ensure_app()
    bridge = _FakeBridge([_row_with_service_id("svc_runtime_only")])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {},
    )
    widget.refresh()

    assert widget._model.rowCount() == 1
    assert _cell_text(widget, 0, widget._COL_SERVICE_ID) == "svc_runtime_only"


def test_built_in_studio_service_row_is_read_only() -> None:
    _ensure_app()
    studio_row = ServiceMonitorRow(
        service_id=STUDIO_SERVICE_ID,
        service_class=STUDIO_SERVICE_CLASS,
        running=True,
        alive=True,
        ready=True,
        active=True,
        cpu_process_percent=None,
        memory_rss_bytes=None,
        gpu_util_percent=None,
        latency_ms_p95=None,
        wait_ms_p95=None,
        error_count_window=None,
        latest_snapshot=None,
    )
    bridge = _FakeBridge([studio_row])
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {},
    )
    widget.refresh()

    assert widget._model.rowCount() == 1
    assert _cell_text(widget, 0, widget._COL_SERVICE_ID) == STUDIO_SERVICE_ID
    assert widget._toggle_btn.isEnabled() is False
    assert widget._stop_btn.isEnabled() is False
    assert widget._deploy_btn.isEnabled() is False
    assert widget._restart_btn.isEnabled() is False

    widget._on_toggle_clicked()
    widget._on_stop_clicked()
    widget._on_deploy_clicked()
    widget._on_restart_clicked()

    assert bridge.start_calls == []
    assert bridge.active_calls == []
    assert bridge.deploy_calls == []


def test_refresh_preserves_scroll_position_when_selection_would_jump_to_top() -> None:
    app = _ensure_app()
    rows = [_row_with_service_id(f"svc{index:02d}") for index in range(60)]
    bridge = _FakeBridge(rows)
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {row.service_id: row.service_class for row in rows},
    )
    widget.resize(700, 220)
    widget.show()
    app.processEvents()

    scrollbar = widget._table.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    expected_scroll = int(scrollbar.value())

    widget.refresh()
    app.processEvents()

    assert expected_scroll > 0
    assert scrollbar.value() == expected_scroll


def test_refresh_reuses_existing_items_for_unchanged_row() -> None:
    _ensure_app()
    rows = [_row_with_service_id("svcA"), _row_with_service_id("svcB")]
    bridge = _FakeBridge(rows)
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {row.service_id: row.service_class for row in rows},
    )
    widget.refresh()

    original_id_index = QtCore.QPersistentModelIndex(widget._model.index(0, widget._COL_SERVICE_ID))
    original_cpu_index = QtCore.QPersistentModelIndex(widget._model.index(0, widget._COL_CPU))

    assert original_id_index.isValid() is True
    assert original_cpu_index.isValid() is True

    bridge._rows = [
        ServiceMonitorRow(
            service_id="svcA",
            service_class="f8.tests.a",
            running=True,
            alive=True,
            ready=True,
            active=True,
            cpu_process_percent=88.0,
            memory_rss_bytes=128 * 1024 * 1024,
            gpu_util_percent=3.0,
            latency_ms_p95=1.25,
            wait_ms_p95=None,
            error_count_window=0,
            latest_snapshot=None,
        ),
        _row_with_service_id("svcB"),
    ]

    widget.refresh()

    assert original_id_index.isValid() is True
    assert original_cpu_index.isValid() is True
    assert widget._model.data(original_id_index, QtCore.Qt.DisplayRole) == "svcA"
    assert widget._model.data(original_cpu_index, QtCore.Qt.DisplayRole) == "88.0"


def test_cpu_column_sorts_by_numeric_value() -> None:
    _ensure_app()
    bridge = _FakeBridge(
        [
            ServiceMonitorRow(
                service_id="svc_low",
                service_class="f8.tests.a",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=9.0,
                memory_rss_bytes=128 * 1024 * 1024,
                gpu_util_percent=3.0,
                latency_ms_p95=1.25,
                wait_ms_p95=None,
                error_count_window=0,
                latest_snapshot=None,
            ),
            ServiceMonitorRow(
                service_id="svc_high",
                service_class="f8.tests.a",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=100.0,
                memory_rss_bytes=128 * 1024 * 1024,
                gpu_util_percent=3.0,
                latency_ms_p95=1.25,
                wait_ms_p95=None,
                error_count_window=0,
                latest_snapshot=None,
            ),
        ]
    )
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svc_low": "f8.tests.a", "svc_high": "f8.tests.a"},
    )
    widget.refresh()

    widget._table.sortByColumn(widget._COL_CPU, QtCore.Qt.DescendingOrder)

    assert _cell_text(widget, 0, widget._COL_SERVICE_ID) == "svc_high"
    assert _cell_text(widget, 1, widget._COL_SERVICE_ID) == "svc_low"


def test_unknown_numeric_values_sort_last_in_ascending_order() -> None:
    _ensure_app()
    bridge = _FakeBridge(
        [
            ServiceMonitorRow(
                service_id="svc_unknown",
                service_class="f8.tests.a",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=None,
                memory_rss_bytes=None,
                gpu_util_percent=None,
                latency_ms_p95=None,
                wait_ms_p95=None,
                error_count_window=None,
                latest_snapshot=None,
            ),
            ServiceMonitorRow(
                service_id="svc_known",
                service_class="f8.tests.a",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=5.0,
                memory_rss_bytes=64 * 1024 * 1024,
                gpu_util_percent=2.0,
                latency_ms_p95=0.5,
                wait_ms_p95=None,
                error_count_window=1,
                latest_snapshot=None,
            ),
        ]
    )
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {"svc_unknown": "f8.tests.a", "svc_known": "f8.tests.a"},
    )
    widget.refresh()

    widget._table.sortByColumn(widget._COL_CPU, QtCore.Qt.AscendingOrder)

    assert _cell_text(widget, 0, widget._COL_SERVICE_ID) == "svc_known"
    assert _cell_text(widget, 1, widget._COL_SERVICE_ID) == "svc_unknown"


def test_unknown_bool_values_sort_last_in_ascending_order() -> None:
    _ensure_app()
    bridge = _FakeBridge(
        [
            ServiceMonitorRow(
                service_id="svc_unknown",
                service_class="f8.tests.a",
                running=True,
                alive=None,
                ready=True,
                active=True,
                cpu_process_percent=1.0,
                memory_rss_bytes=64 * 1024 * 1024,
                gpu_util_percent=0.0,
                latency_ms_p95=0.5,
                wait_ms_p95=None,
                error_count_window=0,
                latest_snapshot=None,
            ),
            ServiceMonitorRow(
                service_id="svc_false",
                service_class="f8.tests.a",
                running=True,
                alive=False,
                ready=True,
                active=True,
                cpu_process_percent=1.0,
                memory_rss_bytes=64 * 1024 * 1024,
                gpu_util_percent=0.0,
                latency_ms_p95=0.5,
                wait_ms_p95=None,
                error_count_window=0,
                latest_snapshot=None,
            ),
            ServiceMonitorRow(
                service_id="svc_true",
                service_class="f8.tests.a",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=1.0,
                memory_rss_bytes=64 * 1024 * 1024,
                gpu_util_percent=0.0,
                latency_ms_p95=0.5,
                wait_ms_p95=None,
                error_count_window=0,
                latest_snapshot=None,
            ),
        ]
    )
    widget = ServiceManagerWidget(
        bridge=bridge,  # type: ignore[arg-type]
        get_declared_services=lambda: {
            "svc_unknown": "f8.tests.a",
            "svc_false": "f8.tests.a",
            "svc_true": "f8.tests.a",
        },
    )
    widget.refresh()

    widget._table.sortByColumn(widget._COL_ALIVE, QtCore.Qt.AscendingOrder)

    assert _cell_text(widget, 0, widget._COL_SERVICE_ID) == "svc_false"
    assert _cell_text(widget, 1, widget._COL_SERVICE_ID) == "svc_true"
    assert _cell_text(widget, 2, widget._COL_SERVICE_ID) == "svc_unknown"


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
