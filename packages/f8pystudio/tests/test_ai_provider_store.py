"""Unit tests for ai_assist.store — JSON round-trip, model cache, and defaults merge."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from f8pystudio.ai_assist.registry import ModelCapabilities, ModelInfo, ProviderConfig
from f8pystudio.ai_assist.store import (
    AiProviderStore,
    _capabilities_from_dict,
    _capabilities_to_dict,
    _model_from_dict,
    _model_to_dict,
    _models_url,
    _provider_from_dict,
    _provider_to_dict,
)


# ---------------------------------------------------------------------------
# Codec helpers
# ---------------------------------------------------------------------------

class TestCapabilitiesCodec:
    def test_round_trip_defaults(self) -> None:
        caps = ModelCapabilities()
        d = _capabilities_to_dict(caps)
        restored = _capabilities_from_dict(d)
        assert restored.supports_fim == caps.supports_fim
        assert restored.supports_reasoning == caps.supports_reasoning
        assert restored.reasoning_levels == caps.reasoning_levels
        assert restored.max_context_tokens == caps.max_context_tokens

    def test_round_trip_full(self) -> None:
        caps = ModelCapabilities(
            supports_fim=True,
            supports_reasoning=True,
            reasoning_levels=("low", "medium", "high"),
            max_context_tokens=200_000,
        )
        d = _capabilities_to_dict(caps)
        restored = _capabilities_from_dict(d)
        assert restored == caps

    def test_missing_keys_use_defaults(self) -> None:
        restored = _capabilities_from_dict({})
        assert restored.supports_fim is False
        assert restored.max_context_tokens == 128_000


class TestModelCodec:
    def test_round_trip(self) -> None:
        m = ModelInfo(
            model_id="gpt-4o",
            display_name="GPT-4o",
            capabilities=ModelCapabilities(max_context_tokens=128_000),
        )
        restored = _model_from_dict(_model_to_dict(m))
        assert restored.model_id == m.model_id
        assert restored.display_name == m.display_name
        assert restored.capabilities.max_context_tokens == 128_000


class TestProviderCodec:
    def test_round_trip_minimal(self) -> None:
        cfg = ProviderConfig(provider_id="test", display_name="Test")
        restored = _provider_from_dict(_provider_to_dict(cfg))
        assert restored.provider_id == "test"
        assert restored.display_name == "Test"
        assert restored.protocol == "openai"
        assert restored.api_mode == "chat_completions"
        assert restored.api_key == ""

    def test_round_trip_full(self) -> None:
        cfg = ProviderConfig(
            provider_id="anthropic",
            display_name="Anthropic",
            protocol="anthropic",
            api_mode="chat_completions",
            api_key="sk-xyz",
            endpoint="https://api.anthropic.com",
            cached_models=[ModelInfo(model_id="claude-3-7", display_name="Claude 3.7")],
            inline_model_id="claude-3-7",
            chat_model_id="claude-3-7",
            reasoning_level="high",
        )
        restored = _provider_from_dict(_provider_to_dict(cfg))
        assert restored.protocol == "anthropic"
        assert restored.api_mode == "chat_completions"
        assert restored.api_key == "sk-xyz"
        assert len(restored.cached_models) == 1
        assert restored.cached_models[0].model_id == "claude-3-7"
        assert restored.reasoning_level == "high"

    def test_round_trip_responses_api_mode(self) -> None:
        cfg = ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            protocol="openai",
            api_mode="responses",
            endpoint="https://api.openai.com/v1",
        )
        restored = _provider_from_dict(_provider_to_dict(cfg))
        assert restored.api_mode == "responses"

    def test_invalid_protocol_defaults_to_openai(self) -> None:
        d = _provider_to_dict(ProviderConfig(provider_id="x", display_name="X"))
        d["protocol"] = "unsupported_thing"
        restored = _provider_from_dict(d)
        assert restored.protocol == "openai"

    def test_provider_config_without_api_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_mode"):
            _provider_from_dict({
                "provider_id": "openai",
                "display_name": "OpenAI",
                "protocol": "openai",
                "endpoint": "https://api.openai.com/v1",
                "chat_path": "/chat/completions",
            })

    def test_provider_config_with_invalid_api_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_mode"):
            _provider_from_dict({
                "provider_id": "openai",
                "display_name": "OpenAI",
                "protocol": "openai",
                "api_mode": "legacy_chat_path_guess",
                "endpoint": "https://api.openai.com/v1",
            })


# ---------------------------------------------------------------------------
# Store persistence
# ---------------------------------------------------------------------------

class TestAiProviderStore:
    pytest.importorskip("qtpy")

    def _make_store(self, tmp_path: Path) -> AiProviderStore:
        store_path = tmp_path / "ai_providers.json"
        with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
            store = AiProviderStore()
        return store

    def test_defaults_loaded_on_first_run(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        providers = store.providers()
        ids = {p.provider_id for p in providers}
        assert "openai" in ids
        assert "anthropic" in ids

    def test_save_and_reload(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        cfg = store.provider_by_id("openai")
        assert cfg is not None
        cfg.api_key = "sk-test-123"
        store.save_provider(cfg)

        # Reload from disk
        store2 = self._make_store(tmp_path)
        reloaded = store2.provider_by_id("openai")
        assert reloaded is not None
        assert reloaded.api_key == "sk-test-123"

    def test_save_new_provider(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        new_cfg = ProviderConfig(provider_id="my_ollama", display_name="My Ollama", protocol="ollama")
        store.save_provider(new_cfg)

        found = store.provider_by_id("my_ollama")
        assert found is not None
        assert found.protocol == "ollama"

    def test_delete_provider(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        new_cfg = ProviderConfig(provider_id="to_delete", display_name="To Delete")
        store.save_provider(new_cfg)
        assert store.provider_by_id("to_delete") is not None

        store.delete_provider("to_delete")
        assert store.provider_by_id("to_delete") is None

    def test_providers_changed_emitted_on_save(self, tmp_path: Path) -> None:
        from qtpy.QtWidgets import QApplication
        import sys
        app = QApplication.instance() or QApplication(sys.argv)

        store = self._make_store(tmp_path)
        changed_count = [0]
        store.providers_changed.connect(lambda: changed_count.__setitem__(0, changed_count[0] + 1))
        new_cfg = ProviderConfig(provider_id="sig_test", display_name="Sig Test")
        store.save_provider(new_cfg)
        assert changed_count[0] == 1

    def test_parse_openai_models_response(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(ProviderConfig(provider_id="test_parse", display_name="Test"))
        raw = json.dumps({
            "data": [
                {"id": "gpt-4o", "object": "model"},
                {"id": "gpt-4-turbo", "object": "model"},
            ]
        })
        models = store._parse_models_response("test_parse", raw)
        ids = [m.model_id for m in models]
        assert "gpt-4o" in ids
        assert "gpt-4-turbo" in ids

    def test_parse_anthropic_models_response(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(ProviderConfig(provider_id="ant", display_name="Ant", protocol="anthropic"))
        raw = json.dumps({
            "models": [
                {"id": "claude-3-7-sonnet"},
                {"id": "claude-3-5-haiku"},
            ]
        })
        models = store._parse_models_response("ant", raw)
        assert len(models) == 2

    def test_parse_invalid_json_raises(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="Invalid JSON"):
            store._parse_models_response("openai", "not-json{{{")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestModelsUrl:
    def test_openai_default(self) -> None:
        cfg = ProviderConfig(provider_id="openai", display_name="OpenAI", protocol="openai")
        url = _models_url(cfg)
        assert "api.openai.com" in url
        assert "/models" in url

    def test_anthropic_default(self) -> None:
        cfg = ProviderConfig(provider_id="anthropic", display_name="Anthropic", protocol="anthropic")
        url = _models_url(cfg)
        assert "api.anthropic.com" in url

    def test_custom_endpoint(self) -> None:
        cfg = ProviderConfig(
            provider_id="custom", display_name="Custom", protocol="openai",
            endpoint="http://localhost:8080/v1"
        )
        url = _models_url(cfg)
        assert url == "http://localhost:8080/v1/models"


class TestModelPing:
    pytest.importorskip("qtpy")

    def test_responses_ping_payload(self, tmp_path: Path) -> None:
        store_path = tmp_path / "ai_providers.json"
        with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
            store = AiProviderStore()
        cfg = ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            protocol="openai",
            api_mode="responses",
            endpoint="https://api.openai.com/v1",
        )
        assert store._test_chat_url(cfg) == "https://api.openai.com/v1/responses"
        assert store._build_ping_payload(cfg, "gpt-4.1") == {
            "model": "gpt-4.1",
            "input": [{"role": "user", "content": "ping"}],
            "max_output_tokens": 1,
            "store": False,
        }

    def test_chat_completions_ping_payload_stays_unchanged(self, tmp_path: Path) -> None:
        store_path = tmp_path / "ai_providers.json"
        with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
            store = AiProviderStore()
        cfg = ProviderConfig(provider_id="custom", display_name="Custom", protocol="openai")
        assert store._test_chat_url(cfg) == "https://api.openai.com/v1/chat/completions"
        assert store._build_ping_payload(cfg, "gpt-4o") == {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }

    def test_chat_path_with_v1_prefix_does_not_duplicate_default_base(self, tmp_path: Path) -> None:
        store_path = tmp_path / "ai_providers.json"
        with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
            store = AiProviderStore()
        cfg = ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            protocol="openai",
            api_mode="responses",
            endpoint="https://api.openai.com/v1",
            chat_path="/v1/responses",
        )
        assert store._test_chat_url(cfg) == "https://api.openai.com/v1/responses"
