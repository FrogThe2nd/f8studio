from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from unittest.mock import patch
import uuid
import logging
import time

from qtpy import QtTest, QtWidgets

from f8pystudio.agents.graph_context import GraphContextSnapshot
from f8pystudio.agents.qt_bridge import AiLlmBridge
from f8pystudio.agents.registry import ModelInfo, ProviderConfig
from f8pystudio.agents.runtime import StudioAgentEvent, StudioAgentRequest
from f8pystudio.agents.store import AiProviderStore
from f8pystudio.editor_assist.workspace import (
    EditorAssistContext,
    EditorAssistDataInPort,
    EditorAssistDataOutPort,
    EditorAssistStateField,
)


class _FailingStreamRuntime:
    async def run_stream(self, request: StudioAgentRequest) -> AsyncIterator[StudioAgentEvent]:
        raise KeyError("stream failed")
        yield


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _wait_until(predicate, *, timeout_ms: int = 3000) -> None:
    deadline = time.monotonic() + (float(timeout_ms) / 1000.0)
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()
    assert predicate()


def test_format_assist_context_includes_node_metadata_outputs_and_descriptions() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_assist_context(
        EditorAssistContext(
            language="python",
            node_kind="operator",
            service_class="f8.pyengine",
            operator_class="f8.python_script",
            node_description="Execute custom python logic.",
            node_instance_purpose="Transform incoming tracks into a compact summary for this graph.",
            target_field_kind="state",
            target_field_name="code",
            target_field_label="Script Body",
            target_field_description="Primary script source.",
            target_ui_language="python",
            target_value_schema={"type": "string"},
            data_in_ports=(
                EditorAssistDataInPort(
                    name="track",
                    required=True,
                    value_schema={"type": "object", "properties": {"frameId": {"type": "integer"}}},
                    description="Incoming track payload.",
                ),
            ),
            data_out_ports=(
                EditorAssistDataOutPort(
                    name="result",
                    required=False,
                    value_schema={"type": "string"},
                    description="Script output text.",
                ),
            ),
            state_fields=(
                EditorAssistStateField(
                    name="inputMode",
                    required=False,
                    value_schema={"type": "string"},
                    access="rw",
                    description="Input binding mode.",
                ),
            ),
        )
    )

    text = bridge._format_assist_context()

    assert "## Node Metadata" in text
    assert "## Editing Target" in text
    assert "- Document language: `python`" in text
    assert "- Target field: `code`" in text
    assert "- Target description: Primary script source." in text
    assert "- Kind: `operator`" in text
    assert "- Service: `f8.pyengine`" in text
    assert "- Operator: `f8.python_script`" in text
    assert "- Type Description: Execute custom python logic." in text
    assert "- Instance Purpose: Transform incoming tracks into a compact summary for this graph." in text
    assert "## Input Ports (`dataInPorts`)" in text
    assert "`track` (required, schema=object<frameId>) | description=Incoming track payload." in text
    assert "## Output Ports (`dataOutPorts`)" in text
    assert "`result` (optional, schema=string) | description=Script output text." in text
    assert "## State Fields (`stateFields`)" in text
    assert "`inputMode` (optional, access=rw, schema=string) | description=Input binding mode." in text


def test_get_system_prompt_biases_json_generation_toward_json() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_assist_context(
        EditorAssistContext(
            language="json",
            target_field_kind="state",
            target_field_name="clsWeights",
            target_field_description="JSON map of detection cls -> weight multiplier.",
            target_ui_language="json",
        )
    )

    prompt = bridge._get_system_prompt("Base prompt.")

    assert "JSON document" in prompt
    assert "Do not default to Python" in prompt
    assert "clsWeights" in prompt


def test_build_chat_messages_uses_language_fenced_context() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_document_language("json")

    messages = bridge._build_chat_messages(
        history=[{"role": "user", "content": "make a template"}],
        code='{"a": 1}',
        selection="",
        system_prompt="system",
        attachments=None,
    )

    assert "```json" in str(messages[1]["content"])
    assert "Current editor content (json)" in str(messages[1]["content"])


def test_debug_prompt_flag_logs_payload() -> None:
    with patch.dict("os.environ", {"F8_AI_DEBUG_PROMPT": "1"}, clear=False):
        temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
        temp_dir.mkdir(parents=True, exist_ok=True)
        store_path = temp_dir / "ai_providers.json"
        with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
            bridge = AiLlmBridge(AiProviderStore())

    with patch("f8pystudio.agents.qt_bridge.logger.warning") as warning_mock:
        bridge._log_prompt_payload(
            mode="chat",
            system_prompt="system",
            messages=[{"role": "system", "content": "system"}],
        )
    warning_mock.assert_called_once()


def test_selection_state_exposes_public_bridge_choices() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_inline_model("openai", "gpt-4.1")
    bridge.set_chat_model("anthropic", "claude-opus-4-5")
    bridge.set_reasoning_level("high")

    selection_state = bridge.selection_state()

    assert selection_state.inline_provider_id == "openai"
    assert selection_state.inline_model_id == "gpt-4.1"
    assert selection_state.chat_provider_id == "anthropic"
    assert selection_state.chat_model_id == "claude-opus-4-5"
    assert selection_state.reasoning_level == "high"


def test_get_clipboard_image_returns_empty_payload_when_no_image(monkeypatch) -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    class _FakeImage:
        def isNull(self) -> bool:
            return True

    class _FakeClipboard:
        def image(self) -> _FakeImage:
            return _FakeImage()

    monkeypatch.setattr("f8pystudio.agents.qt_bridge.QtGui.QGuiApplication.clipboard", lambda: _FakeClipboard())
    payload = bridge.get_clipboard_image()

    assert payload == {"name": "", "content": "", "mime": ""}


def test_get_system_prompt_includes_pinned_graph_context_only_when_set() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    baseline_prompt = bridge._get_system_prompt("Base prompt.")
    assert "Focused Graph Subgraph Snapshot" not in baseline_prompt

    bridge.set_chat_context_snapshot(
        GraphContextSnapshot(
            selection_label="2 selected nodes",
            selected_node_ids=("node-sorter", "node-validator"),
            total_selected_count=2,
            total_one_hop_count=1,
            total_connection_count=2,
        )
    )

    prompt_with_graph = bridge._get_system_prompt("Base prompt.")
    assert "Focused Graph Subgraph Snapshot" in prompt_with_graph
    assert "2 selected nodes" in prompt_with_graph
    assert "not the full graph" in prompt_with_graph

    bridge.clear_chat_context_snapshot()
    assert "Focused Graph Subgraph Snapshot" not in bridge._get_system_prompt("Base prompt.")


def test_agent_request_uses_chat_tools_and_auto_graph_context() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    def graph_snapshot() -> dict[str, object]:
        return {"snapshot": {"nodeCount": 1}}

    bridge.set_agent_tools((graph_snapshot,))
    bridge.set_auto_chat_context_snapshot(
        GraphContextSnapshot(
            selection_label="Auto Node",
            selected_node_ids=("node-auto",),
            total_selected_count=1,
            total_one_hop_count=0,
            total_connection_count=0,
        )
    )

    request = bridge._agent_request(request_id="rid-chat", mode="chat")

    assert request.tools == (graph_snapshot,)
    assert request.graph_context_snapshot is not None
    assert request.graph_context_snapshot.selection_label == "Auto Node"
    prompt = bridge._get_system_prompt("Base prompt.")
    assert "Auto Node" in prompt
    assert "PyStudio Graph Tools" in prompt
    assert "graph_snapshot" in prompt


def test_agent_request_keeps_graph_tools_out_of_inline_requests() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    def graph_snapshot() -> dict[str, object]:
        return {"snapshot": {"nodeCount": 1}}

    bridge.set_agent_tools((graph_snapshot,))

    request = bridge._agent_request(request_id="rid-inline", mode="inline")

    assert request.tools == ()


def test_bridge_publishes_tool_trace_and_resolves_approval(tmp_path: Path) -> None:
    _ensure_app()
    store_path = tmp_path / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    trace_spy = QtTest.QSignalSpy(bridge.tool_trace_ready)
    approval_spy = QtTest.QSignalSpy(bridge.tool_approval_requested)
    resolved: list[tuple[str, bool]] = []
    bridge.set_tool_approval_resolver(lambda approval_id, approved: resolved.append((approval_id, approved)))

    bridge._set_active_stream_request_id("rid-tool")
    bridge.publish_tool_trace({"toolCallId": "tool-1", "toolName": "graph_snapshot", "status": "started"})
    bridge.publish_tool_approval({"approvalId": "approval-1", "toolName": "runtime_deploy"})
    bridge.resolve_tool_approval("approval-1", True)

    trace_payload = json.loads(str(trace_spy.at(0)[1]))
    approval_payload = json.loads(str(approval_spy.at(0)[1]))

    assert trace_spy.at(0)[0] == "rid-tool"
    assert trace_payload == {"toolCallId": "tool-1", "toolName": "graph_snapshot", "status": "started"}
    assert approval_spy.at(0)[0] == "rid-tool"
    assert approval_payload == {"approvalId": "approval-1", "toolName": "runtime_deploy"}
    assert resolved == [("approval-1", True)]
    bridge._clear_active_stream_request_id("rid-tool")


def test_pinned_graph_context_overrides_auto_graph_context() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_auto_chat_context_snapshot(
        GraphContextSnapshot(
            selection_label="Auto Node",
            selected_node_ids=("node-auto",),
            total_selected_count=1,
        )
    )
    bridge.set_chat_context_snapshot(
        GraphContextSnapshot(
            selection_label="Pinned Node",
            selected_node_ids=("node-pinned",),
            total_selected_count=1,
        )
    )

    request = bridge._agent_request(request_id="rid-chat", mode="chat")

    assert request.graph_context_snapshot is not None
    assert request.graph_context_snapshot.selection_label == "Pinned Node"


def test_reset_chat_history_clears_pinned_graph_context() -> None:
    temp_dir = Path(".tmp") / "test_ai_llm_bridge" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        bridge = AiLlmBridge(AiProviderStore())

    bridge.set_chat_context_snapshot(
        GraphContextSnapshot(
            selection_label="2 selected nodes",
            selected_node_ids=("node-sorter", "node-validator"),
            total_selected_count=2,
            total_one_hop_count=1,
            total_connection_count=2,
        )
    )

    assert "2 selected nodes" in bridge.get_chat_context_report()
    bridge.reset_chat_history()
    assert "_No pinned graph context._" in bridge.get_chat_context_report()


def test_stream_request_thread_reports_unexpected_runtime_exception(tmp_path: Path) -> None:
    _ensure_app()
    store_path = tmp_path / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        store = AiProviderStore()

    store.save_provider(
        ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            inference_service="openai_responses",
            chat_model_id="gpt-4.1",
            cached_models=[ModelInfo(model_id="gpt-4.1", display_name="GPT-4.1")],
        ),
        emit=False,
    )
    store.save_active_providers("openai", "openai")
    bridge = AiLlmBridge(store)
    bridge._runtime = _FailingStreamRuntime()
    spy = QtTest.QSignalSpy(bridge.chat_done)

    bridge.request_chat("rid-fail", '[{"role":"user","content":"hello"}]', "", "", "")

    _wait_until(lambda: spy.count() > 0, timeout_ms=1000)
    assert list(spy.at(0)) == ["rid-fail", "KeyError: 'stream failed'"]
