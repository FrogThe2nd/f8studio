"""
Agent provider registry.

These dataclasses remain deliberately explicit so provider configuration is
searchable, refactorable, and friendly to static analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProviderProtocol = Literal["openai", "anthropic", "ollama", "custom"]
ProviderApiMode = Literal["chat_completions", "responses"]
ReasoningLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ModelCapabilities:
    supports_fim: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False
    reasoning_levels: tuple[ReasoningLevel, ...] = ()
    max_context_tokens: int = 128_000


@dataclass
class ModelInfo:
    model_id: str
    display_name: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    health_status: str = "unknown"

    @property
    def health_icon(self) -> str:
        status_map = {"ok": "🟢", "error": "🔴", "unknown": "⚪"}
        return status_map.get(self.health_status, "⚪")

    @property
    def display_name_with_icons(self) -> str:
        tags: list[str] = []
        if self.capabilities.supports_reasoning:
            tags.append("🧠")
        if self.capabilities.supports_vision:
            tags.append("👁️")

        ctx = self.capabilities.max_context_tokens
        if ctx > 0:
            tags.append(f"{ctx // 1000}k" if ctx >= 1000 else str(ctx))

        info = " ".join(tags)
        return f"{self.display_name} [{info}]" if info else self.display_name

    @property
    def full_display_label(self) -> str:
        return f"{self.health_icon} {self.display_name_with_icons}"


@dataclass
class ProviderConfig:
    provider_id: str
    display_name: str
    protocol: ProviderProtocol = "openai"
    api_mode: ProviderApiMode = "chat_completions"
    api_key: str = ""
    endpoint: str = ""
    cached_models: list[ModelInfo] = field(default_factory=list)
    inline_model_id: str = ""
    chat_model_id: str = ""
    reasoning_level: str = ""

    @property
    def health_icon(self) -> str:
        if not self.cached_models:
            return "⚪"
        if any(model.health_status == "ok" for model in self.cached_models):
            return "🟢"
        if all(model.health_status == "error" for model in self.cached_models):
            return "🔴"
        return "⚪"


def _openai_default() -> ProviderConfig:
    return ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        protocol="openai",
        api_mode="responses",
        endpoint="https://api.openai.com/v1",
        cached_models=[
            ModelInfo(
                model_id="gpt-4.1",
                display_name="GPT-4.1",
                capabilities=ModelCapabilities(max_context_tokens=1_047_576),
            ),
            ModelInfo(
                model_id="gpt-4o",
                display_name="GPT-4o",
                capabilities=ModelCapabilities(supports_vision=True, max_context_tokens=128_000),
            ),
            ModelInfo(
                model_id="o4-mini",
                display_name="o4-mini (reasoning)",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=200_000,
                ),
            ),
            ModelInfo(
                model_id="o3",
                display_name="o3 (reasoning)",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=200_000,
                ),
            ),
        ],
    )


def _anthropic_default() -> ProviderConfig:
    return ProviderConfig(
        provider_id="anthropic",
        display_name="Anthropic",
        protocol="anthropic",
        endpoint="https://api.anthropic.com",
        cached_models=[
            ModelInfo(
                model_id="claude-opus-4-5",
                display_name="Claude Opus 4.5",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=200_000,
                ),
            ),
            ModelInfo(
                model_id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=200_000,
                ),
            ),
            ModelInfo(
                model_id="claude-3-7-sonnet-20250219",
                display_name="Claude 3.7 Sonnet",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=200_000,
                ),
            ),
            ModelInfo(
                model_id="claude-3-5-haiku-20241022",
                display_name="Claude 3.5 Haiku",
                capabilities=ModelCapabilities(max_context_tokens=200_000),
            ),
        ],
    )


def _ollama_default() -> ProviderConfig:
    return ProviderConfig(
        provider_id="ollama",
        display_name="Ollama (local)",
        protocol="ollama",
        endpoint="http://localhost:11434/v1",
        cached_models=[],
    )


def _gemini_default() -> ProviderConfig:
    return ProviderConfig(
        provider_id="google_gemini",
        display_name="Google Gemini",
        protocol="openai",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        cached_models=[
            ModelInfo(
                model_id="gemini-2.5-pro-preview-03-25",
                display_name="Gemini 2.5 Pro",
                capabilities=ModelCapabilities(
                    supports_reasoning=True,
                    reasoning_levels=("low", "medium", "high"),
                    max_context_tokens=1_048_576,
                ),
            ),
            ModelInfo(
                model_id="gemini-2.0-flash",
                display_name="Gemini 2.0 Flash",
                capabilities=ModelCapabilities(max_context_tokens=1_048_576),
            ),
        ],
    )


DEFAULT_PROVIDERS: tuple[ProviderConfig, ...] = (
    _openai_default(),
    _anthropic_default(),
    _gemini_default(),
    _ollama_default(),
)
