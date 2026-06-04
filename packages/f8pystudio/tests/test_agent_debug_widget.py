from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.monitoring.service_rows import ServiceMonitorRow
from f8pystudio.ui.agents.agent_debug_widget import AgentDebugServiceRow, AgentDebugServiceTableModel, AgentDebugWidget


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _Bridge:
    studio_service_id = "studio"

    def __init__(self) -> None:
        self.rows = [
            ServiceMonitorRow(
                service_id="svc-a",
                service_class="f8.pyengine",
                running=True,
                alive=True,
                ready=True,
                active=True,
                cpu_process_percent=1.0,
                memory_rss_bytes=1024,
                gpu_util_percent=None,
                latency_ms_p95=2.5,
                wait_ms_p95=None,
                error_count_window=0,
            )
        ]

    def list_service_monitor_rows(self) -> list[ServiceMonitorRow]:
        return list(self.rows)

    def get_latest_monitor_snapshot(self, service_id: str) -> dict[str, object]:
        return {"serviceId": service_id, "ready": True}

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        return [{"serviceId": service_id, "index": 1}]

    def get_service_class(self, service_id: str) -> str:
        if service_id == "svc-a":
            return "f8.pyengine"
        return ""

    def is_service_running(self, service_id: str) -> bool:
        return service_id == "svc-a"

    def get_cached_service_active(self, service_id: str) -> bool | None:
        if service_id == "svc-a":
            return True
        return None


def test_agent_debug_service_table_model_formats_rows() -> None:
    _ensure_app()
    model = AgentDebugServiceTableModel()
    model.update_rows(
        [
            AgentDebugServiceRow(
                service_id="svc-a",
                service_class="f8.pyengine",
                running=True,
                alive=None,
                ready=None,
                active=None,
                latency_ms_p95=None,
                errors=None,
                current_error="",
            )
        ]
    )

    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "svc-a"
    assert model.data(model.index(0, 2)) == "yes"


def test_agent_debug_widget_refreshes_runtime_evidence() -> None:
    _ensure_app()
    bridge = _Bridge()
    widget = AgentDebugWidget(
        bridge=bridge,  # type: ignore[arg-type]
        studio_graph=object(),
        deploy_requested=lambda: None,
    )

    widget.refresh()

    evidence = widget._evidence.toPlainText()
    assert '"serviceId": "svc-a"' in evidence
    assert '"running": true' in evidence
