from __future__ import annotations

from typing import Any

from f8pystudio.automation.client import AutomationClient
from f8pystudio.automation.gui_host import launch_pystudio_with_automation


def _client(connection_file: str = "") -> AutomationClient:
    return AutomationClient.from_connection_file(str(connection_file or "").strip() or None)


def _create_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP SDK is not installed. Install the optional f8pystudio[mcp] dependencies.") from exc

    server = FastMCP("f8pystudio")

    @server.tool()
    def studio_launch(port_file: str = "", token_file: str = "", timeout_s: float = 20.0) -> dict[str, Any]:
        return launch_pystudio_with_automation(
            port_file=port_file or None,
            token_file=token_file or None,
            timeout_s=float(timeout_s),
        ).to_dict()

    @server.tool()
    def studio_attach(connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("studio.status")

    @server.tool()
    def studio_status(connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("studio.status")

    @server.tool()
    def graph_snapshot(connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("graph.snapshot")

    @server.tool()
    def node_catalog(connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("graph.catalog")

    @server.tool()
    def graph_preview_patch(patch: dict[str, Any], connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("graph.previewPatch", {"patch": patch})

    @server.tool()
    def graph_apply_patch(patch: dict[str, Any], confirm: bool = False, connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("graph.applyPatch", {"patch": patch, "confirm": bool(confirm)})

    @server.tool()
    def graph_compile(connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("graph.compile")

    @server.tool()
    def runtime_deploy(confirm: bool = False, wait: bool = True, connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("runtime.deploy", {"confirm": bool(confirm), "wait": bool(wait)})

    @server.tool()
    def runtime_service_status(service_id: str = "", connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("runtime.serviceStatus", {"serviceId": service_id})

    @server.tool()
    def runtime_read_state(service_id: str, node_id: str, field: str, connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call(
            "runtime.readState",
            {"serviceId": service_id, "nodeId": node_id, "field": field},
        )

    @server.tool()
    def runtime_write_state(
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return _client(connection_file).call(
            "runtime.writeState",
            {"serviceId": service_id, "nodeId": node_id, "field": field, "value": value},
        )

    @server.tool()
    def runtime_watch_state(
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
        return _client(connection_file).call(
            "runtime.watchState",
            payload,
        )

    @server.tool()
    def runtime_read_monitor(service_id: str = "", limit: int = 500, connection_file: str = "") -> dict[str, Any]:
        return _client(connection_file).call("runtime.readMonitor", {"serviceId": service_id, "limit": int(limit)})

    @server.tool()
    def runtime_sample_port(
        service_id: str,
        node_id: str,
        port: str,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return _client(connection_file).call(
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

    @server.tool()
    def runtime_invoke_command(
        service_id: str,
        call: str,
        args: Any = None,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        if not bool(confirm):
            raise ValueError("runtime_invoke_command requires confirm=true")
        return _client(connection_file).call(
            "runtime.invokeCommand",
            {"serviceId": service_id, "call": call, "args": args},
        )

    @server.resource("f8studio://graph/current")
    def current_graph() -> dict[str, Any]:
        return _client("").call("graph.snapshot")

    @server.resource("f8studio://catalog/nodes")
    def catalog_nodes() -> dict[str, Any]:
        return _client("").call("graph.catalog")

    @server.resource("f8studio://runtime/monitor")
    def runtime_monitor() -> dict[str, Any]:
        return _client("").call("runtime.readMonitor")

    @server.resource("f8studio://session/current")
    def current_session() -> dict[str, Any]:
        return _client("").call("graph.session")

    @server.prompt()
    def plan_graph_change(goal: str) -> str:
        return (
            "Inspect f8studio://graph/current and f8studio://catalog/nodes, then propose a GraphPatch. "
            f"Goal: {goal}"
        )

    @server.prompt()
    def debug_runtime_node(service_id: str, node_id: str) -> str:
        return (
            "Use runtime_service_status, runtime_read_monitor, and runtime_read_state to diagnose "
            f"serviceId={service_id} nodeId={node_id}."
        )

    @server.prompt()
    def explain_current_graph() -> str:
        return "Use graph_snapshot and node_catalog to explain the current PyStudio graph succinctly."

    return server


def main() -> None:
    _create_server().run()


if __name__ == "__main__":
    main()
