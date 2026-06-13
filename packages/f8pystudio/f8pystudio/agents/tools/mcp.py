from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StudioMCPHttpConfig:
    name: str = "f8pystudio"
    url: str = "http://127.0.0.1:8765/mcp"
    tool_name_prefix: str = "studio"
    allowed_tools: tuple[str, ...] = ()
    request_timeout_s: int | None = 30


def build_studio_mcp_http_tool(config: StudioMCPHttpConfig | None = None) -> Any:
    cfg = config or StudioMCPHttpConfig()
    try:
        from agent_framework import MCPStreamableHTTPTool
    except ModuleNotFoundError as exc:
        raise RuntimeError("agent-framework-core is required to build the Studio MCP HTTP tool.") from exc

    allowed_tools = list(cfg.allowed_tools) if cfg.allowed_tools else None
    return MCPStreamableHTTPTool(
        name=cfg.name,
        url=cfg.url,
        tool_name_prefix=cfg.tool_name_prefix,
        allowed_tools=allowed_tools,
        request_timeout=cfg.request_timeout_s,
        description="F8 PyStudio graph, runtime, and debug tools.",
    )
