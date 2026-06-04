from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from f8pystudio.agents.model_catalog import (
    _ollama_model_infos_from_payload,
    _openai_model_infos_from_payload,
    discover_endpoint_model_catalog,
    load_bundled_model_catalog,
)
from f8pystudio.agents.provider_endpoints import ollama_native_base_url, openai_compatible_api_base_url
from f8pystudio.agents.registry import ProviderConfig


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_load_bundled_model_catalog_uses_curated_defaults() -> None:
    result = load_bundled_model_catalog("openai")

    assert result.success
    assert "gpt-5" in [model.model_id for model in result.models]
    assert "gpt-4.1" in [model.model_id for model in result.models]


def test_load_bundled_model_catalog_reports_unsupported_custom_provider() -> None:
    result = load_bundled_model_catalog("custom_lab")

    assert not result.success
    assert result.status == "not_supported"
    assert "Add model IDs manually" in result.message


def test_openai_model_payload_parses_ids_and_deduplicates() -> None:
    parsed = _openai_model_infos_from_payload(
        {
            "data": [
                {"id": "model-a"},
                {"id": "model-a"},
                {"id": "qwen-vl-reasoning"},
                {"id": "gpt-image-2"},
                {"id": "text-embedding-3-large"},
                {"missing": "ignored"},
            ]
        }
    )

    models = parsed.agent_models
    assert [model.model_id for model in models] == ["model-a", "qwen-vl-reasoning"]
    assert models[1].capabilities.supports_vision
    assert models[1].capabilities.supports_reasoning
    assert parsed.skipped_non_agent_count == 2


def test_openai_model_payload_keeps_gpt5_and_skips_image_models() -> None:
    parsed = _openai_model_infos_from_payload(
        {
            "data": [
                {"id": "gpt-5"},
                {"id": "gpt-5-mini"},
                {"id": "gpt-image-2"},
                {"id": "dall-e-3"},
            ]
        }
    )

    assert [model.model_id for model in parsed.agent_models] == ["gpt-5", "gpt-5-mini"]
    assert parsed.agent_models[0].capabilities.supports_reasoning
    assert parsed.agent_models[0].capabilities.supports_agent_chat
    assert parsed.skipped_non_agent_count == 2


def test_ollama_model_payload_uses_name_then_model() -> None:
    parsed = _ollama_model_infos_from_payload(
        {
            "models": [
                {"name": "llama3.2:latest", "model": "ignored"},
                {"model": "qwen2.5-coder:latest"},
            ]
        }
    )

    assert [model.model_id for model in parsed.agent_models] == ["llama3.2:latest", "qwen2.5-coder:latest"]


def test_discover_openai_compatible_models_calls_v1_models() -> None:
    seen_urls: list[str] = []
    seen_authorization: list[str] = []

    def fake_urlopen(http_request: object, timeout: float) -> _FakeHttpResponse:
        seen_urls.append(http_request.full_url)
        seen_authorization.append(http_request.get_header("Authorization"))
        assert http_request.get_header("User-agent") == "F8PyStudio/0.4 API Client"
        assert timeout == 8.0
        return _FakeHttpResponse({"data": [{"id": "endpoint-model"}, {"id": "gpt-image-2"}]})

    provider = ProviderConfig(
        provider_id="custom_lab",
        display_name="Custom Lab",
        inference_service="custom_chat_client",
        api_key="sk-test",
        endpoint="https://example.test/v1/",
    )

    with patch("f8pystudio.agents.model_catalog.request.urlopen", fake_urlopen):
        result = discover_endpoint_model_catalog(provider)

    assert result.success
    assert seen_urls == ["https://example.test/v1/models"]
    assert seen_authorization == ["Bearer sk-test"]
    assert [model.model_id for model in result.models] == ["endpoint-model"]
    assert result.message == "Discovered 1 agent model(s). Skipped 1 non-agent model(s)."


def test_openai_endpoint_discovery_merges_bundled_agent_defaults() -> None:
    seen_urls: list[str] = []

    def fake_urlopen(http_request: object, timeout: float) -> _FakeHttpResponse:
        seen_urls.append(http_request.full_url)
        assert timeout == 8.0
        return _FakeHttpResponse({"data": [{"id": "gpt-4o"}, {"id": "gpt-image-2"}]})

    provider = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        inference_service="openai_responses",
        endpoint="https://api.openai.com/v1",
    )

    with patch("f8pystudio.agents.model_catalog.request.urlopen", fake_urlopen):
        result = discover_endpoint_model_catalog(provider)

    model_ids = [model.model_id for model in result.models]
    assert result.success
    assert seen_urls == ["https://api.openai.com/v1/models"]
    assert "gpt-4o" in model_ids
    assert "gpt-5" in model_ids
    assert "gpt-image-2" not in model_ids
    assert result.skipped_non_agent_count == 1
    assert result.message == f"Discovered {len(model_ids)} agent model(s). Skipped 1 non-agent model(s)."


def test_openai_compatible_api_base_url_adds_v1_for_root_endpoint() -> None:
    provider = ProviderConfig(
        provider_id="custom_lab",
        display_name="Custom Lab",
        inference_service="custom_chat_client",
        endpoint="https://example.test",
    )

    assert openai_compatible_api_base_url(provider) == "https://example.test/v1"


def test_discover_ollama_models_uses_native_tags_endpoint() -> None:
    seen_urls: list[str] = []

    def fake_urlopen(http_request: object, timeout: float) -> _FakeHttpResponse:
        seen_urls.append(http_request.full_url)
        assert timeout == 8.0
        return _FakeHttpResponse({"models": [{"name": "llama3.2:latest"}]})

    provider = ProviderConfig(
        provider_id="ollama",
        display_name="Ollama",
        inference_service="ollama_chat",
        endpoint="http://localhost:11434/v1",
    )

    with patch("f8pystudio.agents.model_catalog.request.urlopen", fake_urlopen):
        result = discover_endpoint_model_catalog(provider)

    assert result.success
    assert seen_urls == ["http://localhost:11434/api/tags"]
    assert [model.model_id for model in result.models] == ["llama3.2:latest"]


def test_ollama_native_base_url_strips_openai_compatible_v1_suffix() -> None:
    assert ollama_native_base_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert ollama_native_base_url("http://host:11434/custom/v1") == "http://host:11434/custom"
