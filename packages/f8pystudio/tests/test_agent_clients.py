from __future__ import annotations

import types

import pytest

from f8pystudio.agents.clients import (
    AgentClientSelection,
    build_chat_client,
    effective_chat_model_id,
    effective_inline_model_id,
)
from f8pystudio.agents.registry import ModelInfo, ProviderConfig


def test_effective_model_id_uses_selected_then_provider_then_first_cached() -> None:
    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        cached_models=[ModelInfo(model_id="first", display_name="First")],
        inline_model_id="inline",
        chat_model_id="chat",
    )

    assert effective_chat_model_id(cfg, "selected") == "selected"
    assert effective_chat_model_id(cfg, "") == "chat"
    cfg.chat_model_id = ""
    assert effective_chat_model_id(cfg, "") == "first"

    assert effective_inline_model_id(cfg, "selected") == "selected"
    assert effective_inline_model_id(cfg, "") == "inline"
    cfg.inline_model_id = ""
    assert effective_inline_model_id(cfg, "") == "first"


def test_openai_responses_client_uses_agent_framework_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeResponsesClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(("responses", kwargs))

    class FakeChatCompletionsClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(("chat", kwargs))

    fake_module = types.SimpleNamespace(
        OpenAIChatClient=FakeResponsesClient,
        OpenAIChatCompletionClient=FakeChatCompletionsClient,
    )
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.openai", fake_module)

    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        protocol="openai",
        api_mode="responses",
        api_key="sk-test",
        endpoint="https://api.openai.com/v1/",
    )

    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="gpt-4.1"))

    assert isinstance(client, FakeResponsesClient)
    assert created == [
        (
            "responses",
            {"model": "gpt-4.1", "api_key": "sk-test", "base_url": "https://api.openai.com/v1"},
        )
    ]


def test_openai_chat_completion_client_selected_for_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeResponsesClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(("responses", kwargs))

    class FakeChatCompletionsClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(("chat", kwargs))

    fake_module = types.SimpleNamespace(
        OpenAIChatClient=FakeResponsesClient,
        OpenAIChatCompletionClient=FakeChatCompletionsClient,
    )
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.openai", fake_module)

    cfg = ProviderConfig(
        provider_id="compatible",
        display_name="Compatible",
        protocol="custom",
        api_mode="chat_completions",
        endpoint="https://example.test/v1",
    )

    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="model-a"))

    assert isinstance(client, FakeChatCompletionsClient)
    assert created == [
        (
            "chat",
            {"model": "model-a", "api_key": "", "base_url": "https://example.test/v1"},
        )
    ]


def test_ollama_uses_openai_compatible_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeResponsesClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    class FakeChatCompletionsClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(
        OpenAIChatClient=FakeResponsesClient,
        OpenAIChatCompletionClient=FakeChatCompletionsClient,
    )
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.openai", fake_module)

    cfg = ProviderConfig(provider_id="ollama", display_name="Ollama", protocol="ollama")
    build_chat_client(AgentClientSelection(provider=cfg, model_id="llama3"))

    assert created == [{"model": "llama3", "api_key": "", "base_url": "http://localhost:11434/v1"}]
