from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_MCP_HTTP_PORT = 8765
DEFAULT_MCP_HTTP_PATH = "/mcp"
MCP_CONNECTION_FILE_ENV = "F8PYSTUDIO_CONNECTION_FILE"
MCP_HTTP_HOST_ENV = "F8PYSTUDIO_MCP_HOST"
MCP_HTTP_PORT_ENV = "F8PYSTUDIO_MCP_PORT"
MCP_HTTP_PATH_ENV = "F8PYSTUDIO_MCP_PATH"
MCP_LOG_LEVEL_ENV = "F8PYSTUDIO_MCP_LOG_LEVEL"
_SERVER_START_TIMEOUT_S = 2.0
_SERVER_START_POLL_S = 0.02
_SERVER_STOP_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class PyStudioMcpHttpEndpoint:
    host: str = DEFAULT_MCP_HTTP_HOST
    port: int = DEFAULT_MCP_HTTP_PORT
    path: str = DEFAULT_MCP_HTTP_PATH

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


def create_http_mcp_server(
    *,
    connection_file: str = "",
    host: str = DEFAULT_MCP_HTTP_HOST,
    port: int = DEFAULT_MCP_HTTP_PORT,
    path: str = DEFAULT_MCP_HTTP_PATH,
    log_level: str = "INFO",
) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP SDK is not installed. Install the optional f8pystudio[mcp] dependencies.") from exc

    from f8pystudio.agents.tools.studio import StudioAutomationTools
    from f8pystudio.mcp.registration import register_studio_mcp_tools

    tools = StudioAutomationTools(connection_file=str(connection_file or "").strip())
    server = FastMCP(
        "f8pystudio",
        host=str(host or DEFAULT_MCP_HTTP_HOST).strip() or DEFAULT_MCP_HTTP_HOST,
        port=int(port),
        streamable_http_path=normalize_http_path(path),
        log_level=normalize_log_level(log_level),
    )
    register_studio_mcp_tools(server, tools)
    return server


class PyStudioMcpHttpServer:
    def __init__(
        self,
        *,
        connection_file: str = "",
        host: str = DEFAULT_MCP_HTTP_HOST,
        port: int = DEFAULT_MCP_HTTP_PORT,
        path: str = DEFAULT_MCP_HTTP_PATH,
        log_level: str = "INFO",
    ) -> None:
        self._connection_file = str(connection_file or "").strip()
        self._endpoint = PyStudioMcpHttpEndpoint(
            host=str(host or DEFAULT_MCP_HTTP_HOST).strip() or DEFAULT_MCP_HTTP_HOST,
            port=int(port),
            path=normalize_http_path(path),
        )
        self._log_level = normalize_log_level(log_level)
        self._thread: threading.Thread | None = None
        self._uvicorn_server: Any | None = None
        self._lock = threading.Lock()

    @property
    def endpoint(self) -> PyStudioMcpHttpEndpoint:
        return self._endpoint

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def start(self) -> PyStudioMcpHttpEndpoint:
        with self._lock:
            if self.is_running:
                return self._endpoint
            fastmcp_server = create_http_mcp_server(
                connection_file=self._connection_file,
                host=self._endpoint.host,
                port=self._endpoint.port,
                path=self._endpoint.path,
                log_level=self._log_level,
            )
            try:
                import uvicorn
            except ImportError as exc:
                raise RuntimeError("uvicorn is required to run the PyStudio MCP HTTP server.") from exc
            config = uvicorn.Config(
                fastmcp_server.streamable_http_app(),
                host=self._endpoint.host,
                port=self._endpoint.port,
                log_level=self._log_level.lower(),
            )
            uvicorn_server = uvicorn.Server(config)
            thread = threading.Thread(
                target=uvicorn_server.run,
                name="f8pystudio-mcp-http-server",
                daemon=True,
            )
            self._uvicorn_server = uvicorn_server
            self._thread = thread
            thread.start()
            if not _wait_for_uvicorn_start(uvicorn_server, thread):
                uvicorn_server.should_exit = True
                self._uvicorn_server = None
                self._thread = None
                thread.join(timeout=_SERVER_STOP_TIMEOUT_S)
                if thread.is_alive():
                    raise RuntimeError(f"PyStudio MCP HTTP server did not start within {_SERVER_START_TIMEOUT_S:.1f}s.")
                raise RuntimeError(f"PyStudio MCP HTTP server stopped before listening on {self._endpoint.url}.")
            logger.info("PyStudio MCP HTTP listening on %s", self._endpoint.url)
            return self._endpoint

    def stop(self) -> None:
        with self._lock:
            server = self._uvicorn_server
            thread = self._thread
            self._uvicorn_server = None
            self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=_SERVER_STOP_TIMEOUT_S)


def normalize_http_path(value: str) -> str:
    path = str(value or DEFAULT_MCP_HTTP_PATH).strip() or DEFAULT_MCP_HTTP_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _wait_for_uvicorn_start(server: Any, thread: threading.Thread) -> bool:
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.started:
            return True
        if not thread.is_alive():
            return False
        time.sleep(_SERVER_START_POLL_S)
    return False


def normalize_log_level(value: str) -> str:
    text = str(value or "INFO").strip().upper() or "INFO"
    if text not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"unsupported MCP log level: {value!r}")
    return text
