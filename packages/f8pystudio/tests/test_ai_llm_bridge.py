from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import uuid
import logging

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
                    name="lastError",
                    required=False,
                    value_schema={"type": "string"},
                    access="wo",
                    description="Most recent execution error.",
                ),
            ),
        )
    )

    text = bridge._format_assist_context()

    assert "## Node Metadata" in text
    assert "- Kind: `operator`" in text
    assert "- Service: `f8.pyengine`" in text
    assert "- Operator: `f8.python_script`" in text
    assert "- Description: Execute custom python logic." in text
    assert "## Input Ports (`dataInPorts`)" in text
    assert "`track` (required, schema=object<frameId>) | description=Incoming track payload." in text
    assert "## Output Ports (`dataOutPorts`)" in text
    assert "`result` (optional, schema=string) | description=Script output text." in text
    assert "## State Fields (`stateFields`)" in text
    assert "`lastError` (optional, access=wo, schema=string) | description=Most recent execution error." in text


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
