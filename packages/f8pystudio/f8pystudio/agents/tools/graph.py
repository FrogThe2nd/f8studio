from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from qtpy import QtCore

from f8pystudio.automation.domain import decode_graph_patch
from f8pystudio.automation.graph_adapter import StudioGraphAutomationAdapter
from f8pystudio.automation.library_catalog import (
    operator_detail_payload,
    operator_library_payload,
    service_library_payload,
)

logger = logging.getLogger(__name__)
_GRAPH_TOOL_ERRORS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)


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


class LocalStudioGraphToolExecutor(QtCore.QObject):
    _call_requested = QtCore.Signal(str, object, object)

    def __init__(
        self,
        studio_graph: object,
        *,
        bridge: StudioRuntimeToolBridge | None = None,
        log_source: StudioLogToolSource | None = None,
        on_graph_patch_applied: Callable[[], None] | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._studio_graph = studio_graph
        self._adapter = StudioGraphAutomationAdapter(studio_graph)
        self._bridge = bridge
        self._log_source = log_source
        self._on_graph_patch_applied = on_graph_patch_applied
        self._call_requested.connect(
            self._handle_call_on_qt_thread,
            QtCore.Qt.ConnectionType.BlockingQueuedConnection,
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params_dict = dict(params or {})
        if QtCore.QThread.currentThread() == self.thread():
            return self._dispatch(str(method), params_dict)
        response_box: dict[str, Any] = {}
        self._call_requested.emit(str(method), params_dict, response_box)
        error = response_box.get("error")
        if isinstance(error, BaseException):
            raise error
        result = response_box.get("result")
        if isinstance(result, dict):
            return dict(result)
        return {}

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
            self.runtime_deploy,
            self.runtime_service_deploy,
            self.runtime_services,
            self.runtime_service_status,
            self.runtime_set_service_active,
            self.runtime_set_managed_active,
            self.runtime_service_process,
            self.runtime_write_state,
            self.runtime_sample_port,
            self.runtime_invoke_command,
            self.monitor_report,
            self.monitor_service,
            self.logs_read,
        )

    def studio_status(self) -> dict[str, Any]:
        """Return Studio graph/runtime status, including graph revision and runtime management state."""
        return self.executor.call("studio.status")

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
