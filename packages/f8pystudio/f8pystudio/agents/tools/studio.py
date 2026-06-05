from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pystudio.automation.client import AutomationClient
from f8pystudio.automation.gui_host import launch_pystudio_with_automation


@dataclass(frozen=True)
class StudioAutomationTools:
    connection_file: str = ""

    def _client(self, connection_file: str = "") -> AutomationClient:
        target_file = str(connection_file or self.connection_file or "").strip() or None
        return AutomationClient.from_connection_file(target_file)

    def studio_launch(self, port_file: str = "", token_file: str = "", timeout_s: float = 20.0) -> dict[str, Any]:
        return launch_pystudio_with_automation(
            port_file=port_file or None,
            token_file=token_file or None,
            timeout_s=float(timeout_s),
        ).to_dict()

    def studio_attach(self, connection_file: str = "") -> dict[str, Any]:
        return self.studio_status(connection_file=connection_file)

    def studio_status(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("studio.status")

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

    def graph_compile(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.compile")

    def graph_session(self, connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("graph.session")

    def runtime_deploy(
        self,
        confirm: bool = False,
        wait: bool = True,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call("runtime.deploy", {"confirm": bool(confirm), "wait": bool(wait)})

    def runtime_service_status(self, service_id: str = "", connection_file: str = "") -> dict[str, Any]:
        return self._client(connection_file).call("runtime.serviceStatus", {"serviceId": service_id})

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
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
            "runtime.writeState",
            {"serviceId": service_id, "nodeId": node_id, "field": field, "value": value},
        )

    def runtime_watch_state(
        self,
        service_id: str,
        node_id: str,
        field: str,
        duration_ms: int = 1000,
        after_ts_ms: int = 0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serviceId": service_id,
            "nodeId": node_id,
            "field": field,
            "durationMs": int(duration_ms),
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
        connection_file: str = "",
    ) -> dict[str, Any]:
        return self._client(connection_file).call(
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
        connection_file: str = "",
    ) -> dict[str, Any]:
        if not bool(confirm):
            raise ValueError("runtime_invoke_command requires confirm=true")
        return self._client(connection_file).call(
            "runtime.invokeCommand",
            {"serviceId": service_id, "call": call, "args": args},
        )

    def plan_graph_change_prompt(self, goal: str) -> str:
        return (
            "Inspect f8studio://graph/diagnostics, f8studio://graph/current, and f8studio://catalog/nodes, "
            "then propose a GraphPatch. "
            f"Goal: {goal}"
        )

    def debug_runtime_node_prompt(self, service_id: str, node_id: str) -> str:
        return (
            "Use graph_node_detail, runtime_service_status, runtime_read_monitor, runtime_sample_port, "
            "and runtime_read_state to diagnose "
            f"serviceId={service_id} nodeId={node_id}."
        )

    def explain_current_graph_prompt(self) -> str:
        return "Use graph_snapshot and node_catalog to explain the current PyStudio graph succinctly."
