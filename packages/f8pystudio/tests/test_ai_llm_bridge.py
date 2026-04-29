from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import uuid
import logging

from f8pystudio.ai_assist.graph_context import GraphContextSnapshot
from f8pystudio.ai_assist.llm_bridge import AiLlmBridge
from f8pystudio.ai_assist.store import AiProviderStore
from f8pystudio.editor_assist.workspace import (
    EditorAssistContext,
    EditorAssistDataInPort,
    EditorAssistDataOutPort,
    EditorAssistStateField,
)


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

    with patch("f8pystudio.ai_assist.llm_bridge.logger.warning") as warning_mock:
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

    monkeypatch.setattr("f8pystudio.ai_assist.llm_bridge.QtGui.QGuiApplication.clipboard", lambda: _FakeClipboard())
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
