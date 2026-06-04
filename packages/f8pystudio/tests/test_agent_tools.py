from __future__ import annotations

import sys
import types

import pytest

from f8pystudio.agents.tools.mcp import StudioMCPStdioConfig, build_studio_mcp_stdio_tool
from f8pystudio.agents.tools.studio import StudioAutomationTools


def test_studio_automation_tools_forward_graph_snapshot_to_automation_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeClient:
        def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append((method, params))
            return {"ok": True}

    monkeypatch.setattr(
        "f8pystudio.agents.tools.studio.AutomationClient.from_connection_file",
        lambda path: FakeClient(),
    )

    tools = StudioAutomationTools(connection_file="/tmp/f8-connection.json")
    result = tools.graph_snapshot()

    assert result == {"ok": True}
    assert calls == [("graph.snapshot", None)]


def test_runtime_invoke_command_requires_confirm() -> None:
    tools = StudioAutomationTools()

    with pytest.raises(ValueError, match="confirm=true"):
        tools.runtime_invoke_command(service_id="svc", call="doThing", confirm=False)


def test_build_studio_mcp_stdio_tool_uses_agent_framework_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeMCPStdioTool:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(MCPStdioTool=FakeMCPStdioTool)
    monkeypatch.setitem(sys.modules, "agent_framework", fake_module)

    config = StudioMCPStdioConfig(
        name="studio",
        command="pixi",
        args=("run", "f8pystudio_mcp"),
        tool_name_prefix="f8",
        allowed_tools=("graph_snapshot",),
        request_timeout_s=15,
        env={"A": "B"},
    )

    tool = build_studio_mcp_stdio_tool(config)

    assert isinstance(tool, FakeMCPStdioTool)
    assert created == [
        {
            "name": "studio",
            "command": "pixi",
            "args": ["run", "f8pystudio_mcp"],
            "env": {"A": "B"},
            "tool_name_prefix": "f8",
            "allowed_tools": ["graph_snapshot"],
            "request_timeout": 15,
            "description": "F8 PyStudio graph, runtime, and debug tools.",
        }
    ]
