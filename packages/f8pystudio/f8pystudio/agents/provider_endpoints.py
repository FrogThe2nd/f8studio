"""Provider endpoint helpers that are independent from Agent Framework."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from .registry import ProviderConfig, ProviderInferenceService


def provider_default_endpoint_for_service(service: ProviderInferenceService) -> str:
    if service in ("openai_chat_completion", "openai_responses"):
        return "https://api.openai.com/v1"
    if service in ("azure_openai_chat_completion", "azure_openai_responses"):
        return ""
    if service == "anthropic_claude":
        return "https://api.anthropic.com"
    if service == "ollama_chat":
        return "http://localhost:11434/v1"
    return ""


def openai_compatible_base_url(cfg: ProviderConfig) -> str:
    endpoint = str(cfg.endpoint or "").strip().rstrip("/")
    if endpoint:
        return endpoint
    return provider_default_endpoint_for_service(cfg.inference_service)


def openai_compatible_api_base_url(cfg: ProviderConfig) -> str:
    endpoint = openai_compatible_base_url(cfg)
    if not endpoint:
        return endpoint
    split = urlsplit(endpoint)
    path = split.path.rstrip("/")
    if path == "/v1" or path.endswith("/v1"):
        return endpoint.rstrip("/")
    api_path = f"{path}/v1" if path else "/v1"
    return urlunsplit((split.scheme, split.netloc, api_path, "", "")).rstrip("/")


def anthropic_base_url(cfg: ProviderConfig) -> str:
    endpoint = str(cfg.endpoint or "").strip().rstrip("/")
    if endpoint:
        return endpoint
    return provider_default_endpoint_for_service("anthropic_claude")


def append_endpoint_path(base_url: str, path: str) -> str:
    normalized_base = str(base_url or "").strip().rstrip("/")
    normalized_path = str(path or "").strip().lstrip("/")
    if not normalized_base:
        raise ValueError("Provider endpoint URL is required.")
    if not normalized_path:
        return normalized_base
    return f"{normalized_base}/{normalized_path}"


def ollama_native_base_url(endpoint: str) -> str:
    raw_endpoint = str(endpoint or "").strip() or "http://localhost:11434"
    split = urlsplit(raw_endpoint)
    path = split.path.rstrip("/")
    if path == "/v1":
        path = ""
    elif path.endswith("/v1"):
        path = path[:-3].rstrip("/")
    return urlunsplit((split.scheme, split.netloc, path, "", "")).rstrip("/")
