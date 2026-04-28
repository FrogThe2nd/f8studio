"""
AI provider registry — static dataclasses describing provider configs, model
capabilities and per-model parameters.  Deliberately zero magic: all fields
are explicit, typed, and accessible by name so IDE refactoring and mypy work
without issues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Protocol identifiers recognised by the HTTP client.
ProviderProtocol = Literal["openai", "anthropic", "ollama", "custom"]

# OpenAI-compatible providers can speak either the legacy Chat Completions
# wire shape or the newer Responses API wire shape.
ProviderApiMode = Literal["chat_completions", "responses"]

# All reasoning-level values that a model may advertise.
ReasoningLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ModelCapabilities:
    """Static capabilities of a single model as returned by the provider."""

    supports_fim: bool = False
    """True when the model can handle Fill-In-the-Middle completions."""

    supports_reasoning: bool = False
    """True when the model exposes a configurable reasoning/thinking level."""

    supports_vision: bool = False
    """True when the model supports image inputs."""

    reasoning_levels: tuple[ReasoningLevel, ...] = ()
    """Ordered set of reasoning levels, e.g. ('low', 'medium', 'high')."""

    max_context_tokens: int = 128_000
    """Maximum input + output token budget advertised by the model."""


@dataclass
class ModelInfo:
    """Metadata for a single model within a provider."""

    model_id: str
    """API-facing identifier, e.g. 'gpt-4o' or 'claude-3-7-sonnet-20250219'."""

    display_name: str
    """Human-readable label shown in dropdowns."""

    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    
    health_status: str = "unknown"
    """Connectivity status: 'unknown', 'ok', or 'error'."""

    @property
    def health_icon(self) -> str:
        status_map = {"ok": "🟢", "error": "🔴", "unknown": "⚪"}
        return status_map.get(self.health_status, "⚪")

    @property
    def display_name_with_icons(self) -> str:
        icons = []
        if self.capabilities.supports_reasoning:
            icons.append("🧠")
        if self.capabilities.supports_vision:
            icons.append("👁️")
        
        ctx = self.capabilities.max_context_tokens
        if ctx > 0:
            if ctx >= 1000:
                icons.append(f"{ctx//1000}k")
            else:
                icons.append(str(ctx))
                
        info = " ".join(icons)
        return f"{self.display_name} [{info}]" if info else self.display_name

    @property
    def full_display_label(self) -> str:
        """Label with health icon and capability icons, e.g. '🟢 GPT-4o [👁️ 128k]'."""
        return f"{self.health_icon} {self.display_name_with_icons}"


@dataclass
class ProviderConfig:
    """
    Mutable configuration for a single AI provider.

    Serialised to / deserialised from JSON by :class:`AiProviderStore`.
    """

    provider_id: str
    """Unique slug used as a stable key, e.g. 'openai', 'anthropic', 'my-ollama'."""

    display_name: str
    """Human-readable label, e.g. 'OpenAI'."""

    protocol: ProviderProtocol = "openai"
    """Wire protocol to use when talking to this provider."""

    api_mode: ProviderApiMode = "chat_completions"
    """OpenAI-compatible API shape to use for chat/edit/plan requests."""

    api_key: str = ""
    """Secret key — stored locally only, never logged."""

    endpoint: str = ""
    """
    Custom base URL.  Empty means the protocol's default endpoint is used:
      * openai   → https://api.openai.com/v1
      * anthropic → https://api.anthropic.com
      * ollama   → http://localhost:11434/v1
    """

    cached_models: list[ModelInfo] = field(default_factory=list)
    """Model list populated by 'Fetch Models' and cached across sessions."""

    # --- per-task model selection ---
    inline_model_id: str = ""
    """Model used for inline (FIM) suggestions."""

    chat_model_id: str = ""
    """Model used for Chat / Edit / Plan modes."""

    reasoning_level: str = ""
    """
    Active reasoning level for the selected chat model.
    Empty string means 'use model default / disabled'.
    """

    models_path: str = ""
    """Path to fetch models. Empty uses protocol default."""

    chat_path: str = ""
    """Path to chat completions. Empty uses protocol default."""

    @property
    def health_icon(self) -> str:
        if not self.cached_models:
            return "⚪"
        if any(m.health_status == "ok" for m in self.cached_models):
            return "🟢"
        if all(m.health_status == "error" for m in self.cached_models):
            return "🔴"
        return "⚪"


# ---------------------------------------------------------------------------
# Default provider presets shipped with the application
# ---------------------------------------------------------------------------

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
                capabilities=ModelCapabilities(max_context_tokens=128_000),
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
