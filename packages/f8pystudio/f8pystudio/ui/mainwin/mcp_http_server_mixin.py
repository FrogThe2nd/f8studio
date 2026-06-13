from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qtpy import QtGui

from f8pystudio.automation.gui_host import StudioAutomationHost
from f8pystudio.mcp.http_server import (
    DEFAULT_MCP_HTTP_HOST,
    DEFAULT_MCP_HTTP_PATH,
    DEFAULT_MCP_HTTP_PORT,
    PyStudioMcpHttpServer,
)
from f8pystudio.ui.support.ui_notifications import show_info, show_warning

if TYPE_CHECKING:
    from f8pystudio.automation.observation_store import RuntimeObservationStore
    from f8pystudio.bridge.studio_bridge import PyStudioServiceBridge
    from f8pystudio.nodegraph.node_graph import F8StudioGraph
    from f8pystudio.ui.widgets.service_log_widget import ServiceLogDock

logger = logging.getLogger(__name__)
_MCP_HTTP_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class MainWindowMcpHttpServerMixin:
    if TYPE_CHECKING:
        studio_graph: F8StudioGraph
        _bridge: PyStudioServiceBridge
        _log_dock: ServiceLogDock
        _automation_host: StudioAutomationHost | None
        _runtime_observations: RuntimeObservationStore
        _mcp_http_server: PyStudioMcpHttpServer | None
        _mcp_http_server_action: QtGui.QAction

    def _ensure_automation_host_for_mcp(self) -> StudioAutomationHost:
        host = self._automation_host
        if host is not None:
            host.start()
            return host
        host = StudioAutomationHost(
            main_window=self,
            studio_graph=self.studio_graph,
            bridge=self._bridge,
            observation_store=self._runtime_observations,
            parent=self,
        )
        info = host.start()
        self._automation_host = host
        self._log_dock.append("studio", f"[automation] listening on {info.host}:{info.port}\n")
        return host

    def _set_mcp_http_action_checked(self, checked: bool) -> None:
        action = self._mcp_http_server_action
        previous = action.blockSignals(True)
        try:
            action.setChecked(bool(checked))
        finally:
            action.blockSignals(previous)

    def _start_mcp_http_server(self) -> None:
        existing_server = self._mcp_http_server
        if existing_server is not None and existing_server.is_running:
            endpoint = existing_server.endpoint
            self._log_dock.append("studio", f"[mcp] HTTP server already listening on {endpoint.url}\n")
            show_info(self, "MCP Server", f"PyStudio MCP server is listening on:\n{endpoint.url}")
            return
        if existing_server is not None:
            existing_server.stop()
            self._mcp_http_server = None

        host = self._ensure_automation_host_for_mcp()
        if host.connection_info is None:
            raise RuntimeError("automation connection info is unavailable")
        server = PyStudioMcpHttpServer(
            connection_file=str(host.port_file),
            host=DEFAULT_MCP_HTTP_HOST,
            port=DEFAULT_MCP_HTTP_PORT,
            path=DEFAULT_MCP_HTTP_PATH,
        )
        endpoint = server.start()
        self._mcp_http_server = server
        self._log_dock.append("studio", f"[mcp] HTTP server listening on {endpoint.url}\n")
        show_info(self, "MCP Server", f"PyStudio MCP server is listening on:\n{endpoint.url}")

    def _stop_mcp_http_server(self) -> None:
        server = self._mcp_http_server
        self._mcp_http_server = None
        if server is not None:
            server.stop()
        self._log_dock.append("studio", "[mcp] HTTP server stopped\n")

    def _on_mcp_http_server_toggled(self, checked: bool) -> None:
        try:
            if bool(checked):
                self._start_mcp_http_server()
                self._set_mcp_http_action_checked(True)
                return
            self._stop_mcp_http_server()
            self._set_mcp_http_action_checked(False)
        except _MCP_HTTP_ERRORS as exc:
            logger.exception("failed to toggle PyStudio MCP HTTP server")
            self._log_dock.report_exception("studio", "toggle MCP HTTP server failed", exc)
            self._set_mcp_http_action_checked(False)
            show_warning(self, "MCP Server", str(exc))
