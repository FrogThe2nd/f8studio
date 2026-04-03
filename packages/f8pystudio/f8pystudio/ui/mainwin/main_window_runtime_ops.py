from __future__ import annotations

from typing import Callable, Iterable, Protocol

from ...nodegraph.runtime_compiler import CompiledRuntimeGraphs
from ...pystudio_node_registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from ...ui_bus import UiCommand, UiCommandApplier


class MainWindowLogDockLike(Protocol):
    def append(self, channel: str, line: str) -> None: ...
    def report_exception(self, channel: str, context: str, exc: Exception) -> None: ...
    def close_service_tab(self, service_id: str) -> None: ...
    def set_service_name(self, service_id: str, service_name: str) -> None: ...


class ServiceManagerLike(Protocol):
    def queue_refresh(self) -> None: ...


class RuntimeBridgeLike(Protocol):
    def get_service_class(self, service_id: str) -> str | None: ...
    def deploy(self, compiled: CompiledRuntimeGraphs) -> None: ...
    def stop_service(self, service_id: str) -> None: ...
    def is_service_running(self, service_id: str) -> bool: ...
    def deploy_service_rungraph(self, service_id: str, *, compiled: CompiledRuntimeGraphs | None = None) -> None: ...
    def sync_studio_runtime(self, compiled: CompiledRuntimeGraphs) -> None: ...


def handle_service_output(
    *,
    bridge: RuntimeBridgeLike,
    log_dock: MainWindowLogDockLike,
    service_id: str,
    line: str,
) -> None:
    try:
        service_name = str(bridge.get_service_class(service_id) or "").strip()
    except Exception:
        service_name = ""
    if service_name:
        try:
            log_dock.set_service_name(service_id, service_name)
        except (AttributeError, RuntimeError, TypeError):
            pass
    log_dock.append(service_id, line)


def handle_service_process_state(
    *,
    manager: ServiceManagerLike | None,
    log_dock: MainWindowLogDockLike,
    service_id: str,
    running: bool,
) -> None:
    if running:
        if manager is not None:
            manager.queue_refresh()
        return
    try:
        log_dock.close_service_tab(service_id)
    except (AttributeError, RuntimeError, TypeError):
        pass
    if manager is not None:
        manager.queue_refresh()


def clear_all_nodes(
    *,
    parent: object,
    studio_graph: object,
    log_dock: MainWindowLogDockLike,
    show_warning: Callable[[object, str, str], None],
) -> None:
    nodes = list(studio_graph.all_nodes() or [])
    if not nodes:
        log_dock.append("studio", "[graph] clear all nodes skipped: graph already empty\n")
        return

    from qtpy import QtWidgets

    answer = QtWidgets.QMessageBox.question(
        parent,
        "Clear all nodes",
        f"Remove all {len(nodes)} nodes from the current graph?",
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.No,
    )
    if answer != QtWidgets.QMessageBox.StandardButton.Yes:
        return
    try:
        studio_graph.clear_session()
    except Exception as exc:
        log_dock.report_exception("studio", "clear all nodes failed", exc)
        show_warning(parent, "Clear all nodes failed", str(exc))
        return
    log_dock.append("studio", f"[graph] cleared all nodes ({len(nodes)})\n")


def deploy_graph(
    *,
    parent: object,
    compiled: CompiledRuntimeGraphs,
    log_dock: MainWindowLogDockLike,
    bridge: RuntimeBridgeLike,
) -> None:
    for warning in list(compiled.warnings or ()):
        log_dock.append("studio", f"[compile][warn] {warning}\n")
    bridge.deploy(compiled)


def stop_all_services(
    *,
    service_ids: Iterable[str],
    bridge: RuntimeBridgeLike,
    log_dock: MainWindowLogDockLike,
) -> None:
    normalized_ids = [str(service_id) for service_id in service_ids]
    if not normalized_ids:
        log_dock.append("studio", "[service] no graph service instances to stop\n")
        return
    for service_id in sorted(normalized_ids):
        try:
            bridge.stop_service(service_id)
            log_dock.append("studio", f"[service] stop requested: {service_id}\n")
        except Exception as exc:
            log_dock.report_exception("studio", f"stop service failed ({service_id})", exc)


def handle_ui_command(
    *,
    cmd: UiCommand,
    service_manager: ServiceManagerLike | None,
    on_runtime_state_updated: Callable[[str, str, str, object, object], None],
    studio_graph: object,
) -> None:
    if str(cmd.command) == "monitor.update":
        if service_manager is not None:
            service_manager.queue_refresh()
    if str(cmd.command) == "state.update":
        payload = dict(cmd.payload or {})
        field = str(payload.get("field") or "")
        value = payload.get("value")
        service_id = str(payload.get("serviceId") or "")
        node_id = str(cmd.node_id or "")
        if node_id and field:
            on_runtime_state_updated(service_id, node_id, field, value, cmd.ts_ms)
        return

    node_id = str(cmd.node_id or "").strip()
    if not node_id:
        return
    try:
        node = studio_graph.get_node_by_id(node_id)
    except Exception:
        node = None
    if node is None:
        return
    try:
        if isinstance(node, UiCommandApplier):
            node.apply_ui_command(cmd)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def sync_studio_runtime(
    *,
    compiled: CompiledRuntimeGraphs,
    log_dock: MainWindowLogDockLike,
    bridge: RuntimeBridgeLike,
) -> None:
    for warning in list(compiled.warnings or ()):
        log_dock.append("studio", f"[compile][warn] {warning}\n")
    bridge.sync_studio_runtime(compiled)


def apply_auto_deploy(
    *,
    compiled: CompiledRuntimeGraphs,
    current_undo_index: int,
    last_auto_deploy_observed_undo_index: int,
    last_auto_deploy_fingerprint: str,
    bridge: RuntimeBridgeLike,
    log_dock: MainWindowLogDockLike,
    declared_service_ids: Iterable[str],
    fingerprint: str,
) -> tuple[int, str]:
    if current_undo_index == last_auto_deploy_observed_undo_index:
        return last_auto_deploy_observed_undo_index, last_auto_deploy_fingerprint

    for warning in list(compiled.warnings or ()):
        log_dock.append("studio", f"[compile][warn] {warning}\n")

    if fingerprint == last_auto_deploy_fingerprint:
        log_dock.append("studio", "[deploy][auto][skip] deploy fingerprint unchanged\n")
        return current_undo_index, fingerprint

    running_service_ids: list[str] = []
    for service_id in sorted(str(service_id) for service_id in declared_service_ids):
        try:
            if bridge.is_service_running(service_id):
                running_service_ids.append(service_id)
        except Exception as exc:
            log_dock.report_exception("studio", f"auto deploy status check failed ({service_id})", exc)

    if not running_service_ids:
        log_dock.append("studio", "[deploy][auto][skip] no running services\n")
        return current_undo_index, fingerprint

    log_dock.append(
        "studio",
        f"[deploy][auto] applying rungraph to {len(running_service_ids)} running service(s)\n",
    )
    for service_id in running_service_ids:
        try:
            bridge.deploy_service_rungraph(service_id, compiled=compiled)
        except Exception as exc:
            log_dock.report_exception("studio", f"auto deploy failed ({service_id})", exc)
    return current_undo_index, fingerprint
