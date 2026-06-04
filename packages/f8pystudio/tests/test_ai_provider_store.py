"""Unit tests for agents.store JSON round-trip, model cache, and defaults merge."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import time

import pytest
from qtpy import QtTest, QtWidgets  # type: ignore[import-not-found]

from f8pystudio.agents.registry import ModelCapabilities, ModelInfo, ProviderConfig
from f8pystudio.agents.store import (
    AiProviderStore,
    _capabilities_from_dict,
    _capabilities_to_dict,
    _model_from_dict,
    _model_to_dict,
    _provider_from_dict,
    _provider_to_dict,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _wait_until(predicate, *, timeout_ms: int = 3000) -> None:
    deadline = time.monotonic() + (float(timeout_ms) / 1000.0)
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()
    assert predicate()


# ---------------------------------------------------------------------------
# Codec helpers
# ---------------------------------------------------------------------------

class TestCapabilitiesCodec:
    def test_round_trip_defaults(self) -> None:
        caps = ModelCapabilities()
        d = _capabilities_to_dict(caps)
        restored = _capabilities_from_dict(d)
        assert restored.model_kind == "agent"
        assert restored.supports_agent_chat
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
        assert restored.model_kind == "agent"
        assert restored.supports_agent_chat
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

    def test_legacy_model_without_kind_reinfers_non_agent_capabilities(self) -> None:
        restored = _model_from_dict(
            {
                "model_id": "gpt-image-2",
                "display_name": "gpt-image-2",
                "capabilities": {},
            }
        )

        assert restored.capabilities.model_kind == "image"
        assert not restored.capabilities.supports_agent_chat


class TestProviderCodec:
    def test_round_trip_minimal(self) -> None:
        cfg = ProviderConfig(provider_id="test", display_name="Test")
        encoded = _provider_to_dict(cfg)
        restored = _provider_from_dict(encoded)
        assert restored.provider_id == "test"
        assert restored.display_name == "Test"
        assert restored.inference_service == "openai_chat_completion"
        assert restored.api_key == ""
        assert "protocol" not in encoded
        assert "api_mode" not in encoded

    def test_round_trip_full(self) -> None:
        cfg = ProviderConfig(
            provider_id="anthropic",
            display_name="Anthropic",
            inference_service="anthropic_claude",
            api_key="sk-xyz",
            endpoint="https://api.anthropic.com",
            api_version="",
            cached_models=[ModelInfo(model_id="claude-3-7", display_name="Claude 3.7")],
            inline_model_id="claude-3-7",
            chat_model_id="claude-3-7",
            reasoning_level="high",
        )
        restored = _provider_from_dict(_provider_to_dict(cfg))
        assert restored.inference_service == "anthropic_claude"
        assert restored.api_key == "sk-xyz"
        assert len(restored.cached_models) == 1
        assert restored.cached_models[0].model_id == "claude-3-7"
        assert restored.reasoning_level == "high"

    def test_round_trip_responses_api_mode(self) -> None:
        cfg = ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            inference_service="openai_responses",
            endpoint="https://api.openai.com/v1",
        )
        restored = _provider_from_dict(_provider_to_dict(cfg))
        assert restored.inference_service == "openai_responses"

    def test_legacy_provider_config_infers_inference_service(self) -> None:
        restored = _provider_from_dict({
            "provider_id": "openai",
            "display_name": "OpenAI",
            "protocol": "openai",
            "api_mode": "responses",
            "endpoint": "https://api.openai.com/v1",
        })

        assert restored.inference_service == "openai_responses"

    def test_legacy_ollama_inference_service_name_migrates_to_ollama_chat(self) -> None:
        restored = _provider_from_dict({
            "provider_id": "ollama",
            "display_name": "Ollama",
            "inference_service": "ollama_openai_compatible",
            "endpoint": "http://localhost:11434/v1",
        })

        assert restored.inference_service == "ollama_chat"

    def test_invalid_protocol_defaults_to_openai(self) -> None:
        restored = _provider_from_dict({
            "provider_id": "x",
            "display_name": "X",
            "protocol": "unsupported_thing",
            "api_mode": "chat_completions",
        })
        assert restored.inference_service == "openai_chat_completion"

    def test_provider_config_without_api_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_mode"):
            _provider_from_dict({
                "provider_id": "openai",
                "display_name": "OpenAI",
                "protocol": "openai",
                "endpoint": "https://api.openai.com/v1",
            })

    def test_provider_config_with_invalid_api_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_mode"):
            _provider_from_dict({
                "provider_id": "openai",
                "display_name": "OpenAI",
                "protocol": "openai",
                "api_mode": "legacy_endpoint_guess",
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
        new_cfg = ProviderConfig(
            provider_id="my_ollama",
            display_name="My Ollama",
            inference_service="ollama_chat",
        )
        store.save_provider(new_cfg)

        found = store.provider_by_id("my_ollama")
        assert found is not None
        assert found.inference_service == "ollama_chat"

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

    def test_fetch_models_uses_endpoint_discovery(self, tmp_path: Path) -> None:
        _ensure_app()
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="custom_lab",
                display_name="Custom Lab",
                inference_service="custom_chat_client",
                endpoint="https://example.test/v1",
            )
        )

        fetched: list[tuple[str, bool, str]] = []
        store.models_fetched.connect(lambda pid, success, error: fetched.append((pid, success, error)))

        with patch("f8pystudio.agents.store.discover_endpoint_model_catalog") as discover:
            from f8pystudio.agents.model_catalog import ModelCatalogResult

            discover.return_value = ModelCatalogResult(
                status="ok",
                models=(ModelInfo(model_id="endpoint-model", display_name="Endpoint Model"),),
            )
            store.fetch_models_async("custom_lab")
            _wait_until(lambda: len(fetched) == 1)

        updated = store.provider_by_id("custom_lab")
        assert updated is not None
        assert [model.model_id for model in updated.cached_models] == ["endpoint-model"]
        assert fetched == [("custom_lab", True, "Discovered 1 agent model(s).")]

    def test_discover_endpoint_models_merges_provider_native_catalog(self, tmp_path: Path) -> None:
        _ensure_app()
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="custom_lab",
                display_name="Custom Lab",
                inference_service="custom_chat_client",
                endpoint="https://example.test/v1",
            )
        )
        fetched: list[tuple[str, bool, str]] = []
        store.models_fetched.connect(lambda pid, success, error: fetched.append((pid, success, error)))

        with patch("f8pystudio.agents.store.discover_endpoint_model_catalog") as discover:
            from f8pystudio.agents.model_catalog import ModelCatalogResult

            discover.return_value = ModelCatalogResult(
                status="ok",
                models=(ModelInfo(model_id="endpoint-model", display_name="Endpoint Model"),),
            )
            store.discover_endpoint_models_async("custom_lab")
            _wait_until(lambda: len(fetched) == 1)

        updated = store.provider_by_id("custom_lab")
        assert updated is not None
        assert [model.model_id for model in updated.cached_models] == ["endpoint-model"]
        assert fetched == [("custom_lab", True, "Discovered 1 agent model(s).")]

    def test_add_cached_model_sets_defaults_and_capabilities(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="local",
                display_name="Local",
                inference_service="custom_chat_client",
            )
        )

        assert store.add_cached_model("local", "qwen-vl-reasoning")

        updated = store.provider_by_id("local")
        assert updated is not None
        assert len(updated.cached_models) == 1
        assert updated.cached_models[0].display_name == "qwen-vl-reasoning"
        assert updated.cached_models[0].capabilities.supports_vision
        assert updated.cached_models[0].capabilities.supports_reasoning
        assert updated.inline_model_id == "qwen-vl-reasoning"
        assert updated.chat_model_id == "qwen-vl-reasoning"

    def test_add_cached_non_agent_model_does_not_set_defaults(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="local",
                display_name="Local",
                inference_service="custom_chat_client",
            )
        )

        assert store.add_cached_model("local", "gpt-image-2")

        updated = store.provider_by_id("local")
        assert updated is not None
        assert updated.cached_models[0].capabilities.model_kind == "image"
        assert not updated.cached_models[0].capabilities.supports_agent_chat
        assert updated.inline_model_id == ""
        assert updated.chat_model_id == ""

    def test_add_cached_model_updates_duplicate_display_name(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="local",
                display_name="Local",
                inference_service="custom_chat_client",
            )
        )

        assert store.add_cached_model("local", "local-model")
        assert store.add_cached_model("local", "local-model", "Local Model")

        updated = store.provider_by_id("local")
        assert updated is not None
        assert len(updated.cached_models) == 1
        assert updated.cached_models[0].display_name == "Local Model"

    def test_remove_cached_models_clears_selected_defaults(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        cfg = ProviderConfig(
            provider_id="local",
            display_name="Local",
            inference_service="custom_chat_client",
            cached_models=[
                ModelInfo(model_id="a", display_name="A"),
                ModelInfo(model_id="b", display_name="B"),
            ],
            inline_model_id="a",
            chat_model_id="b",
        )
        store.save_provider(cfg)

        removed_count = store.remove_cached_models("local", ["a", "b"])

        updated = store.provider_by_id("local")
        assert updated is not None
        assert removed_count == 2
        assert updated.cached_models == []
        assert updated.inline_model_id == ""
        assert updated.chat_model_id == ""

    def test_test_models_async_routes_through_provider_connectivity_boundary(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="runner",
                display_name="Runner",
                cached_models=[
                    ModelInfo(model_id="first", display_name="First"),
                    ModelInfo(model_id="second", display_name="Second"),
                ],
            )
        )

        with patch.object(AiProviderStore, "_test_model_with_provider") as recorder:
            store.test_models_async("runner", ["first"])

        assert recorder.call_count == 1
        assert recorder.call_args.kwargs["provider"].provider_id == "runner"
        assert recorder.call_args.kwargs["model_id"] == "first"

    def test_test_models_async_ignores_non_agent_models(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="runner",
                display_name="Runner",
                cached_models=[
                    ModelInfo(
                        model_id="gpt-image-2",
                        display_name="gpt-image-2",
                        capabilities=ModelCapabilities(model_kind="image", supports_agent_chat=False),
                    ),
                ],
            )
        )

        with patch.object(AiProviderStore, "_test_model_with_provider") as recorder:
            started = store.test_models_async("runner", ["gpt-image-2"])

        assert not started
        assert recorder.call_count == 0

    def test_record_model_test_result_updates_health_and_emits(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_provider(
            ProviderConfig(
                provider_id="runner",
                display_name="Runner",
                cached_models=[ModelInfo(model_id="first", display_name="First")],
            )
        )
        tested: list[tuple[str, str, bool, str]] = []
        store.model_tested.connect(
            lambda pid, model_id, success, error: tested.append((pid, model_id, success, error))
        )

        store._record_model_test_result("runner", "first", True, "")

        updated = store.provider_by_id("runner")
        assert updated is not None
        assert updated.cached_models[0].health_status == "ok"
        assert tested == [("runner", "first", True, "")]
