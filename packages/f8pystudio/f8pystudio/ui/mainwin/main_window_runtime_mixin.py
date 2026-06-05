from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable, cast

from qtpy import QtCore, QtWidgets

from ...nodegraph.runtime_compiler import CompiledRuntimeGraphs, compile_runtime_graphs_from_studio
from ...nodegraph.viewer import F8StudioNodeViewer
from ...ui.support.ui_notifications import show_warning
from ..support.service_inventory import collect_declared_service_ids
from f8pystudio.contracts.ui_commands import UiCommand, UiCommandApplier
from f8pystudio.studio_specs.registry import SERVICE_CLASS as STUDIO_SERVICE_CLASS

if TYPE_CHECKING:
    from ...monitoring.alerts import MonitorAlertNotifier
    from ...nodegraph.node_graph import F8StudioGraph
    from ..support.runtime_state_sync import RuntimeStateSyncController
    from ..widgets.node_property_panel import F8StudioSingleNodePropertiesWidget
    from ..widgets.service_log_widget import ServiceLogDock
    from .service_manager_widget import ServiceManagerWidget
    from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge
    from f8pystudio.automation.gui_host import StudioAutomationHost
    from f8pystudio.automation.observation_store import RuntimeObservationStore

logger = logging.getLogger(__name__)
_BRIDGE_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_BRIDGE_SERVICE_CONTROL_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_NODEGRAPH_MUTATION_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
_NODEGRAPH_VIEW_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
_PLACEMENT_CANCEL_ERRORS = (AttributeError, RuntimeError, TypeError)


class MainWindowRuntimeMixin:
    if TYPE_CHECKING:
        studio_graph: F8StudioGraph
        _bridge: PyStudioServiceBridge
        _log_dock: ServiceLogDock
        _service_manager: ServiceManagerWidget | None
        _prop_editor: F8StudioSingleNodePropertiesWidget
        _runtime_state_sync: RuntimeStateSyncController
        _monitor_alert_notifier: MonitorAlertNotifier
        _automation_host: StudioAutomationHost | None
        _runtime_observations: RuntimeObservationStore

        def _mark_auto_deploy_synced(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None: ...

    @QtCore.Slot(str, str)
    def _on_service_output(self, service_id: str, line: str) -> None:
        service_name = ""
        try:
            resolved_service_name = self._bridge.get_service_class(service_id)
        except _BRIDGE_ERRORS:
            logger.exception("Failed to resolve service class for output stream: %s", service_id)
        else:
            service_name = str(resolved_service_name or "").strip()

        if service_name:
            try:
                self._log_dock.set_service_name(service_id, service_name)
            except (AttributeError, RuntimeError, TypeError):
                logger.exception("Failed to set service name in log dock: %s", service_id)
        self._log_dock.append(service_id, line)

    @QtCore.Slot(str, bool)
    def _on_service_process_state(self, service_id: str, running: bool) -> None:
        if bool(running):
            if self._service_manager is not None:
                self._service_manager.queue_refresh()
            return
        try:
            self._log_dock.close_service_tab(service_id)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to close service log tab: %s", service_id)
        if self._service_manager is not None:
            self._service_manager.queue_refresh()

    @QtCore.Slot()
    def _on_clear_all_nodes_action(self) -> None:
        nodes = list(self.studio_graph.all_nodes() or [])
        if not nodes:
            self._log_dock.append("studio", "[graph] clear all nodes skipped: graph already empty\n")
            return

        answer = QtWidgets.QMessageBox.question(
            self,
            "Clear all nodes",
            f"Remove all {len(nodes)} nodes from the current graph?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            self.studio_graph.clear_session()
        except _NODEGRAPH_MUTATION_ERRORS as exc:
            self._log_dock.report_exception("studio", "clear all nodes failed", exc)
            show_warning(self, "Clear all nodes failed", str(exc))
            return
        self._log_dock.append("studio", f"[graph] cleared all nodes ({len(nodes)})\n")

    @QtCore.Slot()
    def _on_deploy_action(self) -> None:
        compiled = self._compile_runtime_graphs_for_action(
            blocked_fallback_message="deploy blocked by invalid graph",
            blocked_log_prefix="[deploy][blocked]",
            error_log_prefix="[deploy][error]",
            report_context="deploy compile failed",
            warning_blocked_title="Deploy blocked",
            warning_error_title="Deploy failed",
        )
        if compiled is None:
            return

        self._append_compile_warnings(compiled)
        self._bridge.deploy(compiled)
        self._mark_auto_deploy_synced(compiled=compiled)

    @QtCore.Slot()
    def _on_stop_all_services_action(self) -> None:
        declared_service_ids = collect_declared_service_ids(
            nodes=list(self.studio_graph.all_nodes() or []),
            studio_service_class=STUDIO_SERVICE_CLASS,
        )
        try:
            self._bridge.stop_all_services()
        except _BRIDGE_SERVICE_CONTROL_ERRORS as exc:
            self._log_dock.report_exception("studio", "stop all services failed", exc)
            return
        normalized_ids = sorted(str(service_id) for service_id in declared_service_ids)
        if normalized_ids:
            self._log_dock.append("studio", f"[service] stop all requested ({len(normalized_ids)} graph service(s))\n")
            return
        self._log_dock.append("studio", "[service] stop all requested\n")

    @QtCore.Slot(str)
    def _focus_node_by_id(self, node_id: str) -> None:
        target_node_id = str(node_id or "").strip()
        if not target_node_id:
            return
        try:
            node = self.studio_graph.get_node_by_id(target_node_id)
        except _NODEGRAPH_VIEW_ERRORS:
            logger.exception("Failed to resolve node while focusing: %s", target_node_id)
            return
        if node is None:
            return

        try:
            for existing in list(self.studio_graph.selected_nodes() or []):
                existing.set_property("selected", False, push_undo=False)
        except _NODEGRAPH_VIEW_ERRORS:
            logger.exception("Failed to clear selection while focusing hotkey row nodeId=%s", target_node_id)

        try:
            node.set_property("selected", True, push_undo=False)
        except _NODEGRAPH_VIEW_ERRORS:
            logger.exception("Failed to select hotkey row nodeId=%s", target_node_id)

        self._prop_editor.set_node(node)
        viewer = self.studio_graph.viewer()
        if not isinstance(viewer, F8StudioNodeViewer):
            return
        try:
            viewer.centerOn(node.view)
        except _NODEGRAPH_VIEW_ERRORS:
            logger.exception("Failed to center focused hotkey row nodeId=%s", target_node_id)

    @QtCore.Slot()
    def _on_escape_cancel_placement(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None and app.activePopupWidget() is not None:
            return
        viewer_object = self.studio_graph.viewer()
        if viewer_object is None:
            return
        viewer = cast(F8StudioNodeViewer, viewer_object)
        try:
            if viewer.is_graph_placement_active():
                self.studio_graph.cancel_graph_placement()
                return
            if viewer.is_node_placement_active():
                self.studio_graph.cancel_node_placement()
        except _PLACEMENT_CANCEL_ERRORS:
            logger.debug("Failed to cancel active graph/node placement from escape shortcut", exc_info=True)
            return

    def _on_runtime_state_updated(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: object,
        ts_ms: object,
    ) -> None:
        try:
            self._runtime_observations.put_state(
                service_id=service_id,
                node_id=node_id,
                field=field,
                value=value,
                ts_ms=int(ts_ms or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.exception("failed to record runtime state observation nodeId=%s field=%s", node_id, field)
        self._runtime_state_sync.on_runtime_state_updated(service_id, node_id, field, value, ts_ms)

    def _on_ui_command(self, cmd: UiCommand) -> None:
        command = str(cmd.command)
        if command == "monitor.update":
            payload_obj = cmd.payload
            if isinstance(payload_obj, dict):
                self._monitor_alert_notifier.handle_snapshot(payload_obj, parent=self)
            if self._service_manager is not None:
                self._service_manager.queue_monitor_refresh()
            return
        if command == "state.update":
            payload = dict(cmd.payload or {})
            field = str(payload.get("field") or "")
            value = payload.get("value")
            service_id = str(payload.get("serviceId") or "")
            node_id = str(cmd.node_id or "")
            if node_id and field:
                self._on_runtime_state_updated(service_id, node_id, field, value, cmd.ts_ms)
            return

        node_id = str(cmd.node_id or "").strip()
        if not node_id:
            return
        try:
            node = self.studio_graph.get_node_by_id(node_id)
        except _NODEGRAPH_VIEW_ERRORS:
            logger.exception("Failed to resolve node for ui command: %s", node_id)
            return
        if node is None or not isinstance(node, UiCommandApplier):
            return
        try:
            node.apply_ui_command(cmd)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.exception("Failed to apply ui command to node: %s", node_id)

    def _on_ui_property_changed(self, node: object, name: str, value: object) -> None:
        self._runtime_state_sync.on_ui_property_changed(node, name, value)

    def _append_studio_log_line(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        if not text.endswith("\n"):
            text = text + "\n"
        self._log_dock.append("studio", text)

    @QtCore.Slot()
    def _on_studio_runtime_sync_timeout(self) -> None:
        compiled = self._compile_runtime_graphs_for_action(
            blocked_fallback_message="studio runtime sync blocked by invalid graph",
            blocked_log_prefix="[studio][sync][blocked]",
            error_log_prefix="[studio][sync][error]",
            report_context="studio runtime sync compile failed",
            warning_blocked_title=None,
            warning_error_title=None,
        )
        if compiled is None:
            return

        try:
            self._append_compile_warnings(compiled)
            self._bridge.sync_studio_runtime(compiled)
        except _BRIDGE_SERVICE_CONTROL_ERRORS as exc:
            self._log_dock.report_exception("studio", "studio runtime sync failed", exc)

    def _compile_runtime_graphs_for_action(
        self,
        *,
        blocked_fallback_message: str,
        blocked_log_prefix: str,
        error_log_prefix: str,
        report_context: str,
        warning_blocked_title: str | None,
        warning_error_title: str | None,
    ) -> CompiledRuntimeGraphs | None:
        try:
            return compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or str(blocked_fallback_message)
            self._log_dock.append("studio", f"{blocked_log_prefix} {msg}\n")
            if warning_blocked_title is not None:
                show_warning(self, warning_blocked_title, msg)
            return None
        except _NODEGRAPH_VIEW_ERRORS as exc:
            self._log_dock.append("studio", f"{error_log_prefix} {exc}\n")
            self._log_dock.report_exception("studio", report_context, exc)
            if warning_error_title is not None:
                show_warning(self, warning_error_title, str(exc))
            return None

    def _apply_auto_deploy(
        self,
        *,
        compiled: CompiledRuntimeGraphs,
        current_undo_index: int,
        last_auto_deploy_observed_undo_index: int,
        last_auto_deploy_fingerprint: str,
        declared_service_ids: Iterable[str],
        fingerprint: str,
    ) -> tuple[int, str]:
        if current_undo_index == last_auto_deploy_observed_undo_index:
            return last_auto_deploy_observed_undo_index, last_auto_deploy_fingerprint

        self._append_compile_warnings(compiled)
        if fingerprint == last_auto_deploy_fingerprint:
            self._log_dock.append("studio", "[deploy][auto][skip] deploy fingerprint unchanged\n")
            return current_undo_index, fingerprint

        running_service_ids: list[str] = []
        for service_id in sorted(str(service_id) for service_id in declared_service_ids):
            try:
                if self._bridge.is_service_running(service_id):
                    running_service_ids.append(service_id)
            except _BRIDGE_SERVICE_CONTROL_ERRORS as exc:
                self._log_dock.report_exception("studio", f"auto deploy status check failed ({service_id})", exc)

        if not running_service_ids:
            self._log_dock.append("studio", "[deploy][auto][skip] no running services\n")
            return current_undo_index, fingerprint

        self._log_dock.append(
            "studio",
            f"[deploy][auto] applying rungraph to {len(running_service_ids)} running service(s)\n",
        )
        for service_id in running_service_ids:
            try:
                self._bridge.deploy_service_rungraph(service_id, compiled=compiled)
            except _BRIDGE_SERVICE_CONTROL_ERRORS as exc:
                self._log_dock.report_exception("studio", f"auto deploy failed ({service_id})", exc)
        return current_undo_index, fingerprint

    def _append_compile_warnings(self, compiled: CompiledRuntimeGraphs) -> None:
        for warning in list(compiled.warnings or ()):
            self._log_dock.append("studio", f"[compile][warn] {warning}\n")
