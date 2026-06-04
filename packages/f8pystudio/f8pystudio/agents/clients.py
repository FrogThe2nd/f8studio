from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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
    if cfg.cached_models:
        return cfg.cached_models[0].model_id
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

    if cfg.protocol in ("openai", "custom", "ollama"):
        return _build_openai_compatible_client(cfg, model_id)
    if cfg.protocol == "anthropic":
        return _build_anthropic_client(cfg, model_id)
    raise ValueError(f"Unsupported provider protocol: {cfg.protocol}")


def _build_openai_compatible_client(cfg: ProviderConfig, model_id: str) -> Any:
    try:
        from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
    except ModuleNotFoundError as exc:
        raise AgentFrameworkImportError(
            "agent-framework-openai is required for OpenAI-compatible Studio agents."
        ) from exc

    endpoint = str(cfg.endpoint or "").strip().rstrip("/") or _default_endpoint(cfg.protocol)
    api_key = str(cfg.api_key or "")
    if cfg.api_mode == "responses":
        return OpenAIChatClient(
            model=model_id,
            api_key=api_key,
            base_url=endpoint or None,
        )
    return OpenAIChatCompletionClient(
        model=model_id,
        api_key=api_key,
        base_url=endpoint or None,
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


def _default_endpoint(protocol: str) -> str:
    if protocol == "ollama":
        return "http://localhost:11434/v1"
    if protocol == "openai":
        return "https://api.openai.com/v1"
    return ""
