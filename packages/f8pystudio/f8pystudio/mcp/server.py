from __future__ import annotations

from typing import Any

from f8pystudio.agents.tools.studio import StudioAutomationTools


def _create_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP SDK is not installed. Install the optional f8pystudio[mcp] dependencies.") from exc

    tools = StudioAutomationTools()
    server = FastMCP("f8pystudio")

    @server.tool()
    def studio_launch(port_file: str = "", token_file: str = "", timeout_s: float = 20.0) -> dict[str, Any]:
        return tools.studio_launch(port_file=port_file, token_file=token_file, timeout_s=timeout_s)

    @server.tool()
    def studio_attach(connection_file: str = "") -> dict[str, Any]:
        return tools.studio_attach(connection_file=connection_file)

    @server.tool()
    def studio_status(connection_file: str = "") -> dict[str, Any]:
        return tools.studio_status(connection_file=connection_file)

    @server.tool()
    def graph_ui_context(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_ui_context(connection_file=connection_file)

    @server.tool()
    def graph_snapshot(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_snapshot(connection_file=connection_file)

    @server.tool()
    def graph_find_nodes(
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
        return tools.graph_find_nodes(
            query=query,
            node_id=node_id,
            node_type=node_type,
            kind=kind,
            service_class=service_class,
            operator_class=operator_class,
            selected_only=selected_only,
            limit=limit,
            connection_file=connection_file,
        )

    @server.tool()
    def graph_node_detail(node_id: str, connection_file: str = "") -> dict[str, Any]:
        return tools.graph_node_detail(node_id=node_id, connection_file=connection_file)

    @server.tool()
    def graph_connections(
        node_id: str = "",
        direction: str = "both",
        limit: int = 200,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.graph_connections(
            node_id=node_id,
            direction=direction,
            limit=limit,
            connection_file=connection_file,
        )

    @server.tool()
    def graph_diagnostics(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_diagnostics(connection_file=connection_file)

    @server.tool()
    def node_catalog(connection_file: str = "") -> dict[str, Any]:
        return tools.node_catalog(connection_file=connection_file)

    @server.tool()
    def service_library(query: str = "", limit: int = 200, connection_file: str = "") -> dict[str, Any]:
        return tools.service_library(query=query, limit=limit, connection_file=connection_file)

    @server.tool()
    def operator_library(
        service_class: str = "",
        query: str = "",
        limit: int = 300,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.operator_library(
            service_class=service_class,
            query=query,
            limit=limit,
            connection_file=connection_file,
        )

    @server.tool()
    def operator_detail(service_class: str, operator_class: str, connection_file: str = "") -> dict[str, Any]:
        return tools.operator_detail(
            service_class=service_class,
            operator_class=operator_class,
            connection_file=connection_file,
        )

    @server.tool()
    def graph_preview_patch(patch: dict[str, Any], connection_file: str = "") -> dict[str, Any]:
        return tools.graph_preview_patch(patch=patch, connection_file=connection_file)

    @server.tool()
    def graph_apply_patch(patch: dict[str, Any], confirm: bool = False, connection_file: str = "") -> dict[str, Any]:
        return tools.graph_apply_patch(patch=patch, confirm=confirm, connection_file=connection_file)

    @server.tool()
    def graph_build_from_goal(goal: str, limit: int = 24, connection_file: str = "") -> dict[str, Any]:
        return tools.graph_build_from_goal(goal=goal, limit=limit, connection_file=connection_file)

    @server.tool()
    def graph_match_library(goal: str, limit: int = 24, connection_file: str = "") -> dict[str, Any]:
        return tools.graph_match_library(goal=goal, limit=limit, connection_file=connection_file)

    @server.tool()
    def graph_preview_build_plan(plan: dict[str, Any], connection_file: str = "") -> dict[str, Any]:
        return tools.graph_preview_build_plan(plan=plan, connection_file=connection_file)

    @server.tool()
    def graph_apply_build_plan(
        plan: dict[str, Any],
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.graph_apply_build_plan(plan=plan, confirm=confirm, connection_file=connection_file)

    @server.tool()
    def graph_compile(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_compile(connection_file=connection_file)

    @server.tool()
    def runtime_deploy(confirm: bool = False, wait: bool = True, connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_deploy(confirm=confirm, wait=wait, connection_file=connection_file)

    @server.tool()
    def runtime_service_status(service_id: str = "", connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_service_status(service_id=service_id, connection_file=connection_file)

    @server.tool()
    def runtime_read_state(service_id: str, node_id: str, field: str, connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_read_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_write_state(
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_write_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            value=value,
            connection_file=connection_file,
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
        return tools.runtime_watch_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            duration_ms=duration_ms,
            after_ts_ms=after_ts_ms,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_read_monitor(service_id: str = "", limit: int = 500, connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_read_monitor(service_id=service_id, limit=limit, connection_file=connection_file)

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
        return tools.runtime_sample_port(
            service_id=service_id,
            node_id=node_id,
            port=port,
            limit=limit,
            timeout_s=timeout_s,
            include_value=include_value,
            max_value_bytes=max_value_bytes,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_invoke_command(
        service_id: str,
        call: str,
        args: Any = None,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_invoke_command(
            service_id=service_id,
            call=call,
            args=args,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.resource("f8studio://graph/current")
    def current_graph() -> dict[str, Any]:
        return tools.graph_snapshot()

    @server.resource("f8studio://catalog/nodes")
    def catalog_nodes() -> dict[str, Any]:
        return tools.node_catalog()

    @server.resource("f8studio://catalog/services")
    def catalog_services() -> dict[str, Any]:
        return tools.service_library()

    @server.resource("f8studio://catalog/operators")
    def catalog_operators() -> dict[str, Any]:
        return tools.operator_library()

    @server.resource("f8studio://graph/diagnostics")
    def graph_diagnostics_resource() -> dict[str, Any]:
        return tools.graph_diagnostics()

    @server.resource("f8studio://graph/connections")
    def graph_connections_resource() -> dict[str, Any]:
        return tools.graph_connections()

    @server.resource("f8studio://runtime/monitor")
    def runtime_monitor() -> dict[str, Any]:
        return tools.runtime_read_monitor()

    @server.resource("f8studio://session/current")
    def current_session() -> dict[str, Any]:
        return tools.graph_session()

    @server.prompt()
    def plan_graph_change(goal: str) -> str:
        return tools.plan_graph_change_prompt(goal)

    @server.prompt()
    def debug_runtime_node(service_id: str, node_id: str) -> str:
        return tools.debug_runtime_node_prompt(service_id, node_id)

    @server.prompt()
    def explain_current_graph() -> str:
        return tools.explain_current_graph_prompt()

    return server


def main() -> None:
    _create_server().run()


if __name__ == "__main__":
    main()
