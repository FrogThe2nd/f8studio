from __future__ import annotations

import json
import logging
import math
import os
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from qtpy import QtCore

from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio
from f8pystudio.ui.support.ui_notifications import export_recent_notifications

from .client import wait_for_connection_file
from .control_protocol import AutomationConnectionInfo
from .domain import GraphPatch, MoveNodeOp, SetNodeStateOp, decode_graph_patch, graph_patch_to_dict
from .graph_adapter import StudioGraphAutomationAdapter
from .local_server import LocalAutomationServer
from .observation_store import RuntimeObservationStore
from .paths import automation_dir, default_port_file, default_token_file
from .projects import project_list_payload, project_load_payload, project_new_payload, project_save_payload
from f8pystudio.agents.graph_builder import (
    decode_graph_build_plan,
    delivery_report_for_plan,
    graph_build_plan_schema_hint,
    graph_patch_from_build_plan,
    match_graph_library_candidates,
)
from f8pystudio.automation.library_catalog import (
    operator_detail_payload,
    operator_library_payload,
    service_library_payload,
)
from f8pystudio.modding import ModdingAutomationService

logger = logging.getLogger(__name__)
_HOST_METHOD_ERRORS = (Exception,)
_FILE_WRITE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_SERVER_THREAD_METHODS = frozenset(
    {
        "runtime.readState",
        "runtime.watchState",
        "runtime.samplePort",
        "runtime.debugData",
        "modding.verifyStream",
    }
)


class StudioAutomationHost(QtCore.QObject):
    _request = QtCore.Signal(str, object, object)

    def __init__(
        self,
        *,
        main_window: Any,
        studio_graph: Any,
        bridge: Any,
        observation_store: RuntimeObservationStore | None = None,
        token_file: str | Path | None = None,
        port_file: str | Path | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._graph = studio_graph
        self._bridge = bridge
        self._graph_adapter = StudioGraphAutomationAdapter(studio_graph)
        self._observations = observation_store or RuntimeObservationStore()
        self._modding: ModdingAutomationService | None = None
        self._token_file = Path(token_file).expanduser() if token_file is not None else default_token_file()
        self._port_file = Path(port_file).expanduser() if port_file is not None else default_port_file()
        self._token = ""
        self._server: LocalAutomationServer | None = None
        self._request.connect(self._handle_request_on_qt_thread, QtCore.Qt.ConnectionType.BlockingQueuedConnection)

    @property
    def connection_info(self) -> AutomationConnectionInfo | None:
        server = self._server
        if server is None:
            return None
        return AutomationConnectionInfo(
            pid=os.getpid(),
            host=server.host,
            port=server.port,
            token_file=str(self._token_file),
            studio_service_id=str(self._bridge.studio_service_id),
            created_at=int(time.time()),
        )

    @property
    def port_file(self) -> Path:
        return self._port_file

    def start(self) -> AutomationConnectionInfo:
        if self._server is not None:
            info = self.connection_info
            if info is None:
                raise RuntimeError("automation server is started but connection info is unavailable")
            return info
        self._token = secrets.token_urlsafe(32)
        self._write_private_text(self._token_file, self._token + "\n")
        server = LocalAutomationServer(token=self._token, request_handler=self._handle_request_from_server_thread)
        server.start()
        self._server = server
        info = self.connection_info
        if info is None:
            raise RuntimeError("failed to create automation connection info")
        self._write_private_json(self._port_file, info.to_dict())
        logger.info("PyStudio automation listening on %s:%s", info.host, info.port)
        return info

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def record_runtime_state(self, *, service_id: str, node_id: str, field: str, value: Any, ts_ms: int) -> None:
        self._observations.put_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            value=value,
            ts_ms=int(ts_ms),
        )

    @property
    def observation_store(self) -> RuntimeObservationStore:
        return self._observations

    def _handle_request_from_server_thread(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in _SERVER_THREAD_METHODS:
            try:
                return self._dispatch_server_thread(str(method), dict(params))
            except _HOST_METHOD_ERRORS as exc:
                logger.exception("automation server-thread method failed method=%s", method)
                return {
                    "ok": False,
                    "error": {
                        "code": "method_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "details": {"method": str(method)},
                    },
                    "result": {},
                }
        response_box: dict[str, Any] = {}
        self._request.emit(str(method), dict(params), response_box)
        return dict(response_box)

    @QtCore.Slot(str, object, object)
    def _handle_request_on_qt_thread(self, method: str, params: object, response_box: object) -> None:
        if not isinstance(response_box, dict):
            return
        try:
            params_dict = params if isinstance(params, dict) else {}
            response_box.update(self._dispatch(str(method), dict(params_dict)))
        except _HOST_METHOD_ERRORS as exc:
            logger.exception("automation method failed method=%s", method)
            response_box.update(
                {
                    "ok": False,
                    "error": {
                        "code": "method_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "details": {"method": str(method)},
                    },
                    "result": {},
                }
            )

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "studio.status":
            return self._status()
        if method == "graph.snapshot":
            return {"snapshot": self._graph_adapter.snapshot().to_dict()}
        if method == "graph.session":
            return {"session": self._graph_adapter.session_payload()}
        if method == "project.list":
            return project_list_payload()
        if method == "project.new":
            if not bool(params.get("confirm")):
                raise ValueError("project.new requires confirm=true")
            result = project_new_payload(
                self._graph,
                clear_current_project=bool(params.get("clearCurrentProject", True)),
            )
            self._schedule_studio_runtime_sync()
            return result
        if method == "project.save":
            return project_save_payload(
                self._graph,
                name=str(params.get("name") or ""),
                description=str(params.get("description") or ""),
                tags=_string_list_param(params.get("tags")),
                project_id=str(params.get("projectId") or ""),
                overwrite_project_id=str(params.get("overwriteProjectId") or ""),
            )
        if method == "project.load":
            if not bool(params.get("confirm")):
                raise ValueError("project.load requires confirm=true")
            result = project_load_payload(self._graph, project_id=_required_text(params, "projectId"))
            self._schedule_studio_runtime_sync()
            return result
        if method == "modding.detectTarget":
            return self._modding_service().detect_target(target_path=_required_text(params, "targetPath"))
        if method == "modding.previewInstall":
            return self._modding_service().preview_install(
                target_path=_required_text(params, "targetPath"),
                options_payload=_optional_dict_param(params.get("options")),
            )
        if method == "modding.applyInstall":
            if not bool(params.get("confirm")):
                raise ValueError("modding.applyInstall requires confirm=true")
            return self._modding_service().apply_install(
                plan_payload=_required_dict_param(params.get("plan"), "plan"),
                confirm=True,
            )
        if method == "modding.createRecipe":
            if not bool(params.get("confirm")):
                raise ValueError("modding.createRecipe requires confirm=true")
            return self._modding_service().create_recipe(
                name=str(params.get("name") or ""),
                description=str(params.get("description") or ""),
                tags=_string_list_param(params.get("tags")),
                detection_payload=_optional_dict_param(params.get("detection")),
                install_payload=_optional_dict_param(params.get("install")),
                verification_payload=_optional_dict_param(params.get("verification")),
                graph_payload=_optional_dict_param(params.get("graph")),
                notes=str(params.get("notes") or ""),
                confirm=True,
            )
        if method == "modding.recipeList":
            return self._modding_service().recipe_list()
        if method == "modding.recipeLoad":
            return self._modding_service().recipe_load(recipe_id=_required_text(params, "recipeId"))
        if method == "modding.recipeExport":
            if not bool(params.get("confirm")):
                raise ValueError("modding.recipeExport requires confirm=true")
            return self._modding_service().recipe_export(
                recipe_id=_required_text(params, "recipeId"),
                path=_required_text(params, "path"),
                confirm=True,
            )
        if method == "graph.uiContext":
            return {"uiContext": self._graph_ui_context()}
        if method == "graph.catalog":
            return self._graph_adapter.node_catalog()
        if method == "graph.findNodes":
            return self._graph_adapter.find_nodes(
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
            return {"detail": self._graph_adapter.node_detail(_required_text(params, "nodeId"))}
        if method == "graph.connections":
            return self._graph_adapter.connections(
                node_id=str(params.get("nodeId") or ""),
                direction=str(params.get("direction") or "both"),
                limit=int(params.get("limit") or 200),
            )
        if method == "graph.diagnostics":
            return {"diagnostics": self._graph_adapter.diagnostics()}
        if method == "graph.previewPatch":
            patch = decode_graph_patch(params.get("patch"))
            return {"preview": self._graph_adapter.preview_patch(patch).to_dict()}
        if method == "graph.applyPatch":
            if _requires_confirm(params) and not bool(params.get("confirm")):
                raise ValueError("graph.applyPatch with destructive ops requires confirm=true")
            patch = decode_graph_patch(params.get("patch"))
            preview = self._graph_adapter.apply_patch(patch)
            self._schedule_studio_runtime_sync()
            return {"preview": preview.to_dict(), "snapshot": self._graph_adapter.snapshot().to_dict()}
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
            return {"compile": self._graph_adapter.compile_graph()}
        if method == "library.services":
            return service_library_payload(params)
        if method == "library.operators":
            return operator_library_payload(params)
        if method == "library.operatorDetail":
            return operator_detail_payload(params)
        if method == "runtime.deploy":
            if not bool(params.get("confirm")):
                raise ValueError("runtime.deploy requires confirm=true")
            return self._runtime_deploy(params)
        if method == "runtime.serviceDeploy":
            return self._runtime_service_deploy(params)
        if method == "runtime.services":
            return self._runtime_services()
        if method == "runtime.serviceStatus":
            return {"service": self._runtime_service_status(str(params.get("serviceId") or ""))}
        if method == "runtime.setServiceActive":
            return self._runtime_set_service_active(params)
        if method == "runtime.setManagedActive":
            return self._runtime_set_managed_active(params)
        if method == "runtime.serviceProcess":
            return self._runtime_service_process(params)
        if method == "runtime.writeState":
            return self._runtime_write_state(params)
        if method == "runtime.readMonitor":
            return {"monitor": self._runtime_read_monitor(params)}
        if method == "runtime.invokeCommand":
            return self._runtime_invoke_command(params)
        if method == "monitor.report":
            return self._monitor_report()
        if method == "monitor.service":
            return self._monitor_service(params)
        if method == "logs.read":
            return self._logs_read(params)
        if method == "notifications.read":
            return self._notifications_read(params)
        raise ValueError(f"unsupported automation method: {method}")

    def _dispatch_server_thread(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "runtime.readState":
            return {"state": self._runtime_read_state(params)}
        if method == "runtime.watchState":
            return {"state": self._runtime_watch_state(params)}
        if method == "runtime.samplePort":
            return self._runtime_sample_port(params)
        if method == "runtime.debugData":
            return self._runtime_debug_data(params)
        if method == "modding.verifyStream":
            return self._modding_service().verify_stream(
                port=int(params.get("port") or 39540),
                host=str(params.get("host") or "127.0.0.1"),
                timeout_s=float(params.get("timeoutS") or 3.0),
                max_samples=int(params.get("maxSamples") or 8),
            )
        raise ValueError(f"unsupported server-thread automation method: {method}")

    def _status(self) -> dict[str, Any]:
        info = self.connection_info
        return {
            "pid": os.getpid(),
            "automation": info.to_dict() if info is not None else None,
            "graphRevision": self._graph_adapter.revision(),
            "studioServiceId": str(self._bridge.studio_service_id),
        }

    def _modding_service(self) -> ModdingAutomationService:
        service = self._modding
        if service is None:
            service = ModdingAutomationService()
            self._modding = service
        return service

    def _runtime_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_runtime_graphs_from_studio(self._graph)
        wait = bool(params.get("wait", True))
        timeout_s = float(params.get("timeoutS") or 20.0)
        if wait:
            deploy_result = self._bridge.deploy_and_wait(compiled, timeout_s=timeout_s)
        else:
            self._bridge.deploy(compiled)
            deploy_result = {"submitted": True, "completed": False, "error": ""}
        return {
            "deploy": deploy_result,
            "compileWarnings": list(compiled.warnings or ()),
            "compile": self._graph_adapter.compile_graph(),
        }

    def _runtime_service_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = _required_text(params, "serviceId")
        compiled = compile_runtime_graphs_from_studio(self._graph)
        return {
            "deploy": self._bridge.deploy_service_and_wait(
                service_id,
                compiled=compiled,
                timeout_s=float(params.get("timeoutS") or 10.0),
            )
        }

    def _runtime_services(self) -> dict[str, Any]:
        return {"services": [_monitor_row_to_dict(row) for row in self._bridge.list_service_monitor_rows()]}

    def _runtime_set_service_active(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = _required_text(params, "serviceId")
        active = bool(params.get("active"))
        self._bridge.set_service_active(service_id, active)
        return {"submitted": True, "serviceId": service_id, "active": active}

    def _runtime_set_managed_active(self, params: dict[str, Any]) -> dict[str, Any]:
        active = bool(params.get("active"))
        self._bridge.set_managed_active(active)
        return {"submitted": True, "active": active}

    def _runtime_service_process(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = _required_text(params, "serviceId")
        action = str(params.get("action") or "").strip().lower()
        service_class = str(params.get("serviceClass") or "").strip() or None
        if action == "start":
            self._bridge.start_service(service_id, service_class=service_class)
        elif action == "stop":
            self._bridge.stop_service(service_id)
        elif action == "restart":
            self._bridge.restart_service(service_id, service_class=service_class)
        else:
            raise ValueError("runtime.serviceProcess action must be start, stop, or restart")
        return {"submitted": True, "serviceId": service_id, "action": action}

    def _graph_ui_context(self) -> dict[str, Any]:
        snapshot = self._graph_adapter.snapshot()
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

    def _graph_build_from_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        goal = _required_text(params, "goal")
        matches = match_graph_library_candidates(
            goal=goal,
            node_catalog=self._graph_adapter.node_catalog(),
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
                "nextTools": ["graph_preview_build_plan", "graph_apply_build_plan"],
            }
        }

    def _graph_match_library(self, params: dict[str, Any]) -> dict[str, Any]:
        goal = _required_text(params, "goal")
        matches = match_graph_library_candidates(
            goal=goal,
            node_catalog=self._graph_adapter.node_catalog(),
            limit=int(params.get("limit") or 24),
        )
        return {"matches": matches.to_dict()}

    def _graph_preview_build_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        plan = decode_graph_build_plan(params.get("plan"))
        patch = graph_patch_from_build_plan(plan, expected_revision=self._graph_adapter.revision())
        preview = self._graph_adapter.preview_patch(patch)
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
        patch = graph_patch_from_build_plan(plan, expected_revision=self._graph_adapter.revision())
        preview = self._graph_adapter.apply_patch(patch)
        self._schedule_studio_runtime_sync()
        diagnostics = self._graph_adapter.diagnostics()
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
                "snapshot": self._graph_adapter.snapshot().to_dict(),
            }
        }

    def _runtime_service_status(self, service_id: str) -> dict[str, Any]:
        sid = str(service_id or "").strip() or str(self._bridge.studio_service_id)
        latest_monitor = self._bridge.get_latest_monitor_snapshot(sid)
        return {
            "serviceId": sid,
            "serviceClass": str(self._bridge.get_service_class(sid)),
            "running": bool(self._bridge.is_service_running(sid)),
            "active": self._bridge.get_cached_service_active(sid),
            "latestMonitor": latest_monitor,
        }

    def _runtime_read_state(self, params: dict[str, Any]) -> dict[str, Any] | None:
        value = self._observations.get_state(
            service_id=str(params.get("serviceId") or ""),
            node_id=str(params.get("nodeId") or ""),
            field=str(params.get("field") or ""),
        )
        return _stored_state_to_dict(value)

    def _runtime_watch_state(self, params: dict[str, Any]) -> dict[str, Any] | None:
        value = self._observations.wait_state(
            service_id=str(params.get("serviceId") or ""),
            node_id=str(params.get("nodeId") or ""),
            field=str(params.get("field") or ""),
            after_ts_ms=_optional_int_param(params, "afterTsMs"),
            timeout_s=float(params.get("timeoutS") or (float(params.get("durationMs") or 1000) / 1000.0)),
        )
        return _stored_state_to_dict(value)

    def _runtime_write_state(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        node_id = str(params.get("nodeId") or "").strip()
        field = str(params.get("field") or "").strip()
        if "value" not in params:
            raise ValueError("runtime.writeState requires value")
        if not service_id or not node_id or not field:
            raise ValueError("runtime.writeState requires serviceId, nodeId, and field")
        return {
            "state": self._bridge.set_remote_state_and_wait(
                service_id,
                node_id,
                field,
                params.get("value"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _runtime_read_monitor(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        limit = int(params.get("limit") or 500)
        if service_id:
            return {
                "latest": self._bridge.get_latest_monitor_snapshot(service_id),
                "stream": self._bridge.get_monitor_snapshot_stream(service_id, limit=limit),
            }
        return {"report": self._bridge.export_monitor_report()}

    def _runtime_sample_port(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        node_id = str(params.get("nodeId") or "").strip()
        port = str(params.get("port") or "").strip()
        limit = int(params.get("limit") or 1)
        timeout_s = float(params.get("timeoutS") or 2.0)
        cached_only = bool(params.get("cachedOnly", False))
        min_count = int(params.get("minCount") or 1)
        if bool(params.get("subscribe", True)):
            result = self._bridge.sample_data_port_and_wait(
                service_id,
                node_id,
                port,
                limit=limit,
                timeout_s=timeout_s,
                include_value=bool(params.get("includeValue", True)),
                max_value_bytes=int(params.get("maxValueBytes") or 65536),
            )
            samples = list(result.get("samples") if isinstance(result.get("samples"), list) else [])
            for sample in samples:
                if isinstance(sample, dict):
                    self._observations.put_port_sample(
                        service_id=service_id,
                        node_id=node_id,
                        port=port,
                        sample=sample,
                    )
            if samples:
                return {
                    "samples": [dict(item) for item in samples if isinstance(item, dict)],
                    "timedOut": bool(result.get("timedOut", False)),
                    "error": str(result.get("error") or ""),
                    "cached": False,
                }
            if not cached_only:
                return {
                    "samples": [],
                    "timedOut": bool(result.get("timedOut", False)),
                    "error": str(result.get("error") or ""),
                    "cached": False,
                }
        samples = self._observations.wait_port_samples(
            service_id=service_id,
            node_id=node_id,
            port=port,
            min_count=min_count,
            limit=limit,
            after_observed_at_ms=_optional_int_param(params, "afterObservedAtMs"),
            timeout_s=timeout_s,
        )
        return {
            "samples": samples,
            "timedOut": len(samples) < max(1, min(min_count, 100)),
            "error": "",
            "cached": True,
        }

    def _runtime_debug_data(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bridge.debug_runtime_data_and_wait(
            str(params.get("serviceId") or "").strip(),
            str(params.get("nodeId") or "").strip(),
            str(params.get("port") or "").strip(),
            limit=int(params.get("limit") or 100),
            timeout_s=float(params.get("timeoutS") or 1.0),
            include_value=bool(params.get("includeValue", True)),
            max_value_bytes=int(params.get("maxValueBytes") or 65536),
        )

    def _runtime_invoke_command(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        call = str(params.get("call") or params.get("name") or "").strip()
        if not service_id or not call:
            raise ValueError("runtime.invokeCommand requires serviceId and call")
        return {
            "command": self._bridge.invoke_remote_command_and_wait(
                service_id,
                call,
                params.get("args"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _monitor_report(self) -> dict[str, Any]:
        return {"monitor": self._bridge.export_monitor_report()}

    def _monitor_service(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = _required_text(params, "serviceId")
        limit = int(params.get("limit") or 500)
        return {
            "latest": self._bridge.get_latest_monitor_snapshot(service_id),
            "stream": self._bridge.get_monitor_snapshot_stream(service_id, limit=limit),
        }

    def _logs_read(self, params: dict[str, Any]) -> dict[str, Any]:
        minimum_level = params.get("minimumLevel")
        resolved_minimum_level = int(minimum_level) if minimum_level is not None else None
        return {
            "logs": self._main_window._log_dock.export_logs(
                service_id=str(params.get("serviceId") or ""),
                limit=int(params.get("limit") or 200),
                minimum_level=resolved_minimum_level,
            )
        }

    def _notifications_read(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "notifications": export_recent_notifications(
                limit=int(params.get("limit") or 100),
                minimum_severity=str(params.get("minimumSeverity") or ""),
            )
        }

    def _graph_debug_service(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        status: dict[str, Any] | None = None
        monitor: dict[str, Any] | None = None
        logs: dict[str, Any] | None = None
        notifications = self._notifications_read(
            {
                "limit": int(params.get("notificationLimit") or 50),
                "minimumSeverity": str(params.get("notificationMinimumSeverity") or "WARNING"),
            }
        )["notifications"]
        if service_id:
            status = self._runtime_service_status(service_id)
            monitor = self._monitor_service({"serviceId": service_id, "limit": int(params.get("limit") or 100)})
            logs = self._logs_read({"serviceId": service_id, "limit": int(params.get("logLimit") or 100)})["logs"]
        diagnostics = self._graph_adapter.diagnostics()
        compile_payload = self._graph_adapter.compile_graph()
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
                "notifications": notifications,
                "diagnostics": diagnostics,
                "compile": compile_payload,
            }
        }

    def _graph_auto_layout(self, params: dict[str, Any]) -> dict[str, Any]:
        selected_only = bool(params.get("selectedOnly", False))
        apply_layout = bool(params.get("apply", False)) or bool(params.get("confirm", False))
        snapshot = self._graph_adapter.snapshot()
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
        preview = self._graph_adapter.preview_patch(patch)
        workflow = {
            "name": "graph_auto_layout",
            "status": "previewed",
            "summary": f"Prepared auto layout for {len(ops)} nodes.",
            "patch": graph_patch_to_dict(patch),
            "preview": preview.to_dict(),
        }
        if not apply_layout:
            return {"workflow": workflow}
        if not bool(params.get("confirm")):
            raise ValueError("graph_auto_layout apply requires confirm=true")
        applied = self._graph_adapter.apply_patch(patch)
        self._schedule_studio_runtime_sync()
        workflow["status"] = "applied"
        workflow["summary"] = f"Applied auto layout to {len(ops)} nodes."
        workflow["applyPreview"] = applied.to_dict()
        return {"workflow": workflow}

    def _graph_fix_container_bindings(self, params: dict[str, Any]) -> dict[str, Any]:
        apply_fix = bool(params.get("apply", False)) or bool(params.get("confirm", False))
        snapshot = self._graph_adapter.snapshot()
        diagnostics = self._graph_adapter.diagnostics()
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
        preview = self._graph_adapter.preview_patch(patch) if ops else None
        workflow: dict[str, Any] = {
            "name": "graph_fix_container_bindings",
            "status": "previewed" if ops else "no_fix_available",
            "summary": f"Prepared {len(ops)} service-container binding fixes; unresolved={len(unresolved)}.",
            "patch": graph_patch_to_dict(patch),
            "preview": None if preview is None else preview.to_dict(),
            "unresolved": unresolved,
        }
        if not apply_fix:
            return {"workflow": workflow}
        if not ops:
            return {"workflow": workflow}
        if not bool(params.get("confirm")):
            raise ValueError("graph_fix_container_bindings apply requires confirm=true")
        applied = self._graph_adapter.apply_patch(patch)
        self._schedule_studio_runtime_sync()
        workflow["status"] = "applied"
        workflow["summary"] = f"Applied {len(ops)} service-container binding fixes."
        workflow["applyPreview"] = applied.to_dict()
        workflow["diagnosticsAfter"] = self._graph_adapter.diagnostics()
        return {"workflow": workflow}

    def _schedule_studio_runtime_sync(self) -> None:
        try:
            self._main_window._schedule_studio_runtime_sync()
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("failed to schedule studio runtime sync after automation patch")

    @staticmethod
    def _write_private_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except _FILE_WRITE_ERRORS:
            logger.exception("failed to chmod private automation file path=%s", path)

    @classmethod
    def _write_private_json(cls, path: Path, payload: dict[str, Any]) -> None:
        cls._write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _requires_confirm(params: dict[str, Any]) -> bool:
    method = str(params.get("method") or "")
    if method in {"project.new", "project.load"}:
        return True
    patch = params.get("patch")
    if not isinstance(patch, dict):
        return True
    ops = patch.get("ops")
    if not isinstance(ops, list):
        return True
    for op in ops:
        if not isinstance(op, dict):
            return True
        if str(op.get("op") or "") in {"deleteNode", "setNodePorts", "setNodeStateFields"}:
            return True
    return False


def _required_text(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _required_dict_param(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return dict(value)


def _optional_dict_param(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return dict(value)


def _stored_state_to_dict(value: Any) -> dict[str, Any] | None:
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


def _string_list_param(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("tags must be a list of strings")
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _monitor_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "serviceId": str(row.service_id),
        "serviceClass": str(row.service_class),
        "running": bool(row.running),
        "alive": row.alive,
        "ready": row.ready,
        "active": row.active,
        "cpuProcessPercent": row.cpu_process_percent,
        "memoryRssBytes": row.memory_rss_bytes,
        "gpuUtilPercent": row.gpu_util_percent,
        "latencyMsP95": row.latency_ms_p95,
        "waitMsP95": row.wait_ms_p95,
        "errorCountWindow": row.error_count_window,
        "currentErrorNodeId": str(row.current_error_node_id),
        "currentErrorCode": str(row.current_error_code),
        "currentErrorMessage": str(row.current_error_message),
        "currentErrorSeverity": str(row.current_error_severity),
        "currentErrorTsMs": row.current_error_ts_ms,
        "latestSnapshot": row.latest_snapshot,
    }


def launch_pystudio_with_automation(
    *,
    port_file: str | Path | None = None,
    token_file: str | Path | None = None,
    timeout_s: float = 20.0,
) -> AutomationConnectionInfo:
    resolved_port_file = Path(port_file).expanduser() if port_file is not None else default_port_file()
    previous_mtime_ns: int | None = None
    if resolved_port_file.exists():
        previous_mtime_ns = int(resolved_port_file.stat().st_mtime_ns)
    launch_started_at = int(time.time())
    args = [
        sys.executable,
        "-m",
        "f8pystudio.main",
        "--automation",
        "--automation-port-file",
        str(resolved_port_file),
    ]
    if token_file is not None:
        args.extend(["--automation-token-file", str(Path(token_file).expanduser())])
    subprocess.Popen(args, start_new_session=True)
    return wait_for_connection_file(
        resolved_port_file,
        timeout_s=float(timeout_s),
        min_created_at=launch_started_at,
        previous_mtime_ns=previous_mtime_ns,
    )
