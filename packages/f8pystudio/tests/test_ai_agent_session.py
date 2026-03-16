from __future__ import annotations

from typing import Any

from f8pystudio.ai_assist.agent_session import GraphAgentSession
from f8pystudio.ai_assist.graph_agent_tools import AgentToolCall, AgentToolResult
from f8pystudio.ai_assist.graph_context import GraphAgentSeedContext
from f8pystudio.ai_assist.registry import ProviderConfig


class _FakeHttpClient:
    def __init__(self, responses: list[tuple[str, str | None]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_completion(
        self,
        cfg: ProviderConfig,
        *,
        model_id: str,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
        on_result,
    ) -> None:
        self.calls.append(
            {
                "cfg": cfg,
                "model_id": model_id,
                "messages": list(messages),
                "system": system,
                "max_tokens": max_tokens,
            }
        )
        text, err = self._responses.pop(0)
        on_result(text, err)


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[AgentToolCall] = []

    def execute_tool_call(self, call: AgentToolCall) -> AgentToolResult:
        self.calls.append(call)
        return AgentToolResult(
            tool_name=call.tool_name,
            arguments=call.arguments,
            success=True,
            payload={"node_id": "node-sorter", "state_fields": ["enabled"]},
            summary="Loaded node spec",
        )


def test_graph_agent_session_runs_tool_loop_then_finishes() -> None:
    http = _FakeHttpClient(
        responses=[
            ('{"type":"tool_call","tool_name":"get_node_spec","arguments":{"node_id":"node-sorter","sections":["state_fields"]},"reason":"Need state metadata"}', None),
            ('{"type":"final_answer","answer_markdown":"The node exposes an `enabled` state field."}', None),
        ]
    )
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    message_lengths: list[int] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[{"role": "user", "content": "What state does this node have?"}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
        on_messages_changed=lambda messages: message_lengths.append(len(messages)),
    )

    session.start()

    assert len(tool_executor.calls) == 1
    assert tool_executor.calls[0].tool_name == "get_node_spec"
    assert chunks == ["The node exposes an `enabled` state field."]
    assert outcomes == [{"answer_markdown": "The node exposes an `enabled` state field.", "error": ""}]
    assert [event["event_type"] for event in traces] == ["tool_call", "tool_result", "final_answer"]
    assert message_lengths == [1, 3]


def test_graph_agent_session_repairs_invalid_json_once() -> None:
    http = _FakeHttpClient(
        responses=[
            ("not json", None),
            ('{"type":"clarifying_question","question":"Which node do you mean?"}', None),
        ]
    )
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[{"role": "user", "content": "Tell me more."}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert tool_executor.calls == []
    assert chunks == ["Which node do you mean?"]
    assert outcomes == [{"answer_markdown": "Which node do you mean?", "error": ""}]
    assert traces[0]["event_type"] == "parse_error"
    assert traces[0]["payload"]["raw_response_excerpt"] == "not json"


def test_graph_agent_session_fails_after_second_invalid_json() -> None:
    http = _FakeHttpClient(
        responses=[
            ("not json", None),
            ("still not json", None),
        ]
    )
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[{"role": "user", "content": "Tell me more."}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert chunks == []
    assert outcomes[0]["error"].startswith("Graph agent returned invalid JSON twice.")
    assert traces[-1]["event_type"] == "error"


def test_graph_agent_session_extracts_json_object_from_surrounding_prose() -> None:
    http = _FakeHttpClient(
        responses=[
            ('Sure, here is the next step:\n{"type":"final_answer","answer_markdown":"Use the sorter output."}\nThanks!', None),
        ]
    )
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[{"role": "user", "content": "Tell me more."}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert chunks == ["Use the sorter output."]
    assert outcomes == [{"answer_markdown": "Use the sorter output.", "error": ""}]
    assert [event["event_type"] for event in traces] == ["final_answer"]


def test_graph_agent_session_coerces_plaintext_answer_after_tool_steps() -> None:
    http = _FakeHttpClient(
        responses=[
            ('{"type":"tool_call","tool_name":"get_node_spec","arguments":{"node_id":"node-sorter","sections":["data_out_ports"]},"reason":"Need output port metadata"}', None),
            ("`detections` is typically an array of detection records produced by the node's output port.", None),
        ]
    )
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[{"role": "user", "content": "What is detections?"}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert len(tool_executor.calls) == 1
    assert chunks == ["`detections` is typically an array of detection records produced by the node's output port."]
    assert outcomes == [
        {
            "answer_markdown": "`detections` is typically an array of detection records produced by the node's output port.",
            "error": "",
        }
    ]
    assert [event["event_type"] for event in traces] == ["tool_call", "tool_result", "final_answer"]


def test_graph_agent_session_answers_selected_node_identity_directly_from_seed_context() -> None:
    http = _FakeHttpClient(responses=[])
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(
            selection_label="1 selected node",
            selected_node_ids=("3Q7h",),
            focus_node_ids=("3Q7h",),
            focus_node_names=("CVKit Template Match",),
        ),
        history=[{"role": "user", "content": "我选中的节点叫什么"}],
        attachments=[],
        on_trace=lambda event: traces.append(event.to_dict()),
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert http.calls == []
    assert tool_executor.calls == []
    assert chunks == ["当前被选中的节点叫 `CVKit Template Match`，id 是 `3Q7h`。"]
    assert outcomes == [{"answer_markdown": "当前被选中的节点叫 `CVKit Template Match`，id 是 `3Q7h`。", "error": ""}]
    assert traces == [
        {
            "event_type": "final_answer",
            "step_index": 0,
            "tool_name": "",
            "reason": "",
            "summary": "Answered directly from seed context.",
            "error": "",
            "arguments": {},
            "payload": {"source": "seed_context_fast_path"},
        }
    ]


def test_graph_agent_session_answers_english_selected_node_question_directly_from_seed_context() -> None:
    http = _FakeHttpClient(responses=[])
    tool_executor = _FakeToolExecutor()
    chunks: list[str] = []
    outcomes: list[dict[str, Any]] = []
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(
            selection_label="1 selected node",
            selected_node_ids=("3Q7h",),
            focus_node_ids=("3Q7h",),
            focus_node_names=("CVKit Template Match",),
        ),
        history=[{"role": "user", "content": "which node is selected?"}],
        attachments=[],
        on_trace=lambda _event: None,
        on_chunk=lambda text: chunks.append(text),
        on_done=lambda outcome: outcomes.append(outcome.to_dict()),
    )

    session.start()

    assert http.calls == []
    assert tool_executor.calls == []
    assert chunks == ["当前被选中的节点叫 `CVKit Template Match`，id 是 `3Q7h`。"]
    assert outcomes == [{"answer_markdown": "当前被选中的节点叫 `CVKit Template Match`，id 是 `3Q7h`。", "error": ""}]


def test_graph_agent_system_prompt_disambiguates_studio_graph_from_image_graph() -> None:
    http = _FakeHttpClient(responses=[])
    tool_executor = _FakeToolExecutor()
    session = GraphAgentSession(
        http_client=http,
        provider_config=ProviderConfig(provider_id="openai", display_name="OpenAI"),
        model_id="gpt-4.1",
        tool_executor=tool_executor,
        seed_context=GraphAgentSeedContext(selection_label="1 node"),
        history=[],
        attachments=[],
        on_trace=lambda _event: None,
        on_chunk=lambda _text: None,
        on_done=lambda _outcome: None,
    )

    prompt = session.system_prompt

    assert "not a chart, plot, screenshot, or external image" in prompt
    assert "Do not ask the user to upload, paste, or link a graph image" in prompt
    assert "selected_node_ids" in prompt
    assert "focus_node_ids" in prompt
    assert "## Current Selection Focus" in prompt
    assert "No selected focus nodes are available" in prompt
    assert "which node is selected?" in prompt
    assert "当前被选中的节点" in prompt
    assert "do not call `resolve_nodes` with the whole user request" in prompt
    assert "If you already know a node_id" in prompt
    assert "Tool selection examples" in prompt
    assert "Available tools:" in prompt
    assert "`get_node_spec`" in prompt
    assert "## Tool Usage Guide" in prompt
