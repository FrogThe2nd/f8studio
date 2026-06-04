"""
Persistent storage for Studio agent provider configurations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from qtpy import QtCore  # type: ignore[import-not-found]

from .registry import (
    DEFAULT_PROVIDERS,
    ModelCapabilities,
    ModelInfo,
    ProviderApiMode,
    ProviderConfig,
    ProviderProtocol,
)

logger = logging.getLogger(__name__)


def _copy_model(model: ModelInfo) -> ModelInfo:
    return ModelInfo(
        model_id=model.model_id,
        display_name=model.display_name,
        capabilities=model.capabilities,
        health_status=model.health_status,
    )


def _copy_provider(provider: ProviderConfig) -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider.provider_id,
        display_name=provider.display_name,
        protocol=provider.protocol,
        api_mode=provider.api_mode,
        api_key=provider.api_key,
        endpoint=provider.endpoint,
        cached_models=[_copy_model(model) for model in provider.cached_models],
        inline_model_id=provider.inline_model_id,
        chat_model_id=provider.chat_model_id,
        reasoning_level=provider.reasoning_level,
    )


def _default_provider_by_id(provider_id: str) -> ProviderConfig | None:
    for provider in DEFAULT_PROVIDERS:
        if provider.provider_id == provider_id:
            return _copy_provider(provider)
    return None


def _merge_model_lists(existing: list[ModelInfo], defaults: list[ModelInfo]) -> list[ModelInfo]:
    result: list[ModelInfo] = [_copy_model(model) for model in existing if model.model_id.strip()]
    known_ids = {model.model_id for model in result}
    for default_model in defaults:
        if default_model.model_id in known_ids:
            continue
        result.append(_copy_model(default_model))
        known_ids.add(default_model.model_id)
    return result


def _model_capabilities_from_model_id(model_id: str) -> ModelCapabilities:
    lower_model_id = model_id.lower()
    supports_reasoning = any(
        token in lower_model_id for token in ("o1", "o3", "o4", "r1", "think", "reasoning", "deepseek-reasoner")
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
        supports_reasoning=supports_reasoning,
        supports_vision=supports_vision,
        max_context_tokens=128_000,
    )


def _capabilities_to_dict(caps: ModelCapabilities) -> dict[str, Any]:
    return {
        "supports_fim": caps.supports_fim,
        "supports_reasoning": caps.supports_reasoning,
        "supports_vision": caps.supports_vision,
        "reasoning_levels": list(caps.reasoning_levels),
        "max_context_tokens": caps.max_context_tokens,
    }


def _capabilities_from_dict(payload: dict[str, Any]) -> ModelCapabilities:
    return ModelCapabilities(
        supports_fim=bool(payload.get("supports_fim", False)),
        supports_reasoning=bool(payload.get("supports_reasoning", False)),
        supports_vision=bool(payload.get("supports_vision", False)),
        reasoning_levels=tuple(str(level) for level in payload.get("reasoning_levels", [])),  # type: ignore[arg-type]
        max_context_tokens=int(payload.get("max_context_tokens", 128_000)),
    )


def _model_to_dict(model: ModelInfo) -> dict[str, Any]:
    return {
        "model_id": model.model_id,
        "display_name": model.display_name,
        "capabilities": _capabilities_to_dict(model.capabilities),
        "health_status": model.health_status,
    }


def _model_from_dict(payload: dict[str, Any]) -> ModelInfo:
    return ModelInfo(
        model_id=str(payload.get("model_id", "")),
        display_name=str(payload.get("display_name", payload.get("model_id", ""))),
        capabilities=_capabilities_from_dict(dict(payload.get("capabilities", {}))),
        health_status=str(payload.get("health_status", "unknown")),
    )


def _provider_to_dict(provider: ProviderConfig) -> dict[str, Any]:
    return {
        "provider_id": provider.provider_id,
        "display_name": provider.display_name,
        "protocol": provider.protocol,
        "api_mode": provider.api_mode,
        "api_key": provider.api_key,
        "endpoint": provider.endpoint,
        "cached_models": [_model_to_dict(model) for model in provider.cached_models],
        "inline_model_id": provider.inline_model_id,
        "chat_model_id": provider.chat_model_id,
        "reasoning_level": provider.reasoning_level,
    }


def _provider_from_dict(payload: dict[str, Any]) -> ProviderConfig:
    protocol_raw = str(payload.get("protocol", "openai"))
    if protocol_raw not in ("openai", "anthropic", "ollama", "custom"):
        protocol_raw = "openai"
    protocol = cast(ProviderProtocol, protocol_raw)

    api_mode_raw = str(payload.get("api_mode", "")).strip()
    if api_mode_raw not in ("chat_completions", "responses"):
        raise ValueError("AI provider config is missing valid api_mode.")
    api_mode = cast(ProviderApiMode, api_mode_raw)

    return ProviderConfig(
        provider_id=str(payload["provider_id"]),
        display_name=str(payload.get("display_name", payload["provider_id"])),
        protocol=protocol,
        api_mode=api_mode,
        api_key=str(payload.get("api_key", "")),
        endpoint=str(payload.get("endpoint", "")),
        cached_models=[_model_from_dict(dict(model)) for model in payload.get("cached_models", [])],
        inline_model_id=str(payload.get("inline_model_id", "")),
        chat_model_id=str(payload.get("chat_model_id", "")),
        reasoning_level=str(payload.get("reasoning_level", "")),
    )


class AiProviderStore(QtCore.QObject):
    providers_changed = QtCore.Signal()
    models_fetched = QtCore.Signal(str, bool, str)
    model_tested = QtCore.Signal(str, str, bool, str)
    _model_test_finished = QtCore.Signal(str, str, bool, str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._providers: list[ProviderConfig] = []
        self.active_inline_provider: str = ""
        self.active_chat_provider: str = ""
        self._storage_path = self._resolve_storage_path()
        self._model_test_finished.connect(self._record_model_test_result)  # type: ignore[attr-defined]
        self._load()

    def providers(self) -> list[ProviderConfig]:
        return list(self._providers)

    def provider_by_id(self, provider_id: str) -> ProviderConfig | None:
        for provider in self._providers:
            if provider.provider_id == provider_id:
                return provider
        return None

    def save_provider(self, cfg: ProviderConfig, emit: bool = True) -> None:
        for index, existing in enumerate(self._providers):
            if existing.provider_id == cfg.provider_id:
                self._providers[index] = cfg
                self._persist()
                if emit:
                    self.providers_changed.emit()
                return
        self._providers.append(cfg)
        self._persist()
        if emit:
            self.providers_changed.emit()

    def save_active_providers(self, inline_id: str, chat_id: str) -> None:
        self.active_inline_provider = str(inline_id or "")
        self.active_chat_provider = str(chat_id or "")
        self._persist()

    def delete_provider(self, provider_id: str) -> None:
        before = len(self._providers)
        self._providers = [provider for provider in self._providers if provider.provider_id != provider_id]
        if len(self._providers) != before:
            self._persist()
            self.providers_changed.emit()

    def add_cached_model(self, provider_id: str, model_id: str, display_name: str = "") -> bool:
        cfg = self.provider_by_id(provider_id)
        normalized_model_id = str(model_id or "").strip()
        if cfg is None or not normalized_model_id:
            return False

        normalized_display_name = str(display_name or "").strip() or normalized_model_id
        for model in cfg.cached_models:
            if model.model_id == normalized_model_id:
                model.display_name = normalized_display_name
                self.save_provider(cfg)
                return True

        cfg.cached_models.append(
            ModelInfo(
                model_id=normalized_model_id,
                display_name=normalized_display_name,
                capabilities=_model_capabilities_from_model_id(normalized_model_id),
                health_status="unknown",
            )
        )
        if not cfg.inline_model_id:
            cfg.inline_model_id = normalized_model_id
        if not cfg.chat_model_id:
            cfg.chat_model_id = normalized_model_id
        self.save_provider(cfg)
        return True

    def remove_cached_models(self, provider_id: str, model_ids: list[str]) -> int:
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            return 0
        ids_to_remove = {str(model_id or "").strip() for model_id in model_ids if str(model_id or "").strip()}
        if not ids_to_remove:
            return 0
        before = len(cfg.cached_models)
        cfg.cached_models = [model for model in cfg.cached_models if model.model_id not in ids_to_remove]
        removed_count = before - len(cfg.cached_models)
        if removed_count <= 0:
            return 0
        if cfg.inline_model_id in ids_to_remove:
            cfg.inline_model_id = ""
        if cfg.chat_model_id in ids_to_remove:
            cfg.chat_model_id = ""
        self.save_provider(cfg)
        return removed_count

    def fetch_models_async(self, provider_id: str) -> None:
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            logger.warning("fetch_models_async: unknown provider_id=%r", provider_id)
            self.models_fetched.emit(provider_id, False, "Provider not found")
            return
        defaults = _default_provider_by_id(cfg.provider_id)
        if defaults is None:
            self.models_fetched.emit(
                provider_id,
                False,
                "Automatic model discovery is not available through Agent Framework; add model IDs manually.",
            )
            return
        cfg.cached_models = _merge_model_lists(cfg.cached_models, defaults.cached_models)
        self.save_provider(cfg, emit=False)
        self.models_fetched.emit(provider_id, True, "")

    def test_models_async(self, provider_id: str, model_ids: list[str] | None = None) -> None:
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            return
        to_test = [model for model in cfg.cached_models if model_ids is None or model.model_id in model_ids]
        if not to_test:
            return

        for model in to_test:
            self._test_model_with_agent_framework(provider_id=provider_id, model_id=model.model_id)

    def _test_model_with_agent_framework(self, *, provider_id: str, model_id: str) -> None:
        import asyncio
        import threading

        def _worker() -> None:
            error = ""
            success = False
            try:
                from .runtime import StudioAgentRequest, StudioAgentRuntime

                runtime = StudioAgentRuntime(self)
                result = asyncio.run(
                    runtime.run_text(
                        StudioAgentRequest(
                            request_id=f"provider-test-{provider_id}-{model_id}",
                            mode="chat",
                            messages=({"role": "user", "content": "Reply with only: ok"},),
                            chat_provider_id=provider_id,
                            chat_model_id=model_id,
                        )
                    )
                )
                success = bool(str(result or "").strip())
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("Agent Framework provider test failed provider=%s model=%s", provider_id, model_id)
            self._model_test_finished.emit(provider_id, model_id, success, error)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"f8-agent-provider-test-{provider_id}-{model_id}",
        ).start()

    @QtCore.Slot(str, str, bool, str)
    def _record_model_test_result(self, provider_id: str, model_id: str, success: bool, error: str) -> None:
        cfg = self.provider_by_id(provider_id)
        if cfg is not None:
            for model in cfg.cached_models:
                if model.model_id == model_id:
                    model.health_status = "ok" if success else "error"
                    break
            self._persist()
        self.model_tested.emit(provider_id, model_id, bool(success), str(error or ""))

    @staticmethod
    def _resolve_storage_path() -> Path:
        return Path.home() / ".config" / "Feel8" / "ai_providers.json"

    def _load(self) -> None:
        if self._storage_path.exists():
            try:
                raw = self._storage_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                provider_list: Any = []
                if isinstance(data, list):
                    provider_list = data
                elif isinstance(data, dict):
                    self.active_inline_provider = str(data.get("active_inline_provider", ""))
                    self.active_chat_provider = str(data.get("active_chat_provider", ""))
                    provider_list = data.get("providers", [])

                loaded: dict[str, ProviderConfig] = {}
                if isinstance(provider_list, list):
                    for item in provider_list:
                        if not isinstance(item, dict):
                            logger.warning("Skipping malformed provider entry: %r", item)
                            continue
                        try:
                            cfg = _provider_from_dict(dict(item))
                        except (KeyError, ValueError):
                            logger.warning("Skipping malformed provider entry: %r", item)
                            continue
                        loaded[cfg.provider_id] = cfg
                self._providers = list(loaded.values())
                return
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to load AI provider config from %s", self._storage_path)
        self._providers = [_copy_provider(provider) for provider in DEFAULT_PROVIDERS]

    def _persist(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_inline_provider": self.active_inline_provider,
                "active_chat_provider": self.active_chat_provider,
                "providers": [_provider_to_dict(provider) for provider in self._providers],
            }
            self._storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Failed to persist AI provider config to %s", self._storage_path)
