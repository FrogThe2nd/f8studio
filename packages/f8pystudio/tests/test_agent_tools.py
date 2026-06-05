from __future__ import annotations

import sys
import threading
import types
from dataclasses import dataclass

import pytest
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.specs import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8ServiceSchemaVersion,
    F8ServiceSpec,
    string_schema,
)
from qtpy import QtWidgets

from f8pystudio.automation.observation_store import RuntimeObservationStore
from f8pystudio.agents.tools.graph import LocalStudioGraphToolExecutor, LocalStudioGraphTools
from f8pystudio.agents.tools.mcp import StudioMCPStdioConfig, build_studio_mcp_stdio_tool
from f8pystudio.agents.tools.studio import StudioAutomationTools


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


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


def test_studio_automation_tools_forward_graph_ui_context(
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
    result = tools.graph_ui_context()

    assert result == {"ok": True}
    assert calls == [("graph.uiContext", None)]


def test_studio_automation_tools_forward_graph_build_plan_calls(
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
    plan = {"summary": "x", "requirement": {"goal": "x"}, "nodes": [{"nodeType": "n", "nodeId": "n"}]}

    assert tools.graph_build_from_goal("build wave") == {"ok": True}
    assert tools.graph_match_library("build wave", limit=3) == {"ok": True}
    assert tools.graph_preview_build_plan(plan) == {"ok": True}
    assert tools.graph_apply_build_plan(plan, confirm=True) == {"ok": True}
    assert calls == [
        ("graph.buildFromGoal", {"goal": "build wave", "limit": 24}),
        ("graph.matchLibrary", {"goal": "build wave", "limit": 3}),
        ("graph.previewBuildPlan", {"plan": plan}),
        ("graph.applyBuildPlan", {"plan": plan, "confirm": True}),
    ]


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


def test_local_studio_graph_tools_dispatch_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()
    calls: list[object] = []
    callback_count = 0

    class FakePayload:
        def __init__(
            self,
            payload: dict[str, object],
            *,
            revision: int = 0,
            selected_node_ids: tuple[str, ...] = (),
        ) -> None:
            self._payload = dict(payload)
            self.revision = int(revision)
            self.selected_node_ids = tuple(selected_node_ids)

        def to_dict(self) -> dict[str, object]:
            return dict(self._payload)

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            calls.append(f"init:{studio_graph}")

        def revision(self) -> int:
            calls.append("revision")
            return 7

        def snapshot(self) -> FakePayload:
            calls.append("snapshot")
            return FakePayload({"nodeCount": 1}, revision=7, selected_node_ids=("node-a",))

        def session_payload(self) -> dict[str, object]:
            calls.append("session")
            return {"nodes": []}

        def node_catalog(self) -> dict[str, object]:
            calls.append("catalog")
            return {"nodes": [{"nodeType": "f8.test"}]}

        def find_nodes(
            self,
            *,
            query: str = "",
            node_id: str = "",
            node_type: str = "",
            kind: str = "",
            service_class: str = "",
            operator_class: str = "",
            selected_only: bool = False,
            limit: int = 50,
        ) -> dict[str, object]:
            calls.append(
                (
                    "find",
                    query,
                    node_id,
                    node_type,
                    kind,
                    service_class,
                    operator_class,
                    selected_only,
                    limit,
                )
            )
            return {"nodes": [{"node_id": "node-a"}]}

        def node_detail(self, node_id: str) -> dict[str, object]:
            calls.append(f"detail:{node_id}")
            return {"node": {"node_id": node_id}}

        def connections(self, *, node_id: str = "", direction: str = "both", limit: int = 200) -> dict[str, object]:
            calls.append(f"connections:{node_id}:{direction}:{limit}")
            return {"connections": [{"from_node_id": node_id}]}

        def diagnostics(self) -> dict[str, object]:
            calls.append("diagnostics")
            return {"ok": True, "issues": []}

        def preview_patch(self, patch: object) -> FakePayload:
            calls.append(f"preview:{type(patch).__name__}")
            return FakePayload({"valid": True})

        def apply_patch(self, patch: object) -> FakePayload:
            calls.append(f"apply:{type(patch).__name__}")
            return FakePayload({"valid": True})

        def compile_graph(self) -> dict[str, object]:
            calls.append("compile")
            return {"warnings": []}

    def on_graph_patch_applied() -> None:
        nonlocal callback_count
        callback_count += 1

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)

    executor = LocalStudioGraphToolExecutor("graph", on_graph_patch_applied=on_graph_patch_applied)
    tools = LocalStudioGraphTools(executor)
    patch = {"expectedRevision": None, "ops": []}

    assert tools.graph_snapshot() == {"snapshot": {"nodeCount": 1}}
    assert tools.graph_ui_context() == {
        "uiContext": {
            "graphRevision": 7,
            "selectedNodeIds": ["node-a"],
            "selectionLabel": "node-a",
            "selectionCount": 1,
            "propertyPanelNodeId": "",
            "primaryNodeId": "node-a",
            "primaryNodeSource": "singleSelection",
        }
    }
    assert tools.graph_find_nodes(query="node", selected_only=True, limit=3) == {"nodes": [{"node_id": "node-a"}]}
    assert tools.graph_node_detail("node-a") == {"detail": {"node": {"node_id": "node-a"}}}
    assert tools.graph_connections("node-a", direction="outgoing", limit=4) == {
        "connections": [{"from_node_id": "node-a"}]
    }
    assert tools.graph_diagnostics() == {"diagnostics": {"ok": True, "issues": []}}
    assert tools.node_catalog() == {"nodes": [{"nodeType": "f8.test"}]}
    assert tools.graph_session() == {"session": {"nodes": []}}
    assert tools.graph_compile() == {"compile": {"warnings": []}}
    assert tools.graph_preview_patch(patch) == {"preview": {"valid": True}}
    assert tools.graph_apply_patch(patch) == {"preview": {"valid": True}, "snapshot": {"nodeCount": 1}}
    assert callback_count == 1
    assert calls == [
        "init:graph",
        "snapshot",
        "snapshot",
        ("find", "node", "", "", "", "", "", True, 3),
        "detail:node-a",
        "connections:node-a:outgoing:4",
        "diagnostics",
        "catalog",
        "session",
        "compile",
        "preview:GraphPatch",
        "apply:GraphPatch",
        "snapshot",
    ]


@dataclass(frozen=True)
class _FakeMonitorRow:
    service_id: str
    service_class: str
    running: bool
    active: bool | None


class _FakeBridge:
    studio_service_id = "studio"
    managed_active = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def export_monitor_report(self) -> dict[str, object]:
        self.calls.append(("export_monitor_report", (), {}))
        return {"services": [{"serviceId": "svc"}]}

    def get_latest_monitor_snapshot(self, service_id: str) -> dict[str, object] | None:
        self.calls.append(("get_latest_monitor_snapshot", (service_id,), {}))
        return {"serviceId": service_id, "ready": True}

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, object]]:
        self.calls.append(("get_monitor_snapshot_stream", (service_id,), {"limit": limit}))
        return [{"serviceId": service_id, "index": 1}]

    def list_service_monitor_rows(self) -> list[_FakeMonitorRow]:
        self.calls.append(("list_service_monitor_rows", (), {}))
        return [_FakeMonitorRow(service_id="svc", service_class="f8.test", running=True, active=True)]

    def is_service_running(self, service_id: str) -> bool:
        self.calls.append(("is_service_running", (service_id,), {}))
        return True

    def get_service_class(self, service_id: str) -> str:
        self.calls.append(("get_service_class", (service_id,), {}))
        return "f8.test"

    def get_cached_service_active(self, service_id: str) -> bool | None:
        self.calls.append(("get_cached_service_active", (service_id,), {}))
        return True

    def deploy_and_wait(self, compiled: object, *, timeout_s: float = 20.0) -> dict[str, object]:
        self.calls.append(("deploy_and_wait", (compiled,), {"timeout_s": timeout_s}))
        return {"submitted": True, "completed": True}

    def deploy_service_and_wait(
        self,
        service_id: str,
        *,
        compiled: object | None = None,
        timeout_s: float = 10.0,
    ) -> dict[str, object]:
        self.calls.append(("deploy_service_and_wait", (service_id, compiled), {"timeout_s": timeout_s}))
        return {"submitted": True, "completed": True, "deployed": True}

    def start_service(self, service_id: str, *, service_class: str | None = None) -> None:
        self.calls.append(("start_service", (service_id,), {"service_class": service_class}))

    def stop_service(self, service_id: str) -> None:
        self.calls.append(("stop_service", (service_id,), {}))

    def restart_service(self, service_id: str, *, service_class: str | None = None) -> None:
        self.calls.append(("restart_service", (service_id,), {"service_class": service_class}))

    def set_service_active(self, service_id: str, active: bool) -> None:
        self.calls.append(("set_service_active", (service_id, active), {}))

    def set_managed_active(self, active: bool) -> None:
        self.calls.append(("set_managed_active", (active,), {}))

    def set_remote_state_and_wait(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: object,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, object]:
        self.calls.append(("set_remote_state_and_wait", (service_id, node_id, field, value), {"timeout_s": timeout_s}))
        return {"accepted": True}

    def sample_data_port_and_wait(
        self,
        service_id: str,
        node_id: str,
        port: str,
        *,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
    ) -> dict[str, object]:
        self.calls.append(
            (
                "sample_data_port_and_wait",
                (service_id, node_id, port),
                {
                    "limit": limit,
                    "timeout_s": timeout_s,
                    "include_value": include_value,
                    "max_value_bytes": max_value_bytes,
                },
            )
        )
        return {"samples": [{"value": 42}]}

    def invoke_remote_command_and_wait(
        self,
        service_id: str,
        call: str,
        args: object = None,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, object]:
        self.calls.append(("invoke_remote_command_and_wait", (service_id, call, args), {"timeout_s": timeout_s}))
        return {"ok": True, "result": {"done": True}}


class _FakeLogSource:
    def export_logs(
        self,
        *,
        service_id: str = "",
        limit: int = 200,
        minimum_level: int | None = None,
    ) -> dict[str, object]:
        return {
            "services": [
                {
                    "serviceId": service_id or "studio",
                    "lines": [{"line": "hello", "level": int(minimum_level or 20)}],
                    "limit": limit,
                }
            ]
        }


def test_local_studio_graph_tools_dispatch_runtime_monitor_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    class FakePayload:
        node_count = 2
        edge_count = 1

        def to_dict(self) -> dict[str, object]:
            return {"nodeCount": self.node_count, "edgeCount": self.edge_count}

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

        def revision(self) -> int:
            return 3

        def snapshot(self) -> FakePayload:
            return FakePayload()

        def compile_graph(self) -> dict[str, object]:
            return {"warnings": []}

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    monkeypatch.setattr("f8pystudio.nodegraph.runtime_compiler.compile_runtime_graphs_from_studio", lambda graph: {"compiled": graph})

    bridge = _FakeBridge()
    tools = LocalStudioGraphTools(LocalStudioGraphToolExecutor("graph", bridge=bridge, log_source=_FakeLogSource()))

    assert tools.studio_status()["status"]["runtime"] == {"studioServiceId": "studio", "managedActive": True}
    assert tools.runtime_services()["services"][0]["service_id"] == "svc"
    assert tools.runtime_service_status("svc")["service"]["latestMonitor"] == {"serviceId": "svc", "ready": True}
    assert tools.monitor_report()["monitor"] == {"services": [{"serviceId": "svc"}]}
    assert tools.monitor_service("svc", limit=7)["stream"] == [{"serviceId": "svc", "index": 1}]
    assert tools.logs_read(service_id="studio", limit=5, minimum_level=30)["logs"]["services"][0]["lines"][0]["line"] == "hello"
    assert tools.runtime_write_state("svc", "node", "gain", 2.0)["state"] == {"accepted": True}
    assert tools.runtime_sample_port("svc", "node", "out", limit=2)["samples"] == {"samples": [{"value": 42}]}
    assert tools.runtime_invoke_command("svc", "reset", confirm=True)["command"]["ok"] is True
    assert tools.runtime_service_process("svc", "restart", service_class="f8.test") == {
        "submitted": True,
        "serviceId": "svc",
        "action": "restart",
    }
    assert tools.runtime_set_service_active("svc", False) == {"submitted": True, "serviceId": "svc", "active": False}
    assert tools.runtime_set_managed_active(False) == {"submitted": True, "active": False}
    assert tools.runtime_deploy(confirm=True, timeout_s=4.0)["deploy"]["completed"] is True
    assert tools.runtime_service_deploy("svc", timeout_s=3.0)["deploy"]["deployed"] is True

    call_names = [call[0] for call in bridge.calls]
    assert "deploy_and_wait" in call_names
    assert "deploy_service_and_wait" in call_names
    assert "restart_service" in call_names
    assert "invoke_remote_command_and_wait" in call_names


def test_local_studio_graph_tool_traces_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()
    traces: list[dict[str, object]] = []

    class FakePayload:
        node_count = 1
        edge_count = 0

        def to_dict(self) -> dict[str, object]:
            return {"nodeCount": self.node_count, "edgeCount": self.edge_count}

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

        def snapshot(self) -> FakePayload:
            return FakePayload()

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    executor = LocalStudioGraphToolExecutor("graph", on_tool_trace=traces.append)
    tools = LocalStudioGraphTools(executor)

    assert tools.graph_snapshot()["snapshot"]["nodeCount"] == 1
    with pytest.raises(ValueError, match="unsupported"):
        executor.call("graph.unknown")

    assert traces[0]["status"] == "started"
    assert traces[0]["toolName"] == "graph_snapshot"
    assert traces[1]["status"] == "completed"
    assert traces[2]["status"] == "started"
    assert traces[3]["status"] == "failed"
    assert "ValueError" in str(traces[3]["error"])


def test_runtime_action_tool_can_use_gui_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()
    approvals: list[dict[str, object]] = []

    class FakePayload:
        node_count = 1
        edge_count = 0

        def to_dict(self) -> dict[str, object]:
            return {"nodeCount": self.node_count, "edgeCount": self.edge_count}

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

        def revision(self) -> int:
            return 1

        def snapshot(self) -> FakePayload:
            return FakePayload()

        def compile_graph(self) -> dict[str, object]:
            return {"warnings": []}

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    monkeypatch.setattr("f8pystudio.nodegraph.runtime_compiler.compile_runtime_graphs_from_studio", lambda graph: {"compiled": graph})

    bridge = _FakeBridge()
    executor = LocalStudioGraphToolExecutor(
        "graph",
        bridge=bridge,
        on_tool_approval_requested=approvals.append,
        approval_timeout_s=2.0,
    )
    tools = LocalStudioGraphTools(executor)

    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}

    def call_tool() -> None:
        try:
            result_box["result"] = tools.runtime_deploy(confirm=False, timeout_s=4.0)
        except BaseException as exc:
            error_box["error"] = exc

    worker = threading.Thread(target=call_tool, daemon=True)
    worker.start()
    while not approvals and worker.is_alive():
        QtWidgets.QApplication.processEvents()
    assert approvals
    executor.resolve_approval(str(approvals[0]["approvalId"]), True)
    while worker.is_alive():
        QtWidgets.QApplication.processEvents()
        worker.join(timeout=0.01)
    assert "error" not in error_box
    result = result_box["result"]

    assert result["deploy"]["completed"] is True
    assert approvals[0]["toolName"] == "runtime_deploy"
    assert bridge.calls[-1][0] == "deploy_and_wait"


def test_runtime_action_tool_gui_approval_denial_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()
    approvals: list[dict[str, object]] = []

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    executor = LocalStudioGraphToolExecutor(
        "graph",
        bridge=_FakeBridge(),
        on_tool_approval_requested=approvals.append,
        approval_timeout_s=2.0,
    )
    tools = LocalStudioGraphTools(executor)

    error_box: dict[str, BaseException] = {}

    def call_tool() -> None:
        try:
            tools.runtime_deploy(confirm=False)
        except BaseException as exc:
            error_box["error"] = exc

    worker = threading.Thread(target=call_tool, daemon=True)
    worker.start()
    while not approvals and worker.is_alive():
        QtWidgets.QApplication.processEvents()
    assert approvals
    executor.resolve_approval(str(approvals[0]["approvalId"]), False)
    worker.join(timeout=1.0)

    assert isinstance(error_box["error"], ValueError)
    assert "approval denied" in str(error_box["error"])


def test_runtime_action_tools_require_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    tools = LocalStudioGraphTools(LocalStudioGraphToolExecutor("graph", bridge=_FakeBridge()))

    with pytest.raises(ValueError, match="confirm=true"):
        tools.runtime_deploy()
    with pytest.raises(ValueError, match="confirm=true"):
        tools.runtime_invoke_command("svc", "reset")


def test_runtime_read_and_watch_state_use_observation_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    store = RuntimeObservationStore()
    store.put_state(service_id="svc", node_id="node", field="gain", value=2.5, ts_ms=100)
    tools = LocalStudioGraphTools(LocalStudioGraphToolExecutor("graph", observation_source=store))

    assert tools.runtime_read_state("svc", "node", "gain")["state"] == {
        "serviceId": "svc",
        "nodeId": "node",
        "field": "gain",
        "value": 2.5,
        "tsMs": 100,
    }
    assert tools.runtime_watch_state("svc", "node", "gain", after_ts_ms=0, timeout_s=0.01)["state"]["tsMs"] == 100


def test_workflow_tools_preview_goal_and_auto_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    class FakeNode:
        def __init__(self, node_id: str, selected: bool) -> None:
            self.node_id = node_id
            self.node_type = "type"
            self.name = node_id
            self.kind = "operator"
            self.service_class = "f8.test"
            self.operator_class = "op"
            self.pos = (0.0, 0.0)
            self.selected = selected
            self.inputs = ()
            self.outputs = ()
            self.state_fields = ()

    class FakePreview:
        valid = True

        def to_dict(self) -> dict[str, object]:
            return {"valid": True, "changed_node_ids": ["node-a"]}

    class FakeSnapshot:
        revision = 9
        nodes = (FakeNode("node-a", True), FakeNode("node-b", False))
        node_count = 2
        edge_count = 0

        def to_dict(self) -> dict[str, object]:
            return {"revision": self.revision, "nodeCount": self.node_count}

    class FakeAdapter:
        def __init__(self, studio_graph: object) -> None:
            self.studio_graph = studio_graph

        def revision(self) -> int:
            return 9

        def snapshot(self) -> FakeSnapshot:
            return FakeSnapshot()

        def node_catalog(self) -> dict[str, object]:
            return {
                "nodes": [
                    {
                        "nodeType": "svc.f8.test",
                        "label": "Test Engine",
                        "kind": "service",
                        "serviceClass": "f8.test",
                        "operatorClass": "",
                        "inputs": [],
                        "outputs": [],
                        "stateFields": [],
                    },
                    {
                        "nodeType": "f8.test.wave",
                        "label": "Wave Source",
                        "kind": "operator",
                        "serviceClass": "f8.test",
                        "operatorClass": "wave",
                        "inputs": [],
                        "outputs": [{"name": "value", "kind": "data"}],
                        "stateFields": [{"name": "hz", "description": "Frequency"}],
                    },
                    {
                        "nodeType": "f8.test.range_map",
                        "label": "Range Map",
                        "kind": "operator",
                        "serviceClass": "f8.test",
                        "operatorClass": "range_map",
                        "inputs": [{"name": "value", "kind": "data"}],
                        "outputs": [{"name": "value", "kind": "data"}],
                        "stateFields": [{"name": "outMax", "description": "Output maximum"}],
                    },
                    {
                        "nodeType": "f8.test.viz.wave",
                        "label": "Viz Wave",
                        "kind": "operator",
                        "serviceClass": "f8.test",
                        "operatorClass": "viz.wave",
                        "inputs": [{"name": "y", "kind": "data"}],
                        "outputs": [],
                        "stateFields": [],
                    },
                ]
            }

        def preview_patch(self, patch: object) -> FakePreview:
            self.patch = patch
            return FakePreview()

        def apply_patch(self, patch: object) -> FakePreview:
            self.patch = patch
            return FakePreview()

        def diagnostics(self) -> dict[str, object]:
            return {"issues": [], "summary": {"nodeCount": 2}}

        def compile_graph(self) -> dict[str, object]:
            return {"warnings": []}

    monkeypatch.setattr("f8pystudio.agents.tools.graph.StudioGraphAutomationAdapter", FakeAdapter)
    tools = LocalStudioGraphTools(LocalStudioGraphToolExecutor("graph"))

    goal = "Create a 1Hz wave, map it to 0-100, and show it in viz wave"
    workflow = tools.graph_build_from_goal(goal)["workflow"]
    assert workflow["status"] == "planning_required"
    assert workflow["planSchema"]["type"] == "GraphBuildPlan"
    assert workflow["nextTools"] == ["graph_preview_build_plan", "graph_apply_build_plan", "graph_debug_service"]
    assert [item["nodeType"] for item in workflow["libraryMatches"]["candidates"][:2]] == [
        "f8.test.viz.wave",
        "f8.test.wave",
    ]

    matches = tools.graph_match_library(goal, limit=2)["matches"]
    assert matches["queryTerms"]
    assert len(matches["candidates"]) == 2

    plan = {
        "summary": "Create a wave processing graph with visualization.",
        "requirement": {
            "goal": goal,
            "serviceHints": ["f8.test"],
            "operatorHints": ["wave", "range_map", "viz.wave"],
            "dataFlowHints": ["wave.value -> range.value -> viz.y"],
            "validationHints": ["sample mapped.value in runtime"],
            "visualizationHints": ["viz wave y"],
        },
        "nodes": [
            {
                "nodeType": "svc.f8.test",
                "nodeId": "test_service",
                "name": "Test Engine",
                "role": "Service container",
                "position": [0, 0],
            },
            {
                "nodeType": "f8.test.wave",
                "nodeId": "wave_source",
                "name": "1 Hz Wave",
                "role": "Signal source",
                "stateValues": {"svcId": "test_service", "hz": 1.0},
                "position": [120, 80],
            },
            {
                "nodeType": "f8.test.range_map",
                "nodeId": "mapped_wave",
                "name": "0-100 Map",
                "role": "Range transform",
                "stateValues": {"svcId": "test_service", "outMin": 0.0, "outMax": 100.0},
                "position": [360, 80],
            },
            {
                "nodeType": "f8.test.viz.wave",
                "nodeId": "wave_viz",
                "name": "Wave Viz",
                "role": "Visualization",
                "stateValues": {"svcId": "test_service"},
                "position": [600, 80],
            },
        ],
        "connections": [
            {
                "fromNodeId": "wave_source",
                "fromPort": "value",
                "toNodeId": "mapped_wave",
                "toPort": "value",
                "reason": "Map the source value.",
            },
            {
                "fromNodeId": "mapped_wave",
                "fromPort": "value",
                "toNodeId": "wave_viz",
                "toPort": "y",
                "reason": "Visualize mapped output.",
            },
        ],
        "validationTargets": [
            {
                "serviceId": "test_service",
                "nodeId": "mapped_wave",
                "port": "value",
                "description": "Mapped output remains in 0-100.",
                "expectedMin": 0.0,
                "expectedMax": 100.0,
            }
        ],
    }

    preview_workflow = tools.graph_preview_build_plan(plan)["workflow"]
    assert preview_workflow["status"] == "previewed"
    assert preview_workflow["patch"]["expectedRevision"] == 9
    assert preview_workflow["patch"]["ops"][0] == {
        "op": "createNode",
        "nodeType": "svc.f8.test",
        "nodeId": "test_service",
        "name": "Test Engine",
        "pos": [0.0, 0.0],
        "selected": False,
    }
    assert preview_workflow["patch"]["ops"][-1]["op"] == "connectPorts"
    assert preview_workflow["delivery"]["status"] == "previewed"

    with pytest.raises(ValueError, match="confirm=true"):
        tools.graph_apply_build_plan(plan)

    applied_workflow = tools.graph_apply_build_plan(plan, confirm=True)["workflow"]
    assert applied_workflow["status"] == "applied"
    assert applied_workflow["diagnostics"] == {"issues": [], "summary": {"nodeCount": 2}}

    layout = tools.graph_auto_layout(selected_only=True)["workflow"]
    assert layout["status"] == "previewed"
    assert layout["patch"]["ops"][0]["op"] == "moveNode"
    assert layout["patch"]["ops"][0]["nodeId"] == "node-a"


def test_service_and_operator_library_tools_query_catalog() -> None:
    catalog = ServiceCatalog.instance()
    previous_services = catalog.services.all()
    previous_operators = catalog.operators.all()
    previous_paths = catalog.service_entry_paths()
    catalog.clear()
    try:
        catalog.register_service(
            F8ServiceSpec(
                serviceClass="f8.tests.agent",
                label="Agent Test Service",
                schemaVersion=F8ServiceSchemaVersion.f8service_1,
            )
        )
        catalog.register_operator(
            F8OperatorSpec(
                serviceClass="f8.tests.agent",
                operatorClass="gain",
                label="Gain",
                schemaVersion=F8OperatorSchemaVersion.f8operator_1,
                description="Multiply a signal.",
                tags=["math"],
                dataInPorts=[F8DataPortSpec(name="input", valueSchema=string_schema(), required=True)],
                dataOutPorts=[F8DataPortSpec(name="output", valueSchema=string_schema())],
            )
        )

        tools = LocalStudioGraphTools(LocalStudioGraphToolExecutor("graph"))

        services = tools.service_library(query="agent")
        operators = tools.operator_library(service_class="f8.tests.agent", query="gain")
        detail = tools.operator_detail("f8.tests.agent", "gain")

        assert services["services"][0]["serviceClass"] == "f8.tests.agent"
        assert operators["operators"][0]["operatorClass"] == "gain"
        assert operators["operators"][0]["dataInPorts"][0]["name"] == "input"
        assert detail["operator"]["operatorClass"] == "gain"
        assert detail["service"]["serviceClass"] == "f8.tests.agent"
    finally:
        catalog.clear()
        for service in previous_services:
            catalog.register_service(service, service_entry_path=previous_paths.get(str(service.serviceClass)))
        for operator in previous_operators:
            catalog.register_operator(operator)


def test_service_log_dock_exports_recent_lines() -> None:
    _ensure_app()
    from f8pystudio.ui.widgets.service_log_widget import ServiceLogDock

    dock = ServiceLogDock()
    dock.append("studio", "[info] hello")
    dock.append("studio", "[error] broken")

    payload = dock.export_logs(service_id="studio", limit=1)

    assert payload["services"][0]["serviceId"] == "studio"
    assert payload["services"][0]["lines"] == [{"line": "[error] broken", "level": 40}]
