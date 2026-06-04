from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StudioMCPStdioConfig:
    name: str = "f8pystudio"
    command: str = "pixi"
    args: tuple[str, ...] = ("run", "f8pystudio_mcp")
    tool_name_prefix: str = "studio"
    allowed_tools: tuple[str, ...] = ()
    request_timeout_s: int | None = 30
    env: dict[str, str] = field(default_factory=dict)


def build_studio_mcp_stdio_tool(config: StudioMCPStdioConfig | None = None) -> Any:
    cfg = config or StudioMCPStdioConfig()
    try:
        from agent_framework import MCPStdioTool
    except ModuleNotFoundError as exc:
        raise RuntimeError("agent-framework-core is required to build the Studio MCP tool.") from exc

    allowed_tools = list(cfg.allowed_tools) if cfg.allowed_tools else None
    return MCPStdioTool(
        name=cfg.name,
        command=cfg.command,
        args=list(cfg.args),
        env=dict(cfg.env) or None,
        tool_name_prefix=cfg.tool_name_prefix,
        allowed_tools=allowed_tools,
        request_timeout=cfg.request_timeout_s,
        description="F8 PyStudio graph, runtime, and debug tools.",
    )
