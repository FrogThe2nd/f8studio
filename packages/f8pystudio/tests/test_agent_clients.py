from __future__ import annotations

import types

import pytest

from f8pystudio.agents.clients import (
    AgentClientSelection,
    build_chat_client,
    effective_chat_model_id,
    effective_inline_model_id,
)
from f8pystudio.agents.registry import ModelCapabilities, ModelInfo, ProviderConfig


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


def test_effective_model_id_fallback_skips_non_agent_models() -> None:
    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        cached_models=[
            ModelInfo(
                model_id="gpt-image-2",
                display_name="GPT Image 2",
                capabilities=ModelCapabilities(model_kind="image", supports_agent_chat=False),
            ),
            ModelInfo(model_id="gpt-5", display_name="GPT-5"),
        ],
    )

    assert effective_chat_model_id(cfg, "") == "gpt-5"
    assert effective_inline_model_id(cfg, "") == "gpt-5"


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
        inference_service="openai_responses",
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
        inference_service="custom_chat_client",
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


def test_azure_openai_responses_client_uses_azure_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
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
        provider_id="azure",
        display_name="Azure",
        inference_service="azure_openai_responses",
        api_key="azure-key",
        endpoint="https://example.openai.azure.com/",
        api_version="2025-04-01-preview",
    )

    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="gpt-5"))

    assert isinstance(client, FakeResponsesClient)
    assert created == [
        (
            "responses",
            {
                "model": "gpt-5",
                "api_key": "azure-key",
                "azure_endpoint": "https://example.openai.azure.com",
                "api_version": "2025-04-01-preview",
            },
        )
    ]


def test_ollama_uses_maf_ollama_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeOllamaChatClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(OllamaChatClient=FakeOllamaChatClient)
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.ollama", fake_module)

    cfg = ProviderConfig(provider_id="ollama", display_name="Ollama", inference_service="ollama_chat")
    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="llama3"))

    assert isinstance(client, FakeOllamaChatClient)
    assert created == [{"model": "llama3", "host": "http://localhost:11434"}]


def test_anthropic_uses_maf_anthropic_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeAnthropicClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(AnthropicClient=FakeAnthropicClient)
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.anthropic", fake_module)

    cfg = ProviderConfig(
        provider_id="anthropic",
        display_name="Anthropic",
        inference_service="anthropic_claude",
        api_key="anthropic-key",
    )
    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="claude-sonnet-4-5"))

    assert isinstance(client, FakeAnthropicClient)
    assert created == [{"model": "claude-sonnet-4-5", "api_key": "anthropic-key"}]


def test_bedrock_uses_maf_amazon_bedrock_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeBedrockChatClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(BedrockChatClient=FakeBedrockChatClient)
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.amazon", fake_module)

    cfg = ProviderConfig(
        provider_id="bedrock",
        display_name="Bedrock",
        inference_service="amazon_bedrock",
        endpoint="us-west-2",
        api_key="aws-access-key",
    )
    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="anthropic.claude-3-5-sonnet"))

    assert isinstance(client, FakeBedrockChatClient)
    assert created == [
        {
            "model": "anthropic.claude-3-5-sonnet",
            "region": "us-west-2",
            "access_key": "aws-access-key",
        }
    ]


def test_foundry_agent_uses_maf_foundry_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeFoundryAgent:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "agent_framework.foundry", types.SimpleNamespace(FoundryAgent=FakeFoundryAgent))
    cfg = ProviderConfig(
        provider_id="foundry",
        display_name="Foundry",
        inference_service="foundry_agent",
        endpoint="https://example.services.ai.azure.com/api/projects/project-a",
        chat_model_id="agent-name",
    )

    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="agent-id"))

    assert isinstance(client, FakeFoundryAgent)
    assert created == [
        {
            "project_endpoint": "https://example.services.ai.azure.com/api/projects/project-a",
            "agent_name": "agent-name",
        }
    ]


def test_github_copilot_uses_maf_github_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeGitHubCopilotAgent:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    fake_module = types.SimpleNamespace(GitHubCopilotAgent=FakeGitHubCopilotAgent)
    monkeypatch.setitem(__import__("sys").modules, "agent_framework.github", fake_module)

    cfg = ProviderConfig(
        provider_id="github",
        display_name="GitHub Copilot",
        inference_service="github_copilot",
    )
    client = build_chat_client(AgentClientSelection(provider=cfg, model_id="copilot"))

    assert isinstance(client, FakeGitHubCopilotAgent)
    assert created == [{}]
