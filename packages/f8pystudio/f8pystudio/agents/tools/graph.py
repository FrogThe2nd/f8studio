from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from qtpy import QtCore

from f8pystudio.agents.graph_builder import (
    decode_graph_build_plan,
    delivery_report_for_plan,
    graph_build_plan_schema_hint,
    graph_patch_from_build_plan,
    match_graph_library_candidates,
)
from f8pystudio.agents.tool_events import (
    StudioAgentApprovalRequest,
    StudioAgentToolTrace,
    new_approval_id,
    new_tool_call_id,
    now_ms,
    summarize_tool_params,
    summarize_tool_result,
    tool_method_to_name,
)
from f8pystudio.automation.domain import GraphPatch, MoveNodeOp, SetNodeStateOp, decode_graph_patch, graph_patch_to_dict
from f8pystudio.automation.graph_adapter import StudioGraphAutomationAdapter
from f8pystudio.automation.library_catalog import (
    operator_detail_payload,
    operator_library_payload,
    service_library_payload,
)
from f8pystudio.automation.observation_store import StoredStateValue

logger = logging.getLogger(__name__)
_GRAPH_TOOL_ERRORS = (AttributeError, KeyError, RuntimeError, TimeoutError, TypeError, ValueError)
_DEFAULT_APPROVAL_TIMEOUT_S = 120.0


class StudioGraphToolExecutor(Protocol):
    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...


class StudioRuntimeToolBridge(Protocol):
    @property
    def studio_service_id(self) -> str: ...

    @property
    def managed_active(self) -> bool: ...

    def export_monitor_report(self) -> dict[str, Any]: ...

    def get_latest_monitor_snapshot(self, service_id: str) -> dict[str, Any] | None: ...

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, Any]]: ...

    def list_service_monitor_rows(self) -> list[Any]: ...

    def is_service_running(self, service_id: str) -> bool: ...

    def get_service_class(self, service_id: str) -> str: ...

    def get_cached_service_active(self, service_id: str) -> bool | None: ...

    def deploy_and_wait(self, compiled: Any, *, timeout_s: float = 20.0) -> dict[str, Any]: ...

    def deploy_service_and_wait(self, service_id: str, *, compiled: Any | None = None, timeout_s: float = 10.0) -> dict[str, Any]: ...

    def start_service(self, service_id: str, *, service_class: str | None = None) -> None: ...

    def stop_service(self, service_id: str) -> None: ...

    def restart_service(self, service_id: str, *, service_class: str | None = None) -> None: ...

    def set_service_active(self, service_id: str, active: bool) -> None: ...

    def set_managed_active(self, active: bool) -> None: ...

    def set_remote_state_and_wait(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]: ...

    def sample_data_port_and_wait(
        self,
        service_id: str,
        node_id: str,
        port: str,
        *,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
    ) -> dict[str, Any]: ...

    def invoke_remote_command_and_wait(
        self,
        service_id: str,
        call: str,
        args: Any = None,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]: ...


class StudioLogToolSource(Protocol):
    def export_logs(
        self,
        *,
        service_id: str = "",
        limit: int = 200,
        minimum_level: int | None = None,
    ) -> dict[str, object]: ...


class StudioRuntimeObservationSource(Protocol):
    def get_state(self, *, service_id: str, node_id: str, field: str) -> StoredStateValue | None: ...

    def wait_state(
        self,
        *,
        service_id: str,
        node_id: str,
        field: str,
        after_ts_ms: int | None = None,
        timeout_s: float = 1.0,
    ) -> StoredStateValue | None: ...


class StudioGraphUiContextSource(Protocol):
    def graph_ui_context(self) -> dict[str, Any]: ...


@dataclass
class _PendingToolApproval:
    request: StudioAgentApprovalRequest
    event: threading.Event
    approved: bool | None = None


@dataclass(frozen=True)
class _ToolApprovalSpec:
    title: str
    description: str
    confirm_error_message: str
    require_confirm_without_callback: bool = True


class LocalStudioGraphToolExecutor(QtCore.QObject):
    _call_requested = QtCore.Signal(str, object, object)

    def __init__(
        self,
        studio_graph: object,
        *,
        bridge: StudioRuntimeToolBridge | None = None,
        log_source: StudioLogToolSource | None = None,
        observation_source: StudioRuntimeObservationSource | None = None,
        ui_context_source: StudioGraphUiContextSource | None = None,
        on_graph_patch_applied: Callable[[], None] | None = None,
        on_tool_trace: Callable[[dict[str, Any]], None] | None = None,
        on_tool_approval_requested: Callable[[dict[str, Any]], None] | None = None,
        approval_timeout_s: float = _DEFAULT_APPROVAL_TIMEOUT_S,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._adapter = StudioGraphAutomationAdapter(studio_graph)
        self._bridge = bridge
        self._log_source = log_source
        self._observation_source = observation_source
        self._ui_context_source = ui_context_source
        self._on_graph_patch_applied = on_graph_patch_applied
        self._on_tool_trace = on_tool_trace
        self._on_tool_approval_requested = on_tool_approval_requested
        self._approval_timeout_s = max(1.0, float(approval_timeout_s))
        self._approval_lock = threading.Lock()
        self._pending_approvals: dict[str, _PendingToolApproval] = {}
        self._call_requested.connect(
            self._handle_call_on_qt_thread,
            QtCore.Qt.ConnectionType.BlockingQueuedConnection,
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params_dict = dict(params or {})
        method_text = str(method)
        tool_call_id = new_tool_call_id()
        started_at_ms = now_ms()
        self._emit_tool_trace(
            StudioAgentToolTrace(
                tool_call_id=tool_call_id,
                tool_name=tool_method_to_name(method_text),
                method=method_text,
                status="started",
                started_at_ms=started_at_ms,
                summary=summarize_tool_params(params_dict),
            )
        )
        try:
            self._ensure_approved_if_needed(method_text, params_dict, tool_call_id=tool_call_id)
            result = self._call_dispatch(method_text, params_dict)
        except _GRAPH_TOOL_ERRORS as exc:
            ended_at_ms = now_ms()
            self._emit_tool_trace(
                StudioAgentToolTrace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_method_to_name(method_text),
                    method=method_text,
                    status="failed",
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                    duration_ms=max(0, ended_at_ms - started_at_ms),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
        ended_at_ms = now_ms()
        self._emit_tool_trace(
            StudioAgentToolTrace(
                tool_call_id=tool_call_id,
                tool_name=tool_method_to_name(method_text),
                method=method_text,
                status="completed",
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                duration_ms=max(0, ended_at_ms - started_at_ms),
                summary=summarize_tool_result(result),
            )
        )
        return result

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        resolved_id = str(approval_id or "").strip()
        if not resolved_id:
            return
        with self._approval_lock:
            pending = self._pending_approvals.get(resolved_id)
            if pending is None:
                return
            pending.approved = bool(approved)
            pending.event.set()

    def _call_dispatch(self, method: str, params_dict: dict[str, Any]) -> dict[str, Any]:
        if QtCore.QThread.currentThread() == self.thread():
            return self._dispatch(method, params_dict)
        response_box: dict[str, Any] = {}
        self._call_requested.emit(method, params_dict, response_box)
        error = response_box.get("error")
        if isinstance(error, BaseException):
            raise error
        result = response_box.get("result")
        if isinstance(result, dict):
            return dict(result)
        return {}

    def _emit_tool_trace(self, trace: StudioAgentToolTrace) -> None:
        callback = self._on_tool_trace
        if callback is None:
            return
        try:
            callback(trace.to_dict())
        except _GRAPH_TOOL_ERRORS:
            logger.exception("failed to publish graph agent tool trace method=%s", trace.method)

    def _ensure_approved_if_needed(self, method: str, params: dict[str, Any], *, tool_call_id: str) -> None:
        approval_spec = _approval_spec_for_method(method, params)
        if approval_spec is None:
            return
        if bool(params.get("confirm")):
            return
        callback = self._on_tool_approval_requested
        if callback is None:
            if approval_spec.require_confirm_without_callback:
                raise ValueError(approval_spec.confirm_error_message)
            return
        if QtCore.QThread.currentThread() == self.thread():
            raise ValueError(f"{approval_spec.confirm_error_message}; GUI approval cannot block the UI thread")

        request = StudioAgentApprovalRequest(
            approval_id=new_approval_id(),
            tool_call_id=tool_call_id,
            tool_name=tool_method_to_name(method),
            method=method,
            title=approval_spec.title,
            description=approval_spec.description,
            params_summary=summarize_tool_params(params),
            created_at_ms=now_ms(),
            timeout_s=self._approval_timeout_s,
            metadata={"confirmParam": "confirm"},
        )
        pending = _PendingToolApproval(request=request, event=threading.Event())
        with self._approval_lock:
            self._pending_approvals[request.approval_id] = pending
        try:
            callback(request.to_dict())
            if not pending.event.wait(timeout=self._approval_timeout_s):
                raise TimeoutError(f"{request.tool_name} approval timed out")
            if pending.approved is not True:
                raise ValueError(f"{request.tool_name} approval denied")
            params["confirm"] = True
        finally:
            with self._approval_lock:
                self._pending_approvals.pop(request.approval_id, None)

    @QtCore.Slot(str, object, object)
    def _handle_call_on_qt_thread(self, method: str, params: object, response_box: object) -> None:
        if not isinstance(response_box, dict):
            return
        try:
            params_dict = params if isinstance(params, dict) else {}
            response_box["result"] = self._dispatch(str(method), dict(params_dict))
        except _GRAPH_TOOL_ERRORS as exc:
            logger.exception("local graph agent tool failed method=%s", method)
            response_box["error"] = exc

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "graph.snapshot":
            return {"snapshot": self._adapter.snapshot().to_dict()}
        if method == "graph.session":
            return {"session": self._adapter.session_payload()}
        if method == "graph.uiContext":
            return {"uiContext": self._graph_ui_context()}
        if method == "graph.catalog":
            return self._adapter.node_catalog()
        if method == "graph.findNodes":
            return self._adapter.find_nodes(
                query=str(params.get("query") or ""),
                node_id=str(params.get("nodeId") or ""),
                node_type=str(params.get("nodeType") or ""),
                kind=str(params.get("kind") or ""),
                service_class=str(params.get("serviceClass") or ""),
                operator_class=str(params.get("operatorClass") or ""),
                selected_only=bool(params.get("selectedOnly", False)),
                limit=int(params.get("limit") or 50),
            )
        if method == "graph.nodeDetail":
            return {"detail": self._adapter.node_detail(_required_text(params, "nodeId"))}
        if method == "graph.connections":
            return self._adapter.connections(
                node_id=str(params.get("nodeId") or ""),
                direction=str(params.get("direction") or "both"),
                limit=int(params.get("limit") or 200),
            )
        if method == "graph.diagnostics":
            return {"diagnostics": self._adapter.diagnostics()}
        if method == "graph.previewPatch":
            patch = decode_graph_patch(params.get("patch"))
            return {"preview": self._adapter.preview_patch(patch).to_dict()}
        if method == "graph.applyPatch":
            if _requires_confirm(params) and not bool(params.get("confirm")):
                raise ValueError("graph.applyPatch with destructive ops requires confirm=true")
            patch = decode_graph_patch(params.get("patch"))
            preview = self._adapter.apply_patch(patch)
            self._notify_graph_patch_applied()
            return {"preview": preview.to_dict(), "snapshot": self._adapter.snapshot().to_dict()}
        if method == "graph.buildFromGoal":
            return self._graph_build_from_goal(params)
        if method == "graph.matchLibrary":
            return self._graph_match_library(params)
        if method == "graph.previewBuildPlan":
            return self._graph_preview_build_plan(params)
        if method == "graph.applyBuildPlan":
            return self._graph_apply_build_plan(params)
        if method == "graph.debugService":
            return self._graph_debug_service(params)
        if method == "graph.autoLayout":
            return self._graph_auto_layout(params)
        if method == "graph.fixContainerBindings":
            return self._graph_fix_container_bindings(params)
        if method == "graph.compile":
            return {"compile": self._adapter.compile_graph()}
        if method == "studio.status":
            return self._studio_status()
        if method == "library.services":
            return service_library_payload(params)
        if method == "library.operators":
            return operator_library_payload(params)
        if method == "library.operatorDetail":
            return operator_detail_payload(params)
        if method == "runtime.deploy":
            return self._runtime_deploy(params)
        if method == "runtime.serviceDeploy":
            return self._runtime_service_deploy(params)
        if method == "runtime.services":
            return self._runtime_services()
        if method == "runtime.serviceStatus":
            return self._runtime_service_status(params)
        if method == "runtime.setServiceActive":
            return self._runtime_set_service_active(params)
        if method == "runtime.setManagedActive":
            return self._runtime_set_managed_active(params)
        if method == "runtime.serviceProcess":
            return self._runtime_service_process(params)
        if method == "runtime.writeState":
            return self._runtime_write_state(params)
        if method == "runtime.readState":
            return self._runtime_read_state(params)
        if method == "runtime.watchState":
            return self._runtime_watch_state(params)
        if method == "runtime.samplePort":
            return self._runtime_sample_port(params)
        if method == "runtime.invokeCommand":
            return self._runtime_invoke_command(params)
        if method == "monitor.report":
            return self._monitor_report()
        if method == "monitor.service":
            return self._monitor_service(params)
        if method == "logs.read":
            return self._logs_read(params)
        raise ValueError(f"unsupported local graph agent tool method: {method}")

    def _require_bridge(self) -> StudioRuntimeToolBridge:
        bridge = self._bridge
        if bridge is None:
            raise RuntimeError("runtime bridge is not available for this Studio agent tool")
        return bridge

    def _require_observation_source(self) -> StudioRuntimeObservationSource:
        source = self._observation_source
        if source is None:
            raise RuntimeError("runtime observation store is not available for this Studio agent tool")
        return source

    def _studio_status(self) -> dict[str, Any]:
        snapshot = self._adapter.snapshot()
        status = {
            "graphRevision": self._adapter.revision(),
            "graph": {
                "nodeCount": snapshot.node_count,
                "edgeCount": snapshot.edge_count,
            },
            "runtime": None,
        }
        bridge = self._bridge
        if bridge is not None:
            status["runtime"] = {
                "studioServiceId": bridge.studio_service_id,
                "managedActive": bridge.managed_active,
            }
        return {"status": status}

    def _graph_ui_context(self) -> dict[str, Any]:
        source = self._ui_context_source
        if source is not None:
            payload = source.graph_ui_context()
            if isinstance(payload, dict):
                return dict(payload)
        snapshot = self._adapter.snapshot()
        selected_node_ids = list(snapshot.selected_node_ids)
        primary_node_id = selected_node_ids[0] if len(selected_node_ids) == 1 else ""
        return {
            "graphRevision": snapshot.revision,
            "selectedNodeIds": selected_node_ids,
            "selectionLabel": _selection_label_from_node_ids(selected_node_ids),
            "selectionCount": len(selected_node_ids),
            "propertyPanelNodeId": "",
            "primaryNodeId": primary_node_id,
            "primaryNodeSource": "singleSelection" if primary_node_id else "none",
        }

    def _runtime_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        if not bool(params.get("confirm")):
            raise ValueError("runtime_deploy requires confirm=true")
        bridge = self._require_bridge()
        compiled = self._adapter.compile_graph()
        from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio

        compiled_graphs = compile_runtime_graphs_from_studio(self._studio_graph)
        return {
            "deploy": bridge.deploy_and_wait(compiled_graphs, timeout_s=float(params.get("timeoutS") or 20.0)),
            "compile": compiled,
        }

    def _runtime_service_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio

        compiled_graphs = compile_runtime_graphs_from_studio(self._studio_graph)
        return {
            "deploy": bridge.deploy_service_and_wait(
                service_id,
                compiled=compiled_graphs,
                timeout_s=float(params.get("timeoutS") or 10.0),
            )
        }

    def _runtime_services(self) -> dict[str, Any]:
        bridge = self._require_bridge()
        rows = [_monitor_row_to_dict(row) for row in bridge.list_service_monitor_rows()]
        return {"services": rows}

    def _runtime_service_status(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = str(params.get("serviceId") or "").strip() or bridge.studio_service_id
        return {
            "service": {
                "serviceId": service_id,
                "serviceClass": bridge.get_service_class(service_id),
                "running": bridge.is_service_running(service_id),
                "active": bridge.get_cached_service_active(service_id),
                "latestMonitor": bridge.get_latest_monitor_snapshot(service_id),
            }
        }

    def _runtime_set_service_active(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        active = bool(params.get("active"))
        bridge.set_service_active(service_id, active)
        return {"submitted": True, "serviceId": service_id, "active": active}

    def _runtime_set_managed_active(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        active = bool(params.get("active"))
        bridge.set_managed_active(active)
        return {"submitted": True, "active": active}

    def _runtime_service_process(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        action = str(params.get("action") or "").strip().lower()
        service_class = str(params.get("serviceClass") or "").strip() or None
        if action == "start":
            bridge.start_service(service_id, service_class=service_class)
        elif action == "stop":
            bridge.stop_service(service_id)
        elif action == "restart":
            bridge.restart_service(service_id, service_class=service_class)
        else:
            raise ValueError("runtime_service_process action must be start, stop, or restart")
        return {"submitted": True, "serviceId": service_id, "action": action}

    def _runtime_write_state(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        node_id = _required_text(params, "nodeId")
        field = _required_text(params, "field")
        if "value" not in params:
            raise ValueError("runtime_write_state requires value")
        return {
            "state": bridge.set_remote_state_and_wait(
                service_id,
                node_id,
                field,
                params.get("value"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _runtime_read_state(self, params: dict[str, Any]) -> dict[str, Any]:
        source = self._require_observation_source()
        state = source.get_state(
            service_id=_required_text(params, "serviceId"),
            node_id=_required_text(params, "nodeId"),
            field=_required_text(params, "field"),
        )
        return {"state": _stored_state_to_dict(state)}

    def _runtime_watch_state(self, params: dict[str, Any]) -> dict[str, Any]:
        source = self._require_observation_source()
        state = source.wait_state(
            service_id=_required_text(params, "serviceId"),
            node_id=_required_text(params, "nodeId"),
            field=_required_text(params, "field"),
            after_ts_ms=_optional_int_param(params, "afterTsMs"),
            timeout_s=float(params.get("timeoutS") or (float(params.get("durationMs") or 1000.0) / 1000.0)),
        )
        return {"state": _stored_state_to_dict(state)}

    def _runtime_sample_port(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        node_id = _required_text(params, "nodeId")
        port = _required_text(params, "port")
        return {
            "samples": bridge.sample_data_port_and_wait(
                service_id,
                node_id,
                port,
                limit=int(params.get("limit") or 1),
                timeout_s=float(params.get("timeoutS") or 2.0),
                include_value=bool(params.get("includeValue", True)),
                max_value_bytes=int(params.get("maxValueBytes") or 65536),
            )
        }

    def _runtime_invoke_command(self, params: dict[str, Any]) -> dict[str, Any]:
        if not bool(params.get("confirm")):
            raise ValueError("runtime_invoke_command requires confirm=true")
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        call = _required_text(params, "call")
        return {
            "command": bridge.invoke_remote_command_and_wait(
                service_id,
                call,
                params.get("args"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _monitor_report(self) -> dict[str, Any]:
        bridge = self._require_bridge()
        return {"monitor": bridge.export_monitor_report()}

    def _monitor_service(self, params: dict[str, Any]) -> dict[str, Any]:
        bridge = self._require_bridge()
        service_id = _required_text(params, "serviceId")
        limit = int(params.get("limit") or 500)
        return {
            "latest": bridge.get_latest_monitor_snapshot(service_id),
            "stream": bridge.get_monitor_snapshot_stream(service_id, limit=limit),
        }

    def _logs_read(self, params: dict[str, Any]) -> dict[str, Any]:
        source = self._log_source
        if source is None:
            raise RuntimeError("service log source is not available for this Studio agent tool")
        minimum_level = params.get("minimumLevel")
        resolved_minimum_level = int(minimum_level) if minimum_level is not None else None
        return {
            "logs": source.export_logs(
                service_id=str(params.get("serviceId") or ""),
                limit=int(params.get("limit") or 200),
                minimum_level=resolved_minimum_level,
            )
        }

    def _graph_build_from_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        goal = str(params.get("goal") or "").strip()
        if not goal:
            raise ValueError("graph_build_from_goal requires goal")
        matches = match_graph_library_candidates(
            goal=goal,
            node_catalog=self._adapter.node_catalog(),
            limit=int(params.get("limit") or 24),
        )
        return {
            "workflow": {
                "name": "graph_build_from_goal",
                "status": "planning_required",
                "summary": "Matched graph library candidates. Create a GraphBuildPlan, then call graph_preview_build_plan.",
                "goal": goal,
                "libraryMatches": matches.to_dict(),
                "planSchema": graph_build_plan_schema_hint(),
                "nextTools": ["graph_preview_build_plan", "graph_apply_build_plan", "graph_debug_service"],
            }
        }

    def _graph_match_library(self, params: dict[str, Any]) -> dict[str, Any]:
        goal = _required_text(params, "goal")
        matches = match_graph_library_candidates(
            goal=goal,
            node_catalog=self._adapter.node_catalog(),
            limit=int(params.get("limit") or 24),
        )
        return {"matches": matches.to_dict()}

    def _graph_preview_build_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        plan = decode_graph_build_plan(params.get("plan"))
        patch = graph_patch_from_build_plan(plan, expected_revision=self._adapter.revision())
        preview = self._adapter.preview_patch(patch)
        delivery = delivery_report_for_plan(plan=plan, preview=preview.to_dict(), applied=False)
        return {
            "workflow": {
                "name": "graph_preview_build_plan",
                "status": "previewed",
                "summary": "Previewed typed GraphBuildPlan.",
                "plan": plan.to_dict(),
                "patch": graph_patch_to_dict(patch),
                "preview": preview.to_dict(),
                "delivery": delivery.to_dict(),
            }
        }

    def _graph_apply_build_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        if not bool(params.get("confirm")):
            raise ValueError("graph_apply_build_plan requires confirm=true")
        plan = decode_graph_build_plan(params.get("plan"))
        patch = graph_patch_from_build_plan(plan, expected_revision=self._adapter.revision())
        preview = self._adapter.apply_patch(patch)
        self._notify_graph_patch_applied()
        diagnostics = self._adapter.diagnostics()
        delivery = delivery_report_for_plan(
            plan=plan,
            preview=preview.to_dict(),
            applied=True,
            diagnostics=diagnostics,
        )
        return {
            "workflow": {
                "name": "graph_apply_build_plan",
                "status": "applied",
                "summary": "Applied typed GraphBuildPlan.",
                "plan": plan.to_dict(),
                "patch": graph_patch_to_dict(patch),
                "preview": preview.to_dict(),
                "diagnostics": diagnostics,
                "delivery": delivery.to_dict(),
                "snapshot": self._adapter.snapshot().to_dict(),
            }
        }

    def _graph_debug_service(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        status: dict[str, Any] | None = None
        monitor: dict[str, Any] | None = None
        logs: dict[str, Any] | None = None
        if service_id:
            if self._bridge is not None:
                status = self._runtime_service_status({"serviceId": service_id})["service"]
                monitor = self._monitor_service({"serviceId": service_id, "limit": int(params.get("limit") or 100)})
            if self._log_source is not None:
                logs = self._logs_read({"serviceId": service_id, "limit": int(params.get("logLimit") or 100)})["logs"]
        diagnostics = self._adapter.diagnostics()
        compile_payload = self._adapter.compile_graph()
        issue_count = len(list(diagnostics.get("issues") or []))
        return {
            "workflow": {
                "name": "graph_debug_service",
                "status": "completed",
                "summary": f"Collected service debug bundle; diagnostics issues={issue_count}.",
                "serviceId": service_id,
                "service": status,
                "monitor": monitor,
                "logs": logs,
                "diagnostics": diagnostics,
                "compile": compile_payload,
            }
        }

    def _graph_auto_layout(self, params: dict[str, Any]) -> dict[str, Any]:
        selected_only = bool(params.get("selectedOnly", False))
        apply_layout = bool(params.get("apply", False)) or bool(params.get("confirm", False))
        snapshot = self._adapter.snapshot()
        nodes = [node for node in snapshot.nodes if not selected_only or node.selected]
        if not nodes:
            return {
                "workflow": {
                    "name": "graph_auto_layout",
                    "status": "no_nodes",
                    "summary": "No nodes matched the layout scope.",
                    "patch": graph_patch_to_dict(GraphPatch(expected_revision=snapshot.revision, ops=(), label="auto layout")),
                }
            }
        columns = max(1, int(math.ceil(math.sqrt(float(len(nodes))))))
        spacing_x = float(params.get("spacingX") or 260.0)
        spacing_y = float(params.get("spacingY") or 150.0)
        origin_x = float(params.get("originX") or 0.0)
        origin_y = float(params.get("originY") or 0.0)
        ops: list[MoveNodeOp] = []
        for index, node in enumerate(sorted(nodes, key=lambda item: item.node_id)):
            row = index // columns
            col = index % columns
            ops.append(MoveNodeOp(node_id=node.node_id, pos=(origin_x + col * spacing_x, origin_y + row * spacing_y)))
        patch = GraphPatch(expected_revision=snapshot.revision, label="agent auto layout", ops=tuple(ops))
        patch_payload = graph_patch_to_dict(patch)
        preview = self._adapter.preview_patch(patch)
        workflow = {
            "name": "graph_auto_layout",
            "status": "previewed",
            "summary": f"Prepared auto layout for {len(ops)} nodes.",
            "patch": patch_payload,
            "preview": preview.to_dict(),
        }
        if not apply_layout:
            return {"workflow": workflow}
        if not bool(params.get("confirm")):
            raise ValueError("graph_auto_layout apply requires confirm=true")
        applied = self._adapter.apply_patch(patch)
        self._notify_graph_patch_applied()
        workflow["status"] = "applied"
        workflow["summary"] = f"Applied auto layout to {len(ops)} nodes."
        workflow["applyPreview"] = applied.to_dict()
        return {"workflow": workflow}

    def _graph_fix_container_bindings(self, params: dict[str, Any]) -> dict[str, Any]:
        apply_fix = bool(params.get("apply", False)) or bool(params.get("confirm", False))
        snapshot = self._adapter.snapshot()
        diagnostics = self._adapter.diagnostics()
        issues = [issue for issue in list(diagnostics.get("issues") or []) if isinstance(issue, dict)]
        services_by_class: dict[str, str] = {}
        for node in snapshot.nodes:
            if node.kind == "service" and node.service_class:
                services_by_class.setdefault(node.service_class, node.node_id)

        ops: list[SetNodeStateOp] = []
        unresolved: list[dict[str, Any]] = []
        for issue in issues:
            code = str(issue.get("code") or "")
            if code not in {
                "operator_missing_service_container",
                "operator_service_container_missing",
                "operator_service_class_mismatch",
            }:
                continue
            node_id = str(issue.get("nodeId") or "").strip()
            details = issue.get("details")
            detail_map = details if isinstance(details, dict) else {}
            service_class = str(detail_map.get("serviceClass") or detail_map.get("operatorServiceClass") or "").strip()
            service_id = str(params.get("serviceId") or "").strip() or services_by_class.get(service_class, "")
            if not node_id or not service_id:
                unresolved.append({"nodeId": node_id, "serviceClass": service_class, "code": code})
                continue
            ops.append(SetNodeStateOp(node_id=node_id, field="svcId", value=service_id))

        patch = GraphPatch(expected_revision=snapshot.revision, label="agent fix container bindings", ops=tuple(ops))
        patch_payload = graph_patch_to_dict(patch)
        preview = self._adapter.preview_patch(patch) if ops else None
        workflow: dict[str, Any] = {
            "name": "graph_fix_container_bindings",
            "status": "previewed" if ops else "no_fix_available",
            "summary": f"Prepared {len(ops)} service-container binding fixes; unresolved={len(unresolved)}.",
            "patch": patch_payload,
            "preview": None if preview is None else preview.to_dict(),
            "unresolved": unresolved,
        }
        if not apply_fix:
            return {"workflow": workflow}
        if not ops:
            return {"workflow": workflow}
        if not bool(params.get("confirm")):
            raise ValueError("graph_fix_container_bindings apply requires confirm=true")
        applied = self._adapter.apply_patch(patch)
        self._notify_graph_patch_applied()
        workflow["status"] = "applied"
        workflow["summary"] = f"Applied {len(ops)} service-container binding fixes."
        workflow["applyPreview"] = applied.to_dict()
        workflow["diagnosticsAfter"] = self._adapter.diagnostics()
        return {"workflow": workflow}

    def _notify_graph_patch_applied(self) -> None:
        callback = self._on_graph_patch_applied
        if callback is None:
            return
        try:
            callback()
        except _GRAPH_TOOL_ERRORS:
            logger.exception("local graph agent tool failed to notify graph patch callback")


@dataclass(frozen=True)
class LocalStudioGraphTools:
    executor: StudioGraphToolExecutor

    def available_tools(self) -> tuple[Callable[..., dict[str, Any]], ...]:
        return (
            self.studio_status,
            self.graph_ui_context,
            self.graph_snapshot,
            self.graph_find_nodes,
            self.graph_node_detail,
            self.graph_connections,
            self.graph_diagnostics,
            self.node_catalog,
            self.service_library,
            self.operator_library,
            self.operator_detail,
            self.graph_session,
            self.graph_compile,
            self.graph_preview_patch,
            self.graph_apply_patch,
            self.graph_build_from_goal,
            self.graph_match_library,
            self.graph_preview_build_plan,
            self.graph_apply_build_plan,
            self.graph_debug_service,
            self.graph_auto_layout,
            self.graph_fix_container_bindings,
            self.runtime_deploy,
            self.runtime_service_deploy,
            self.runtime_services,
            self.runtime_service_status,
            self.runtime_set_service_active,
            self.runtime_set_managed_active,
            self.runtime_service_process,
            self.runtime_write_state,
            self.runtime_read_state,
            self.runtime_watch_state,
            self.runtime_sample_port,
            self.runtime_invoke_command,
            self.monitor_report,
            self.monitor_service,
            self.logs_read,
        )

    def available_codeact_diagnostic_tools(self) -> tuple[Callable[..., dict[str, Any]], ...]:
        return (
            self.studio_status,
            self.graph_ui_context,
            self.graph_snapshot,
            self.graph_find_nodes,
            self.graph_node_detail,
            self.graph_connections,
            self.graph_diagnostics,
            self.node_catalog,
            self.service_library,
            self.operator_library,
            self.operator_detail,
            self.graph_session,
            self.graph_compile,
            self.graph_preview_patch,
            self.graph_build_from_goal,
            self.graph_match_library,
            self.graph_preview_build_plan,
            self.graph_debug_service,
            self.runtime_services,
            self.runtime_service_status,
            self.runtime_read_state,
            self.runtime_watch_state,
            self.runtime_sample_port,
            self.monitor_report,
            self.monitor_service,
            self.logs_read,
        )

    def available_tool_names(self) -> tuple[str, ...]:
        return (
            "studio_status",
            "graph_ui_context",
            "graph_snapshot",
            "graph_find_nodes",
            "graph_node_detail",
            "graph_connections",
            "graph_diagnostics",
            "node_catalog",
            "service_library",
            "operator_library",
            "operator_detail",
            "graph_session",
            "graph_compile",
            "graph_preview_patch",
            "graph_apply_patch",
            "graph_build_from_goal",
            "graph_match_library",
            "graph_preview_build_plan",
            "graph_apply_build_plan",
            "graph_debug_service",
            "graph_auto_layout",
            "graph_fix_container_bindings",
            "runtime_deploy",
            "runtime_service_deploy",
            "runtime_services",
            "runtime_service_status",
            "runtime_set_service_active",
            "runtime_set_managed_active",
            "runtime_service_process",
            "runtime_write_state",
            "runtime_read_state",
            "runtime_watch_state",
            "runtime_sample_port",
            "runtime_invoke_command",
            "monitor_report",
            "monitor_service",
            "logs_read",
        )

    def available_codeact_diagnostic_tool_names(self) -> tuple[str, ...]:
        return (
            "studio_status",
            "graph_ui_context",
            "graph_snapshot",
            "graph_find_nodes",
            "graph_node_detail",
            "graph_connections",
            "graph_diagnostics",
            "node_catalog",
            "service_library",
            "operator_library",
            "operator_detail",
            "graph_session",
            "graph_compile",
            "graph_preview_patch",
            "graph_build_from_goal",
            "graph_match_library",
            "graph_preview_build_plan",
            "graph_debug_service",
            "runtime_services",
            "runtime_service_status",
            "runtime_read_state",
            "runtime_watch_state",
            "runtime_sample_port",
            "monitor_report",
            "monitor_service",
            "logs_read",
        )

    def studio_status(self) -> dict[str, Any]:
        """Return Studio graph/runtime status, including graph revision and runtime management state."""
        return self.executor.call("studio.status")

    def graph_ui_context(self) -> dict[str, Any]:
        """Return lightweight UI focus state: selected node ids, property panel node, and primary current node."""
        return self.executor.call("graph.uiContext")

    def graph_snapshot(self) -> dict[str, Any]:
        """Return the current PyStudio graph snapshot, including nodes, selected nodes, ports, and edges."""
        return self.executor.call("graph.snapshot")

    def graph_find_nodes(
        self,
        query: str = "",
        node_id: str = "",
        node_type: str = "",
        kind: str = "",
        service_class: str = "",
        operator_class: str = "",
        selected_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find graph nodes by text, id, node type, kind, service class, operator class, or selection state."""
        return self.executor.call(
            "graph.findNodes",
            {
                "query": query,
                "nodeId": node_id,
                "nodeType": node_type,
                "kind": kind,
                "serviceClass": service_class,
                "operatorClass": operator_class,
                "selectedOnly": bool(selected_only),
                "limit": int(limit),
            },
        )

    def graph_node_detail(self, node_id: str) -> dict[str, Any]:
        """Return detailed node metadata, state values, runtime binding, UI payload, spec, and connections."""
        return self.executor.call("graph.nodeDetail", {"nodeId": node_id})

    def graph_connections(self, node_id: str = "", direction: str = "both", limit: int = 200) -> dict[str, Any]:
        """Return graph connections, optionally filtered to one node and incoming/outgoing direction."""
        return self.executor.call(
            "graph.connections",
            {"nodeId": node_id, "direction": direction, "limit": int(limit)},
        )

    def graph_diagnostics(self) -> dict[str, Any]:
        """Return graph diagnostics, including service-container binding issues and compile warnings/errors."""
        return self.executor.call("graph.diagnostics")

    def node_catalog(self) -> dict[str, Any]:
        """Return the graph node factory catalog so an agent can choose valid canvas node types and ports."""
        return self.executor.call("graph.catalog")

    def service_library(self, query: str = "", limit: int = 200) -> dict[str, Any]:
        """Return registered service specs from the service library."""
        return self.executor.call("library.services", {"query": query, "limit": int(limit)})

    def operator_library(self, service_class: str = "", query: str = "", limit: int = 300) -> dict[str, Any]:
        """Return registered operator specs, optionally filtered by service class or search query."""
        return self.executor.call(
            "library.operators",
            {"serviceClass": service_class, "query": query, "limit": int(limit)},
        )

    def operator_detail(self, service_class: str, operator_class: str) -> dict[str, Any]:
        """Return detailed service/operator schema for a specific operator."""
        return self.executor.call(
            "library.operatorDetail",
            {"serviceClass": service_class, "operatorClass": operator_class},
        )

    def graph_session(self) -> dict[str, Any]:
        """Return the current serialized PyStudio graph session payload."""
        return self.executor.call("graph.session")

    def graph_compile(self) -> dict[str, Any]:
        """Compile the current PyStudio graph and return service, node, edge, and warning metadata."""
        return self.executor.call("graph.compile")

    def graph_preview_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate a GraphPatch against the current graph without committing the change."""
        return self.executor.call("graph.previewPatch", {"patch": patch})

    def graph_apply_patch(self, patch: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        """Apply a non-destructive GraphPatch to the current graph; destructive patches require confirm=true."""
        return self.executor.call("graph.applyPatch", {"patch": patch, "confirm": bool(confirm)})

    def graph_build_from_goal(
        self,
        goal: str,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Match graph library candidates for a natural-language goal and return the typed GraphBuildPlan schema."""
        return self.executor.call(
            "graph.buildFromGoal",
            {"goal": goal, "limit": int(limit)},
        )

    def graph_match_library(self, goal: str, limit: int = 24) -> dict[str, Any]:
        """Search the node catalog for service/operator candidates relevant to a graph build goal."""
        return self.executor.call("graph.matchLibrary", {"goal": goal, "limit": int(limit)})

    def graph_preview_build_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Convert a typed GraphBuildPlan into a GraphPatch and validate it without changing the graph."""
        return self.executor.call("graph.previewBuildPlan", {"plan": plan})

    def graph_apply_build_plan(self, plan: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        """Apply a typed GraphBuildPlan to the current graph; requires confirm=true or GUI approval."""
        return self.executor.call("graph.applyBuildPlan", {"plan": plan, "confirm": bool(confirm)})

    def graph_debug_service(self, service_id: str = "", limit: int = 100, log_limit: int = 100) -> dict[str, Any]:
        """Collect diagnostics, compile metadata, monitor snapshots, and logs for a service debugging pass."""
        return self.executor.call(
            "graph.debugService",
            {"serviceId": service_id, "limit": int(limit), "logLimit": int(log_limit)},
        )

    def graph_auto_layout(
        self,
        selected_only: bool = False,
        apply: bool = False,
        confirm: bool = False,
        spacing_x: float = 260.0,
        spacing_y: float = 150.0,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
    ) -> dict[str, Any]:
        """Preview or apply a simple typed graph auto-layout patch."""
        return self.executor.call(
            "graph.autoLayout",
            {
                "selectedOnly": bool(selected_only),
                "apply": bool(apply),
                "confirm": bool(confirm),
                "spacingX": float(spacing_x),
                "spacingY": float(spacing_y),
                "originX": float(origin_x),
                "originY": float(origin_y),
            },
        )

    def graph_fix_container_bindings(
        self,
        service_id: str = "",
        apply: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Preview or apply fixes for operator nodes missing valid service-container bindings."""
        return self.executor.call(
            "graph.fixContainerBindings",
            {"serviceId": service_id, "apply": bool(apply), "confirm": bool(confirm)},
        )

    def runtime_deploy(self, confirm: bool = False, timeout_s: float = 20.0) -> dict[str, Any]:
        """Compile and deploy the current graph to the runtime; requires confirm=true."""
        return self.executor.call("runtime.deploy", {"confirm": bool(confirm), "timeoutS": float(timeout_s)})

    def runtime_service_deploy(self, service_id: str, timeout_s: float = 10.0) -> dict[str, Any]:
        """Compile and deploy the current per-service rungraph for one service instance."""
        return self.executor.call("runtime.serviceDeploy", {"serviceId": service_id, "timeoutS": float(timeout_s)})

    def runtime_services(self) -> dict[str, Any]:
        """Return the service monitor table rows for known Studio/runtime services."""
        return self.executor.call("runtime.services")

    def runtime_service_status(self, service_id: str = "") -> dict[str, Any]:
        """Return best-effort running/active/latest-monitor status for one service."""
        return self.executor.call("runtime.serviceStatus", {"serviceId": service_id})

    def runtime_set_service_active(self, service_id: str, active: bool) -> dict[str, Any]:
        """Set a managed runtime service active or inactive."""
        return self.executor.call("runtime.setServiceActive", {"serviceId": service_id, "active": bool(active)})

    def runtime_set_managed_active(self, active: bool) -> dict[str, Any]:
        """Set all managed runtime services active or inactive."""
        return self.executor.call("runtime.setManagedActive", {"active": bool(active)})

    def runtime_service_process(self, service_id: str, action: str, service_class: str = "") -> dict[str, Any]:
        """Start, stop, or restart a managed service process."""
        return self.executor.call(
            "runtime.serviceProcess",
            {"serviceId": service_id, "action": action, "serviceClass": service_class},
        )

    def runtime_write_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        """Write a remote runtime state field and wait for the request result."""
        return self.executor.call(
            "runtime.writeState",
            {"serviceId": service_id, "nodeId": node_id, "field": field, "value": value, "timeoutS": float(timeout_s)},
        )

    def runtime_read_state(self, service_id: str, node_id: str, field: str) -> dict[str, Any]:
        """Read the latest observed runtime state value from the local observation store."""
        return self.executor.call("runtime.readState", {"serviceId": service_id, "nodeId": node_id, "field": field})

    def runtime_watch_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        after_ts_ms: int | None = None,
        timeout_s: float = 1.0,
    ) -> dict[str, Any]:
        """Wait for a runtime state observation newer than after_ts_ms, or return the latest observed value on timeout."""
        params: dict[str, Any] = {
            "serviceId": service_id,
            "nodeId": node_id,
            "field": field,
            "timeoutS": float(timeout_s),
        }
        if after_ts_ms is not None:
            params["afterTsMs"] = int(after_ts_ms)
        return self.executor.call("runtime.watchState", params)

    def runtime_sample_port(
        self,
        service_id: str,
        node_id: str,
        port: str,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
    ) -> dict[str, Any]:
        """Subscribe briefly and sample data from a runtime output port."""
        return self.executor.call(
            "runtime.samplePort",
            {
                "serviceId": service_id,
                "nodeId": node_id,
                "port": port,
                "limit": int(limit),
                "timeoutS": float(timeout_s),
                "includeValue": bool(include_value),
                "maxValueBytes": int(max_value_bytes),
            },
        )

    def runtime_invoke_command(
        self,
        service_id: str,
        call: str,
        args: Any = None,
        confirm: bool = False,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        """Invoke a runtime command on a remote service; requires confirm=true."""
        return self.executor.call(
            "runtime.invokeCommand",
            {"serviceId": service_id, "call": call, "args": args, "confirm": bool(confirm), "timeoutS": float(timeout_s)},
        )

    def monitor_report(self) -> dict[str, Any]:
        """Return the Studio monitor report for all known services."""
        return self.executor.call("monitor.report")

    def monitor_service(self, service_id: str, limit: int = 500) -> dict[str, Any]:
        """Return latest monitor snapshot and recent monitor stream for one service."""
        return self.executor.call("monitor.service", {"serviceId": service_id, "limit": int(limit)})

    def logs_read(self, service_id: str = "", limit: int = 200, minimum_level: int | None = None) -> dict[str, Any]:
        """Read recent Studio/service log lines captured by the Service Logs dock."""
        params: dict[str, Any] = {"serviceId": service_id, "limit": int(limit)}
        if minimum_level is not None:
            params["minimumLevel"] = int(minimum_level)
        return self.executor.call("logs.read", params)


def _requires_confirm(params: dict[str, Any]) -> bool:
    patch = params.get("patch")
    if not isinstance(patch, dict):
        return True
    ops = patch.get("ops")
    if not isinstance(ops, list):
        return True
    for op in ops:
        if not isinstance(op, dict):
            return True
        if str(op.get("op") or "") == "deleteNode":
            return True
    return False


def _approval_spec_for_method(method: str, params: dict[str, Any]) -> _ToolApprovalSpec | None:
    if method == "graph.applyPatch" and _requires_confirm(params):
        return _ToolApprovalSpec(
            title="Apply Graph Patch",
            description="This patch may delete nodes or perform a destructive graph change.",
            confirm_error_message="graph.applyPatch with destructive ops requires confirm=true",
        )
    if method == "runtime.deploy":
        return _ToolApprovalSpec(
            title="Deploy Runtime Graph",
            description="Compile and deploy the current graph to the runtime.",
            confirm_error_message="runtime_deploy requires confirm=true",
        )
    if method == "runtime.serviceDeploy":
        return _ToolApprovalSpec(
            title="Deploy Runtime Service",
            description="Compile and deploy one service rungraph to the runtime.",
            confirm_error_message="runtime_service_deploy requires confirm=true",
            require_confirm_without_callback=False,
        )
    if method == "runtime.setServiceActive":
        return _ToolApprovalSpec(
            title="Set Service Active",
            description="Change whether a managed runtime service is active.",
            confirm_error_message="runtime_set_service_active requires confirm=true",
            require_confirm_without_callback=False,
        )
    if method == "runtime.setManagedActive":
        return _ToolApprovalSpec(
            title="Set Managed Services Active",
            description="Change active state for all managed runtime services.",
            confirm_error_message="runtime_set_managed_active requires confirm=true",
            require_confirm_without_callback=False,
        )
    if method == "runtime.serviceProcess":
        return _ToolApprovalSpec(
            title="Control Service Process",
            description="Start, stop, or restart a managed service process.",
            confirm_error_message="runtime_service_process requires confirm=true",
            require_confirm_without_callback=False,
        )
    if method == "runtime.writeState":
        return _ToolApprovalSpec(
            title="Write Runtime State",
            description="Write a state value to a running runtime node.",
            confirm_error_message="runtime_write_state requires confirm=true",
            require_confirm_without_callback=False,
        )
    if method == "runtime.invokeCommand":
        return _ToolApprovalSpec(
            title="Invoke Runtime Command",
            description="Call a command on a running runtime service.",
            confirm_error_message="runtime_invoke_command requires confirm=true",
        )
    if method == "graph.applyBuildPlan":
        return _ToolApprovalSpec(
            title="Apply Graph Build Plan",
            description="Create and connect nodes from the typed graph build plan.",
            confirm_error_message="graph_apply_build_plan requires confirm=true",
        )
    if method == "graph.autoLayout" and bool(params.get("apply", False)):
        return _ToolApprovalSpec(
            title="Apply Auto Layout",
            description="Move graph nodes to the generated layout positions.",
            confirm_error_message="graph_auto_layout apply requires confirm=true",
        )
    if method == "graph.fixContainerBindings" and bool(params.get("apply", False)):
        return _ToolApprovalSpec(
            title="Fix Container Bindings",
            description="Update operator svcId state fields to bind them to service containers.",
            confirm_error_message="graph_fix_container_bindings apply requires confirm=true",
        )
    return None


def _stored_state_to_dict(value: StoredStateValue | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "serviceId": value.service_id,
        "nodeId": value.node_id,
        "field": value.field,
        "value": value.value,
        "tsMs": value.ts_ms,
    }


def _selection_label_from_node_ids(node_ids: list[str]) -> str:
    if not node_ids:
        return ""
    if len(node_ids) == 1:
        return str(node_ids[0])
    return f"{len(node_ids)} selected nodes"


def _optional_int_param(params: dict[str, Any], key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _required_text(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _monitor_row_to_dict(row: Any) -> dict[str, Any]:
    try:
        return asdict(row)
    except (TypeError, ValueError):
        return {
            "serviceId": str(row.service_id),
            "serviceClass": str(row.service_class),
            "running": bool(row.running),
        }
