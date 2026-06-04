from __future__ import annotations

import asyncio
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


class FakeAgentSession:
    def __init__(self, *, session_id: str | None = None, service_session_id: str | None = None) -> None:
        self.session_id = session_id
        self.service_session_id = service_session_id


class FakeAgent:
    calls: list[dict[str, object]] = []

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
            return _fake_stream()
        return _fake_response()


async def _fake_response() -> FakeAgentResponse:
    return FakeAgentResponse("```python\nprint('ok')\n```")


async def _fake_stream() -> AsyncIterator[FakeAgentResponseUpdate]:
    yield FakeAgentResponseUpdate("hel")
    yield FakeAgentResponseUpdate("lo")


def _install_fake_agent_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.SimpleNamespace(
        Agent=FakeAgent,
        AgentResponse=FakeAgentResponse,
        AgentResponseUpdate=FakeAgentResponseUpdate,
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
        protocol="openai",
        api_mode="responses",
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
    assert call["session"] is not None
    assert isinstance(call["session"], FakeAgentSession)
    assert call["session"].session_id == "sidebar:::"
    assert call["options"] == {"max_tokens": 8192, "reasoning": {"effort": "high"}}
    assert "document editing assistant" in str(call["instructions"])


def test_runtime_stream_yields_chunks_and_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgent.calls.clear()
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
    assert FakeAgent.calls[0]["options"] == {"max_tokens": 4096}
