"""Typed model catalog and provider-native discovery helpers.

This module deliberately does not import Agent Framework. Provider catalogs are
configuration-time metadata; agent execution remains in ``runtime.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Literal
from urllib import error, request

from .provider_http import api_request_headers, format_http_error
from .provider_endpoints import append_endpoint_path, ollama_native_base_url, openai_compatible_api_base_url
from .registry import DEFAULT_PROVIDERS, ModelCapabilities, ModelInfo, ModelKind, ProviderConfig

logger = logging.getLogger(__name__)

ModelCatalogStatus = Literal["ok", "not_supported", "error"]


@dataclass(frozen=True)
class ModelCatalogResult:
    status: ModelCatalogStatus
    models: tuple[ModelInfo, ...] = ()
    message: str = ""
    skipped_non_agent_count: int = 0

    @property
    def success(self) -> bool:
        return self.status == "ok"


def copy_model(model: ModelInfo) -> ModelInfo:
    return ModelInfo(
        model_id=model.model_id,
        display_name=model.display_name,
        capabilities=model.capabilities,
        health_status=model.health_status,
    )


def copy_provider(provider: ProviderConfig) -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider.provider_id,
        display_name=provider.display_name,
        inference_service=provider.inference_service,
        api_key=provider.api_key,
        endpoint=provider.endpoint,
        api_version=provider.api_version,
        cached_models=[copy_model(model) for model in provider.cached_models],
        inline_model_id=provider.inline_model_id,
        chat_model_id=provider.chat_model_id,
        reasoning_level=provider.reasoning_level,
    )


def default_provider_by_id(provider_id: str) -> ProviderConfig | None:
    for provider in DEFAULT_PROVIDERS:
        if provider.provider_id == provider_id:
            return copy_provider(provider)
    return None


def supports_endpoint_model_discovery(provider: ProviderConfig) -> bool:
    return provider.inference_service in (
        "azure_openai_chat_completion",
        "azure_openai_responses",
        "openai_chat_completion",
        "openai_responses",
        "ollama_chat",
        "custom_chat_client",
    )


def merge_model_lists(existing: list[ModelInfo], incoming: tuple[ModelInfo, ...] | list[ModelInfo]) -> list[ModelInfo]:
    result: list[ModelInfo] = [copy_model(model) for model in existing if model.model_id.strip()]
    known_ids = {model.model_id for model in result}
    for incoming_model in incoming:
        if incoming_model.model_id in known_ids:
            continue
        result.append(copy_model(incoming_model))
        known_ids.add(incoming_model.model_id)
    return result


def infer_model_capabilities(model_id: str) -> ModelCapabilities:
    lower_model_id = model_id.lower()
    model_kind = infer_model_kind(model_id)
    supports_agent_chat = model_kind == "agent"
    supports_reasoning = any(
        token in lower_model_id
        for token in ("o1", "o3", "o4", "gpt-5", "gpt5", "r1", "think", "reasoning", "deepseek-reasoner")
    )
    supports_vision = any(
        token in lower_model_id
        for token in (
            "vision",
            "vl",
            "omni",
            "gpt-4o",
            "claude-3.5",
            "claude-3-5",
            "gemini-1.5",
            "gemini-2.0",
            "gemini-2.5",
            "llava",
            "qwen-vl",
        )
    )
    return ModelCapabilities(
        model_kind=model_kind,
        supports_agent_chat=supports_agent_chat,
        supports_reasoning=supports_reasoning,
        supports_vision=supports_vision,
        max_context_tokens=128_000,
    )


def infer_model_kind(model_id: str) -> ModelKind:
    lower_model_id = model_id.lower()
    if _looks_like_image_model(lower_model_id):
        return "image"
    if _looks_like_embedding_model(lower_model_id):
        return "embedding"
    if _looks_like_audio_model(lower_model_id):
        return "audio"
    if _looks_like_realtime_model(lower_model_id):
        return "realtime"
    if _looks_like_moderation_model(lower_model_id):
        return "moderation"
    if _looks_like_video_model(lower_model_id):
        return "video"
    if _looks_like_tool_model(lower_model_id):
        return "tool"
    return "agent"


def supports_agent_chat_model(model: ModelInfo) -> bool:
    return model.capabilities.supports_agent_chat and model.capabilities.model_kind == "agent"


def load_bundled_model_catalog(provider_id: str) -> ModelCatalogResult:
    defaults = default_provider_by_id(provider_id)
    if defaults is None or not defaults.cached_models:
        return ModelCatalogResult(
            status="not_supported",
            message="No bundled model catalog is available for this provider. Add model IDs manually or discover from an endpoint.",
        )
    return ModelCatalogResult(
        status="ok",
        models=tuple(copy_model(model) for model in defaults.cached_models if supports_agent_chat_model(model)),
    )


def discover_endpoint_model_catalog(provider: ProviderConfig, *, timeout_s: float = 8.0) -> ModelCatalogResult:
    if not supports_endpoint_model_discovery(provider):
        return ModelCatalogResult(
            status="not_supported",
            message="Endpoint model discovery is not configured for this provider type. Add model IDs manually.",
        )

    try:
        if provider.inference_service == "ollama_chat":
            parsed_catalog = _discover_ollama_models(provider, timeout_s=timeout_s)
        else:
            parsed_catalog = _discover_openai_compatible_models(provider, timeout_s=timeout_s)
    except error.HTTPError as exc:
        return ModelCatalogResult(status="error", message=format_http_error(exc))
    except error.URLError as exc:
        return ModelCatalogResult(status="error", message=f"Connection error: {exc.reason}")
    except TimeoutError:
        return ModelCatalogResult(status="error", message="Timed out while discovering endpoint models.")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return ModelCatalogResult(status="error", message=f"{type(exc).__name__}: {exc}")

    agent_models = _merge_with_bundled_agent_defaults(provider.provider_id, parsed_catalog.agent_models)

    if not agent_models:
        if parsed_catalog.skipped_non_agent_count > 0:
            return ModelCatalogResult(
                status="error",
                message=(
                    f"Endpoint returned {parsed_catalog.skipped_non_agent_count} non-agent model(s), "
                    "but no chat/edit agent models."
                ),
                skipped_non_agent_count=parsed_catalog.skipped_non_agent_count,
            )
        return ModelCatalogResult(status="error", message="Endpoint returned no model IDs.")
    return ModelCatalogResult(
        status="ok",
        models=agent_models,
        message=_format_discovery_summary(len(agent_models), parsed_catalog.skipped_non_agent_count),
        skipped_non_agent_count=parsed_catalog.skipped_non_agent_count,
    )


@dataclass(frozen=True)
class _ParsedModelCatalog:
    agent_models: tuple[ModelInfo, ...]
    skipped_non_agent_count: int = 0


def _discover_openai_compatible_models(provider: ProviderConfig, *, timeout_s: float) -> _ParsedModelCatalog:
    headers = api_request_headers()
    api_key = str(provider.api_key or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = append_endpoint_path(openai_compatible_api_base_url(provider), "models")
    payload = _read_json_get(url, headers=headers, timeout_s=timeout_s)
    return _openai_model_infos_from_payload(payload)


def _discover_ollama_models(provider: ProviderConfig, *, timeout_s: float) -> _ParsedModelCatalog:
    url = append_endpoint_path(ollama_native_base_url(provider.endpoint), "api/tags")
    payload = _read_json_get(url, headers=api_request_headers(), timeout_s=timeout_s)
    return _ollama_model_infos_from_payload(payload)


def _read_json_get(url: str, *, headers: dict[str, str], timeout_s: float) -> Any:
    http_request = request.Request(url, headers=headers, method="GET")
    with request.urlopen(http_request, timeout=timeout_s) as response:
        raw_payload = response.read()
    return json.loads(raw_payload.decode("utf-8"))


def _openai_model_infos_from_payload(payload: Any) -> _ParsedModelCatalog:
    if not isinstance(payload, dict):
        raise ValueError("OpenAI-compatible model list response must be a JSON object.")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("OpenAI-compatible model list response must contain a data array.")

    models: list[ModelInfo] = []
    known_ids: set[str] = set()
    skipped_non_agent_count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if not isinstance(raw_id, str):
            continue
        model_id = raw_id.strip()
        if not model_id or model_id in known_ids:
            continue
        model = _model_info_from_id(model_id)
        if not supports_agent_chat_model(model):
            skipped_non_agent_count += 1
            known_ids.add(model_id)
            continue
        models.append(model)
        known_ids.add(model_id)
    return _ParsedModelCatalog(agent_models=tuple(models), skipped_non_agent_count=skipped_non_agent_count)


def _ollama_model_infos_from_payload(payload: Any) -> _ParsedModelCatalog:
    if not isinstance(payload, dict):
        raise ValueError("Ollama model list response must be a JSON object.")
    data = payload.get("models")
    if not isinstance(data, list):
        raise ValueError("Ollama model list response must contain a models array.")

    models: list[ModelInfo] = []
    known_ids: set[str] = set()
    skipped_non_agent_count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        raw_model = item.get("model")
        model_id = ""
        if isinstance(raw_name, str) and raw_name.strip():
            model_id = raw_name.strip()
        elif isinstance(raw_model, str) and raw_model.strip():
            model_id = raw_model.strip()
        if not model_id or model_id in known_ids:
            continue
        model = _model_info_from_id(model_id)
        if not supports_agent_chat_model(model):
            skipped_non_agent_count += 1
            known_ids.add(model_id)
            continue
        models.append(model)
        known_ids.add(model_id)
    return _ParsedModelCatalog(agent_models=tuple(models), skipped_non_agent_count=skipped_non_agent_count)


def _model_info_from_id(model_id: str) -> ModelInfo:
    return ModelInfo(
        model_id=model_id,
        display_name=model_id,
        capabilities=infer_model_capabilities(model_id),
        health_status="unknown",
    )


def _merge_with_bundled_agent_defaults(provider_id: str, discovered_models: tuple[ModelInfo, ...]) -> tuple[ModelInfo, ...]:
    defaults = default_provider_by_id(provider_id)
    if defaults is None or provider_id != "openai":
        return discovered_models

    merged = merge_model_lists(list(discovered_models), [
        model for model in defaults.cached_models if supports_agent_chat_model(model)
    ])
    return tuple(merged)


def _format_discovery_summary(agent_count: int, skipped_non_agent_count: int) -> str:
    if skipped_non_agent_count > 0:
        return f"Discovered {agent_count} agent model(s). Skipped {skipped_non_agent_count} non-agent model(s)."
    return f"Discovered {agent_count} agent model(s)."


def _looks_like_image_model(lower_model_id: str) -> bool:
    return (
        lower_model_id.startswith("gpt-image")
        or lower_model_id.startswith("dall-e")
        or lower_model_id.startswith("image-")
        or "image-generation" in lower_model_id
        or "stable-diffusion" in lower_model_id
    )


def _looks_like_embedding_model(lower_model_id: str) -> bool:
    return (
        lower_model_id.startswith("text-embedding")
        or lower_model_id.startswith("embedding")
        or lower_model_id.endswith("-embed")
        or "embedding" in lower_model_id
        or "embed-" in lower_model_id
    )


def _looks_like_audio_model(lower_model_id: str) -> bool:
    return (
        lower_model_id.startswith("tts-")
        or lower_model_id.startswith("whisper")
        or lower_model_id.startswith("gpt-4o-audio")
        or lower_model_id.startswith("gpt-4o-mini-audio")
        or "-audio" in lower_model_id
        or "transcribe" in lower_model_id
        or "speech" in lower_model_id
    )


def _looks_like_realtime_model(lower_model_id: str) -> bool:
    return "realtime" in lower_model_id


def _looks_like_moderation_model(lower_model_id: str) -> bool:
    return "moderation" in lower_model_id or lower_model_id.startswith("omni-moderation")


def _looks_like_video_model(lower_model_id: str) -> bool:
    return lower_model_id.startswith("sora") or "video" in lower_model_id


def _looks_like_tool_model(lower_model_id: str) -> bool:
    return lower_model_id.startswith("computer-use")
