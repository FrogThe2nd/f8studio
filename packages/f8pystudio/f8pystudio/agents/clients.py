from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .model_catalog import supports_agent_chat_model
from .provider_endpoints import ollama_native_base_url, openai_compatible_base_url
from .registry import ProviderConfig


class AgentFrameworkImportError(RuntimeError):
    pass


class ChatClientProtocol(Protocol):
    additional_properties: dict[str, Any]


@dataclass(frozen=True)
class AgentClientSelection:
    provider: ProviderConfig
    model_id: str
    reasoning_level: str = ""


def first_model_id(cfg: ProviderConfig) -> str:
    for model in cfg.cached_models:
        if supports_agent_chat_model(model):
            return model.model_id
    return ""


def effective_chat_model_id(cfg: ProviderConfig, selected_model_id: str) -> str:
    return str(selected_model_id or cfg.chat_model_id or first_model_id(cfg)).strip()


def effective_inline_model_id(cfg: ProviderConfig, selected_model_id: str) -> str:
    return str(selected_model_id or cfg.inline_model_id or first_model_id(cfg)).strip()


def build_chat_client(selection: AgentClientSelection) -> Any:
    cfg = selection.provider
    model_id = str(selection.model_id or "").strip()
    if not model_id:
        raise ValueError("No model selected")

    if cfg.inference_service in (
        "azure_openai_chat_completion",
        "azure_openai_responses",
        "openai_chat_completion",
        "openai_responses",
        "custom_chat_client",
    ):
        return _build_openai_compatible_client(cfg, model_id)
    if cfg.inference_service == "ollama_chat":
        return _build_ollama_client(cfg, model_id)
    if cfg.inference_service == "anthropic_claude":
        return _build_anthropic_client(cfg, model_id)
    if cfg.inference_service == "foundry_agent":
        return _build_foundry_agent(cfg)
    if cfg.inference_service == "github_copilot":
        return _build_github_copilot_agent()
    if cfg.inference_service == "amazon_bedrock":
        return _build_bedrock_chat_client(cfg, model_id)
    raise ValueError(f"Unsupported Agent Framework inference service: {cfg.inference_service}")


def _build_openai_compatible_client(cfg: ProviderConfig, model_id: str) -> Any:
    try:
        from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-openai is required for OpenAI-compatible Studio agents."
        ) from exc

    api_key = str(cfg.api_key or "")
    if cfg.inference_service in ("azure_openai_chat_completion", "azure_openai_responses"):
        if not cfg.endpoint.strip():
            raise ValueError("Azure OpenAI endpoint is required.")
        if cfg.inference_service == "azure_openai_responses":
            return OpenAIChatClient(
                model=model_id,
                api_key=api_key,
                azure_endpoint=cfg.endpoint.strip().rstrip("/"),
                api_version=str(cfg.api_version or "").strip() or None,
            )
        return OpenAIChatCompletionClient(
            model=model_id,
            api_key=api_key,
            azure_endpoint=cfg.endpoint.strip().rstrip("/"),
            api_version=str(cfg.api_version or "").strip() or None,
        )

    if cfg.inference_service == "openai_responses":
        return OpenAIChatClient(
            model=model_id,
            api_key=api_key,
            base_url=openai_compatible_base_url(cfg) or None,
        )
    return OpenAIChatCompletionClient(
        model=model_id,
        api_key=api_key,
        base_url=openai_compatible_base_url(cfg) or None,
    )


def _build_anthropic_client(cfg: ProviderConfig, model_id: str) -> Any:
    try:
        from agent_framework.anthropic import AnthropicClient
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-anthropic is required for Anthropic Studio agents."
        ) from exc

    return AnthropicClient(
        model=model_id,
        api_key=str(cfg.api_key or ""),
    )


def _build_bedrock_chat_client(cfg: ProviderConfig, model_id: str) -> Any:
    try:
        from agent_framework.amazon import BedrockChatClient
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-bedrock is required for Amazon Bedrock Studio agents."
        ) from exc

    return BedrockChatClient(
        model=model_id,
        region=str(cfg.endpoint or "").strip() or None,
        access_key=str(cfg.api_key or "").strip() or None,
    )


def _build_ollama_client(cfg: ProviderConfig, model_id: str) -> Any:
    try:
        from agent_framework.ollama import OllamaChatClient
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-ollama is required for Ollama Studio agents."
        ) from exc

    return OllamaChatClient(
        model=model_id,
        host=ollama_native_base_url(cfg.endpoint) or None,
    )


def _build_foundry_agent(cfg: ProviderConfig) -> Any:
    try:
        from agent_framework.foundry import FoundryAgent
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-foundry is required for Foundry Studio agents."
        ) from exc

    endpoint = str(cfg.endpoint or "").strip()
    if not endpoint:
        raise ValueError("Foundry project endpoint is required.")
    return FoundryAgent(project_endpoint=endpoint, agent_name=str(cfg.chat_model_id or "").strip() or None)


def _build_github_copilot_agent() -> Any:
    try:
        from agent_framework.github import GitHubCopilotAgent
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-github-copilot is required for GitHub Copilot Studio agents."
        ) from exc

    return GitHubCopilotAgent()
