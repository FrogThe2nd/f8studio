"""Unit tests for ai_assist.registry — serialization invariants and defaults."""
from __future__ import annotations

import pytest

from f8pystudio.ai_assist.registry import (
    DEFAULT_PROVIDERS,
    ModelCapabilities,
    ModelInfo,
    ProviderConfig,
    _anthropic_default,
    _gemini_default,
    _ollama_default,
    _openai_default,
)


class TestModelCapabilities:
    def test_defaults(self) -> None:
        caps = ModelCapabilities()
        assert caps.supports_fim is False
        assert caps.supports_reasoning is False
        assert caps.reasoning_levels == ()
        assert caps.max_context_tokens == 128_000

    def test_custom_values(self) -> None:
        caps = ModelCapabilities(
            supports_fim=True,
            supports_reasoning=True,
            reasoning_levels=("low", "high"),
            max_context_tokens=200_000,
        )
        assert caps.supports_fim is True
        assert caps.reasoning_levels == ("low", "high")
        assert caps.max_context_tokens == 200_000


class TestModelInfo:
    def test_defaults(self) -> None:
        m = ModelInfo(model_id="gpt-4o", display_name="GPT-4o")
        assert m.model_id == "gpt-4o"
        assert m.display_name == "GPT-4o"
        assert isinstance(m.capabilities, ModelCapabilities)
        assert m.health_status == "unknown"


class TestProviderConfig:
    def test_defaults(self) -> None:
        cfg = ProviderConfig(provider_id="test", display_name="Test")
        assert cfg.protocol == "openai"
        assert cfg.api_mode == "chat_completions"
        assert cfg.api_key == ""
        assert cfg.endpoint == ""
        assert cfg.cached_models == []
        assert cfg.inline_model_id == ""
        assert cfg.chat_model_id == ""
        assert cfg.reasoning_level == ""

    def test_mutable(self) -> None:
        cfg = ProviderConfig(provider_id="test", display_name="Test")
        cfg.api_key = "sk-abc"
        assert cfg.api_key == "sk-abc"
        cfg.cached_models.append(ModelInfo(model_id="m", display_name="M"))
        assert len(cfg.cached_models) == 1

    def test_reasoning_level_assignment(self) -> None:
        cfg = ProviderConfig(provider_id="test", display_name="Test", reasoning_level="high")
        assert cfg.reasoning_level == "high"
        cfg.reasoning_level = ""
        assert cfg.reasoning_level == ""


class TestDefaultProviders:
    def test_all_four_present(self) -> None:
        ids = {p.provider_id for p in DEFAULT_PROVIDERS}
        assert "openai" in ids
        assert "anthropic" in ids
        assert "google_gemini" in ids
        assert "ollama" in ids

    def test_openai_has_models(self) -> None:
        cfg = _openai_default()
        assert cfg.api_mode == "responses"
        assert len(cfg.cached_models) >= 2
        model_ids = [m.model_id for m in cfg.cached_models]
        assert "gpt-4o" in model_ids

    def test_anthropic_has_reasoning_model(self) -> None:
        cfg = _anthropic_default()
        reasoning_models = [m for m in cfg.cached_models if m.capabilities.supports_reasoning]
        assert len(reasoning_models) >= 1

    def test_gemini_has_large_context(self) -> None:
        cfg = _gemini_default()
        assert cfg.api_mode == "chat_completions"
        for m in cfg.cached_models:
            assert m.capabilities.max_context_tokens >= 1_000_000

    def test_ollama_no_models_by_default(self) -> None:
        cfg = _ollama_default()
        assert cfg.api_mode == "chat_completions"
        assert cfg.cached_models == []

    def test_protocols_valid(self) -> None:
        valid_protocols = {"openai", "anthropic", "ollama", "custom"}
        for p in DEFAULT_PROVIDERS:
            assert p.protocol in valid_protocols

    def test_non_openai_defaults_use_chat_completions(self) -> None:
        cfg = ProviderConfig(provider_id="custom", display_name="Custom", protocol="custom")
        assert cfg.api_mode == "chat_completions"
