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
    def graph_session(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_session(connection_file=connection_file)

    @server.tool()
    def project_list(connection_file: str = "") -> dict[str, Any]:
        return tools.project_list(connection_file=connection_file)

    @server.tool()
    def project_new(
        confirm: bool = False,
        clear_current_project: bool = True,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.project_new(
            confirm=confirm,
            clear_current_project=clear_current_project,
            connection_file=connection_file,
        )

    @server.tool()
    def project_save(
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        project_id: str = "",
        overwrite_project_id: str = "",
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.project_save(
            name=name,
            description=description,
            tags=tags,
            project_id=project_id,
            overwrite_project_id=overwrite_project_id,
            connection_file=connection_file,
        )

    @server.tool()
    def project_load(
        project_id: str,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.project_load(project_id=project_id, confirm=confirm, connection_file=connection_file)

    @server.tool()
    def graph_compile(connection_file: str = "") -> dict[str, Any]:
        return tools.graph_compile(connection_file=connection_file)

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
    def graph_debug_service(
        service_id: str = "",
        limit: int = 100,
        log_limit: int = 100,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.graph_debug_service(
            service_id=service_id,
            limit=limit,
            log_limit=log_limit,
            connection_file=connection_file,
        )

    @server.tool()
    def graph_auto_layout(
        selected_only: bool = False,
        apply: bool = False,
        confirm: bool = False,
        spacing_x: float = 260.0,
        spacing_y: float = 150.0,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.graph_auto_layout(
            selected_only=selected_only,
            apply=apply,
            confirm=confirm,
            spacing_x=spacing_x,
            spacing_y=spacing_y,
            origin_x=origin_x,
            origin_y=origin_y,
            connection_file=connection_file,
        )

    @server.tool()
    def graph_fix_container_bindings(
        service_id: str = "",
        apply: bool = False,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.graph_fix_container_bindings(
            service_id=service_id,
            apply=apply,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_deploy(
        confirm: bool = False,
        wait: bool = True,
        timeout_s: float = 20.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_deploy(confirm=confirm, wait=wait, timeout_s=timeout_s, connection_file=connection_file)

    @server.tool()
    def runtime_service_deploy(
        service_id: str,
        timeout_s: float = 10.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_service_deploy(
            service_id=service_id,
            timeout_s=timeout_s,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_services(connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_services(connection_file=connection_file)

    @server.tool()
    def runtime_service_status(service_id: str = "", connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_service_status(service_id=service_id, connection_file=connection_file)

    @server.tool()
    def runtime_set_service_active(
        service_id: str,
        active: bool,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_set_service_active(
            service_id=service_id,
            active=active,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_set_managed_active(
        active: bool,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_set_managed_active(
            active=active,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_service_process(
        service_id: str,
        action: str,
        service_class: str = "",
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_service_process(
            service_id=service_id,
            action=action,
            service_class=service_class,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_write_state(
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        timeout_s: float = 2.0,
        confirm: bool = False,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_write_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            value=value,
            timeout_s=timeout_s,
            confirm=confirm,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_read_state(service_id: str, node_id: str, field: str, connection_file: str = "") -> dict[str, Any]:
        return tools.runtime_read_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_watch_state(
        service_id: str,
        node_id: str,
        field: str,
        after_ts_ms: int = 0,
        timeout_s: float = 1.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_watch_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            after_ts_ms=after_ts_ms,
            timeout_s=timeout_s,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_sample_port(
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
    def runtime_debug_data(
        service_id: str,
        node_id: str = "",
        port: str = "",
        limit: int = 100,
        timeout_s: float = 1.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_debug_data(
            service_id=service_id,
            node_id=node_id,
            port=port,
            limit=limit,
            timeout_s=timeout_s,
            include_value=include_value,
            max_value_bytes=max_value_bytes,
            cached_only=cached_only,
            min_count=min_count,
            after_observed_at_ms=after_observed_at_ms,
            connection_file=connection_file,
        )

    @server.tool()
    def runtime_invoke_command(
        service_id: str,
        call: str,
        args: Any = None,
        confirm: bool = False,
        timeout_s: float = 2.0,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.runtime_invoke_command(
            service_id=service_id,
            call=call,
            args=args,
            confirm=confirm,
            timeout_s=timeout_s,
            connection_file=connection_file,
        )

    @server.tool()
    def monitor_report(connection_file: str = "") -> dict[str, Any]:
        return tools.monitor_report(connection_file=connection_file)

    @server.tool()
    def monitor_service(service_id: str, limit: int = 500, connection_file: str = "") -> dict[str, Any]:
        return tools.monitor_service(service_id=service_id, limit=limit, connection_file=connection_file)

    @server.tool()
    def logs_read(
        service_id: str = "",
        limit: int = 200,
        minimum_level: int | None = None,
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.logs_read(
            service_id=service_id,
            limit=limit,
            minimum_level=minimum_level,
            connection_file=connection_file,
        )

    @server.tool()
    def notifications_read(
        limit: int = 100,
        minimum_severity: str = "",
        connection_file: str = "",
    ) -> dict[str, Any]:
        return tools.notifications_read(
            limit=limit,
            minimum_severity=minimum_severity,
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
        return tools.monitor_report()

    @server.resource("f8studio://session/current")
    def current_session() -> dict[str, Any]:
        return tools.graph_session()

    @server.resource("f8studio://projects/local")
    def local_projects() -> dict[str, Any]:
        return tools.project_list()

    @server.resource("f8studio://ui/notifications")
    def ui_notifications() -> dict[str, Any]:
        return tools.notifications_read()

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
