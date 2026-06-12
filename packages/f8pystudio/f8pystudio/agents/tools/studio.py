from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pystudio.automation.client import AutomationClient


@dataclass(frozen=True)
class StudioAutomationTools:
    connection_file: str = ""

    def _client(self, connection_file: str = "") -> AutomationClient:
        target_file = str(connection_file or self.connection_file or "").strip() or None
        return AutomationClient.from_connection_file(target_file)

    def studio_launch(self, port_file: str = "", token_file: str = "", timeout_s: float = 20.0) -> dict[str, Any]:
        from f8pystudio.automation.gui_host import launch_pystudio_with_automation

        return launch_pystudio_with_automation(
            port_file=port_file or None,
            token_file=token_file or None,
            timeout_s=float(timeout_s),
        ).to_dict()

    def studio_attach(self, connection_file: str = "") -> dict[str, Any]:
        return self.studio_status(connection_file=connection_file)

    def studio_status(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("studio.status")

    def graph_ui_context(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.uiContext")

    def graph_snapshot(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.snapshot")

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
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
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

    def graph_node_detail(self, node_id: str, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.nodeDetail", {"nodeId": node_id})

    def graph_connections(
        self,
        node_id: str = "",
        direction: str = "both",
        limit: int = 200,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "graph.connections",
            {"nodeId": node_id, "direction": direction, "limit": int(limit)},
        )

    def graph_diagnostics(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.diagnostics")

    def node_catalog(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.catalog")

    def service_library(self, query: str = "", limit: int = 200, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("library.services", {"query": query, "limit": int(limit)})

    def operator_library(
        self,
        service_class: str = "",
        query: str = "",
        limit: int = 300,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "library.operators",
            {"serviceClass": service_class, "query": query, "limit": int(limit)},
        )

    def operator_detail(self, service_class: str, operator_class: str, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call(
            "library.operatorDetail",
            {"serviceClass": service_class, "operatorClass": operator_class},
        )

    def graph_preview_patch(self, patch: dict[str, Any], connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.previewPatch", {"patch": patch})

    def graph_apply_patch(
        self,
        patch: dict[str, Any],
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call("graph.applyPatch", {"patch": patch, "confirm": bool(confirm)})

    def graph_build_from_goal(self, goal: str, limit: int = 24, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.buildFromGoal", {"goal": goal, "limit": int(limit)})

    def graph_match_library(self, goal: str, limit: int = 24, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.matchLibrary", {"goal": goal, "limit": int(limit)})

    def graph_preview_build_plan(self, plan: dict[str, Any], connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.previewBuildPlan", {"plan": plan})

    def graph_apply_build_plan(
        self,
        plan: dict[str, Any],
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "graph.applyBuildPlan",
            {"plan": plan, "confirm": bool(confirm)},
        )

    def graph_debug_service(
        self,
        service_id: str = "",
        limit: int = 100,
        log_limit: int = 100,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
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
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
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
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "graph.fixContainerBindings",
            {"serviceId": service_id, "apply": bool(apply), "confirm": bool(confirm)},
        )

    def graph_compile(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.compile")

    def graph_session(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.session")

    def project_list(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("project.list")

    def project_new(
        self,
        confirm: bool = False,
        clear_current_project: bool = True,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "project.new",
            {"confirm": bool(confirm), "clearCurrentProject": bool(clear_current_project)},
        )

    def project_save(
        self,
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        project_id: str = "",
        overwrite_project_id: str = "",
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "project.save",
            {
                "name": name,
                "description": description,
                "tags": None if tags is None else list(tags),
                "projectId": project_id,
                "overwriteProjectId": overwrite_project_id,
            },
        )

    def project_load(
        self,
        project_id: str,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "project.load",
            {"projectId": project_id, "confirm": bool(confirm)},
        )

    def runtime_deploy(
        self,
        confirm: bool = False,
        wait: bool = True,
        timeout_s: float = 20.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.deploy",
            {"confirm": bool(confirm), "wait": bool(wait), "timeoutS": float(timeout_s)},
        )

    def runtime_service_deploy(
        self,
        service_id: str,
        timeout_s: float = 10.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.serviceDeploy",
            {"serviceId": service_id, "timeoutS": float(timeout_s)},
        )

    def runtime_services(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("runtime.services")

    def runtime_service_status(self, service_id: str = "", connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("runtime.serviceStatus", {"serviceId": service_id})

    def runtime_set_service_active(
        self,
        service_id: str,
        active: bool,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.setServiceActive",
            {"serviceId": service_id, "active": bool(active), "confirm": bool(confirm)},
        )

    def runtime_set_managed_active(
        self,
        active: bool,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.setManagedActive",
            {"active": bool(active), "confirm": bool(confirm)},
        )

    def runtime_service_process(
        self,
        service_id: str,
        action: str,
        service_class: str = "",
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.serviceProcess",
            {
                "serviceId": service_id,
                "action": action,
                "serviceClass": service_class,
                "confirm": bool(confirm),
            },
        )

    def runtime_read_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.readState",
            {"serviceId": service_id, "nodeId": node_id, "field": field},
        )

    def runtime_write_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        timeout_s: float = 2.0,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.writeState",
            {
                "serviceId": service_id,
                "nodeId": node_id,
                "field": field,
                "value": value,
                "timeoutS": float(timeout_s),
                "confirm": bool(confirm),
            },
        )

    def runtime_watch_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        after_ts_ms: int = 0,
        timeout_s: float = 1.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serviceId": service_id,
            "nodeId": node_id,
            "field": field,
            "timeoutS": float(timeout_s),
        }
        if int(after_ts_ms) > 0:
            payload["afterTsMs"] = int(after_ts_ms)
        return self._client(connection_file).call("runtime.watchState", payload)

    def runtime_read_monitor(
        self,
        service_id: str = "",
        limit: int = 500,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call("runtime.readMonitor", {"serviceId": service_id, "limit": int(limit)})

    def runtime_sample_port(
        self,
        service_id: str,
        node_id: str,
        port: str,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
        cached_only: bool = False,
        min_count: int = 1,
        after_observed_at_ms: int = 0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serviceId": service_id,
            "nodeId": node_id,
            "port": port,
            "limit": int(limit),
            "timeoutS": float(timeout_s),
            "includeValue": bool(include_value),
            "maxValueBytes": int(max_value_bytes),
            "cachedOnly": bool(cached_only),
            "minCount": int(min_count),
            "subscribe": not bool(cached_only),
        }
        if int(after_observed_at_ms) > 0:
            payload["afterObservedAtMs"] = int(after_observed_at_ms)
        return self._client(connection_file).call("runtime.samplePort", payload)

    def runtime_debug_data(
        self,
        service_id: str,
        node_id: str = "",
        port: str = "",
        limit: int = 100,
        timeout_s: float = 1.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.debugData",
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
        connection_file: str = "",
    ) -> dict[str, Any]:
        if not bool(confirm):
            raise ValueError("runtime_invoke_command requires confirm=true")
        return self._client(connection_file).call(
            "runtime.invokeCommand",
            {
                "serviceId": service_id,
                "call": call,
                "args": args,
                "confirm": bool(confirm),
                "timeoutS": float(timeout_s),
            },
        )

    def monitor_report(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("monitor.report")

    def monitor_service(self, service_id: str, limit: int = 500, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("monitor.service", {"serviceId": service_id, "limit": int(limit)})

    def logs_read(
        self,
        service_id: str = "",
        limit: int = 200,
        minimum_level: int | None = None,
        connection_file: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"serviceId": service_id, "limit": int(limit)}
        if minimum_level is not None:
            payload["minimumLevel"] = int(minimum_level)
        return self._client(connection_file).call("logs.read", payload)

    def notifications_read(
        self,
        limit: int = 100,
        minimum_severity: str = "",
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "notifications.read",
            {"limit": int(limit), "minimumSeverity": minimum_severity},
        )

    def plan_graph_change_prompt(self, goal: str) -> str:
        return (
            "Inspect f8studio://graph/diagnostics, f8studio://graph/current, and f8studio://catalog/nodes. "
            "Use graph_build_from_goal or graph_match_library to choose candidate nodes, then produce a typed "
            "GraphBuildPlan and preview it with graph_preview_build_plan before applying. "
            f"Goal: {goal}"
        )

    def debug_runtime_node_prompt(self, service_id: str, node_id: str) -> str:
        return (
            "Use graph_node_detail, runtime_service_status, runtime_read_monitor, runtime_debug_data, "
            "runtime_sample_port, and runtime_read_state to diagnose "
            f"serviceId={service_id} nodeId={node_id}."
        )

    def explain_current_graph_prompt(self) -> str:
        return "Use graph_snapshot and node_catalog to explain the current PyStudio graph succinctly."
