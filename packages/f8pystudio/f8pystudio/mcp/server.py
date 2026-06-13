from __future__ import annotations

import argparse
import os

from f8pystudio.mcp.http_server import (
    DEFAULT_MCP_HTTP_HOST,
    DEFAULT_MCP_HTTP_PATH,
    DEFAULT_MCP_HTTP_PORT,
    MCP_CONNECTION_FILE_ENV,
    MCP_HTTP_HOST_ENV,
    MCP_HTTP_PATH_ENV,
    MCP_HTTP_PORT_ENV,
    MCP_LOG_LEVEL_ENV,
    create_http_mcp_server,
    normalize_log_level,
)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    server = create_http_mcp_server(
        connection_file=str(args.connection_file or ""),
        host=str(args.host or DEFAULT_MCP_HTTP_HOST),
        port=int(args.port),
        path=str(args.path or DEFAULT_MCP_HTTP_PATH),
        log_level=str(args.log_level or "INFO"),
    )
    server.run("streamable-http")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F8 PyStudio streamable HTTP MCP server")
    parser.add_argument(
        "--host",
        default=_env_text(MCP_HTTP_HOST_ENV, DEFAULT_MCP_HTTP_HOST),
        help="Host for the streamable HTTP MCP server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int(MCP_HTTP_PORT_ENV, DEFAULT_MCP_HTTP_PORT),
        help="Port for the streamable HTTP MCP server.",
    )
    parser.add_argument(
        "--path",
        default=_env_text(MCP_HTTP_PATH_ENV, DEFAULT_MCP_HTTP_PATH),
        help="Path for the streamable HTTP MCP endpoint.",
    )
    parser.add_argument(
        "--connection-file",
        default=_env_text(MCP_CONNECTION_FILE_ENV, ""),
        help="Default PyStudio automation connection file for tool calls.",
    )
    parser.add_argument(
        "--log-level",
        default=_env_text(MCP_LOG_LEVEL_ENV, "INFO"),
        type=normalize_log_level,
        help="MCP server log level.",
    )
    return parser.parse_args(argv)


def _env_text(name: str, default: str) -> str:
    return str(os.environ.get(name) or default)


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {raw!r}") from exc


if __name__ == "__main__":
    main()
