from __future__ import annotations

import asyncio
import contextvars
import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest

from f8pystudio.agents.registry import ModelInfo, ProviderConfig
from f8pystudio.agents.runtime import StudioAgentEvent, StudioAgentRequest, StudioAgentRuntime
from f8pystudio.agents.sessions import StudioAgentSessionKey
from f8pystudio.agents.store import AiProviderStore


class FakeAgentResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeAgentResponseUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeAgentMessage:
    def __init__(self, role: str, contents: list[object]) -> None:
        self.role = role
        self.contents = contents


class FakeAgentSession:
    def __init__(self, *, session_id: str | None = None, service_session_id: str | None = None) -> None:
        self.session_id = session_id
        self.service_session_id = service_session_id


_fake_stream_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fake_agent_stream_context",
    default=None,
)


class FakeAgent:
    calls: list[dict[str, object]] = []
    stream_mode = "normal"

    def __init__(self, client: object, instructions: str, name: str) -> None:
        self.client = client
        self.instructions = instructions
        self.name = name

    def run(
        self,
        messages: object = None,
        *,
        stream: bool = False,
        session: object = None,
        tools: object = None,
        options: object = None,
    ) -> object:
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    raise AttributeError("'dict' object has no attribute 'role'")
                _ = message.role
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "session": session,
                "tools": tools,
                "options": options,
                "instructions": self.instructions,
            }
        )
        if stream:
            if self.stream_mode == "first_timeout":
                return _never_stream()
            if self.stream_mode == "idle_timeout":
                return _idle_timeout_stream()
            if self.stream_mode == "context_token":
                return _context_token_stream(_fake_stream_context.set("active"))
            if self.stream_mode == "heartbeat":
                return _heartbeat_stream()
            return _fake_stream()
        return _fake_response()


async def _fake_response() -> FakeAgentResponse:
    return FakeAgentResponse("```python\nprint('ok')\n```")


async def _fake_stream() -> AsyncIterator[FakeAgentResponseUpdate]:
    yield FakeAgentResponseUpdate("hel")
    yield FakeAgentResponseUpdate("lo")


async def _never_stream() -> AsyncIterator[FakeAgentResponseUpdate]:
    await asyncio.sleep(10)
    yield FakeAgentResponseUpdate("late")


async def _idle_timeout_stream() -> AsyncIterator[FakeAgentResponseUpdate]:
    yield FakeAgentResponseUpdate("hel")
    await asyncio.sleep(10)
    yield FakeAgentResponseUpdate("late")


async def _context_token_stream(
    token: contextvars.Token[str | None],
) -> AsyncIterator[FakeAgentResponseUpdate]:
    try:
        yield FakeAgentResponseUpdate("ctx")
    finally:
        _fake_stream_context.reset(token)


async def _heartbeat_stream() -> AsyncIterator[FakeAgentResponseUpdate]:
    yield FakeAgentResponseUpdate("")
    await asyncio.sleep(0.02)
    yield FakeAgentResponseUpdate("ok")


def _install_fake_agent_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.SimpleNamespace(
        Agent=FakeAgent,
        AgentResponse=FakeAgentResponse,
        AgentResponseUpdate=FakeAgentResponseUpdate,
        Message=FakeAgentMessage,
        AgentSession=FakeAgentSession,
    )
    monkeypatch.setitem(sys.modules, "agent_framework", module)


def _store(tmp_path: Path) -> AiProviderStore:
    store_path = tmp_path / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        store = AiProviderStore()
    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        inference_service="openai_responses",
        chat_model_id="gpt-4.1",
        cached_models=[ModelInfo(model_id="gpt-4.1", display_name="GPT-4.1")],
    )
    store.save_provider(cfg, emit=False)
    store.save_active_providers("openai", "openai")
    return store


def test_runtime_runs_edit_through_agent_framework_and_strips_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(_store(tmp_path))
    result = asyncio.run(
        runtime.run_text(
            StudioAgentRequest(
                request_id="edit-1",
                mode="edit",
                code="print('old')",
                instruction="make it ok",
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
                reasoning_level="high",
                session_key=StudioAgentSessionKey.sidebar(),
            )
        )
    )

    assert result == "print('ok')"
    assert FakeAgent.calls
    call = FakeAgent.calls[0]
    assert call["stream"] is False
    assert call["session"] is None
    assert call["options"] == {"max_tokens": 8192, "store": False, "reasoning": {"effort": "high"}}
    assert "document editing assistant" in str(call["instructions"])


def test_runtime_non_streaming_keeps_session_for_non_responses_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    store = _store(tmp_path)
    cfg = store.provider_by_id("openai")
    assert cfg is not None
    cfg.inference_service = "openai_chat_completion"
    store.save_provider(cfg, emit=False)

    runtime = StudioAgentRuntime(store)
    result = asyncio.run(
        runtime.run_text(
            StudioAgentRequest(
                request_id="chat-openai-chat-session-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
                session_key=StudioAgentSessionKey.sidebar(),
            )
        )
    )

    assert result == "```python\nprint('ok')\n```"
    call = FakeAgent.calls[0]
    assert call["stream"] is False
    assert isinstance(call["session"], FakeAgentSession)
    assert call["session"].session_id == "sidebar:::"


def test_runtime_reasoning_option_is_limited_to_openai_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    store = _store(tmp_path)
    cfg = ProviderConfig(
        provider_id="foundry",
        display_name="Foundry",
        inference_service="foundry_agent",
        endpoint="https://example.services.ai.azure.com/api/projects/project-a",
        chat_model_id="agent-name",
        cached_models=[ModelInfo(model_id="agent-name", display_name="Agent")],
    )
    store.save_provider(cfg, emit=False)

    runtime = StudioAgentRuntime(store)
    result = asyncio.run(
        runtime.run_text(
            StudioAgentRequest(
                request_id="foundry-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="foundry",
                chat_model_id="agent-name",
                reasoning_level="high",
            )
        )
    )

    assert result == "```python\nprint('ok')\n```"
    assert FakeAgent.calls[0]["options"] == {"max_tokens": 4096}


def test_runtime_stream_yields_chunks_and_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(_store(tmp_path))
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events == [
        StudioAgentEvent(kind="chunk", text="hel"),
        StudioAgentEvent(kind="chunk", text="lo"),
        StudioAgentEvent(kind="done"),
    ]
    assert FakeAgent.calls[0]["stream"] is True
    assert FakeAgent.calls[0]["options"] == {"max_tokens": 4096, "store": False}
    call_messages = FakeAgent.calls[0]["messages"]
    assert isinstance(call_messages, list)
    assert isinstance(call_messages[0], FakeAgentMessage)
    assert call_messages[0].role == "user"


def test_runtime_stream_disables_openai_responses_service_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(_store(tmp_path))
    session = runtime._session_registry.session_for(StudioAgentSessionKey.sidebar())
    assert isinstance(session, FakeAgentSession)
    session.service_session_id = "resp_previous"
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-openai-responses-session-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
                session_key=StudioAgentSessionKey.sidebar(),
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events[-1] == StudioAgentEvent(kind="done")
    assert FakeAgent.calls[0]["session"] is None
    assert FakeAgent.calls[0]["options"] == {"max_tokens": 4096, "store": False}


def test_runtime_stream_keeps_session_for_non_responses_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "normal"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    store = _store(tmp_path)
    cfg = store.provider_by_id("openai")
    assert cfg is not None
    cfg.inference_service = "openai_chat_completion"
    store.save_provider(cfg, emit=False)

    runtime = StudioAgentRuntime(store)
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-openai-chat-session-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
                session_key=StudioAgentSessionKey.sidebar(),
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events[-1] == StudioAgentEvent(kind="done")
    assert isinstance(FakeAgent.calls[0]["session"], FakeAgentSession)


def test_runtime_stream_times_out_waiting_for_first_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "first_timeout"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(
        _store(tmp_path),
        stream_first_event_timeout_s=0.01,
        stream_idle_timeout_s=0.01,
    )
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-timeout-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert len(events) == 1
    assert events[0].kind == "error"
    assert "first event" in events[0].error


def test_runtime_stream_times_out_after_idle_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "idle_timeout"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(
        _store(tmp_path),
        stream_first_event_timeout_s=0.2,
        stream_idle_timeout_s=0.01,
    )
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-timeout-2",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events[0] == StudioAgentEvent(kind="chunk", text="hel")
    assert events[1].kind == "error"
    assert "idle" in events[1].error


def test_runtime_abort_wakes_stream_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "first_timeout"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(
        _store(tmp_path),
        stream_first_event_timeout_s=10.0,
        stream_idle_timeout_s=10.0,
    )
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async def _abort_later() -> None:
            await asyncio.sleep(0.01)
            runtime.abort_request("chat-abort-1")

        abort_task = asyncio.create_task(_abort_later())
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-abort-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)
        await abort_task

    asyncio.run(_collect())

    assert events == []


def test_runtime_stream_consumes_provider_stream_in_creation_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "context_token"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(_store(tmp_path))
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-context-token-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events == [
        StudioAgentEvent(kind="chunk", text="ctx"),
        StudioAgentEvent(kind="done"),
    ]


def test_runtime_stream_treats_empty_provider_updates_as_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
    FakeAgent.stream_mode = "heartbeat"
    _install_fake_agent_framework(monkeypatch)
    monkeypatch.setattr("f8pystudio.agents.runtime.build_chat_client", lambda _selection: object())

    runtime = StudioAgentRuntime(
        _store(tmp_path),
        stream_first_event_timeout_s=0.01,
        stream_idle_timeout_s=0.2,
    )
    events: list[StudioAgentEvent] = []

    async def _collect() -> None:
        async for event in runtime.run_stream(
            StudioAgentRequest(
                request_id="chat-heartbeat-1",
                mode="chat",
                messages=({"role": "user", "content": "hello"},),
                chat_provider_id="openai",
                chat_model_id="gpt-4.1",
            )
        ):
            events.append(event)

    asyncio.run(_collect())

    assert events == [
        StudioAgentEvent(kind="chunk", text="ok"),
        StudioAgentEvent(kind="done"),
    ]
