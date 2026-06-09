"""
Persistent storage for Studio agent provider configurations.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import threading
from pathlib import Path
from typing import Any, cast

from qtpy import QtCore  # type: ignore[import-not-found]

from .connectivity import check_model_connectivity
from .model_catalog import (
    copy_model,
    copy_provider,
    discover_endpoint_model_catalog,
    infer_model_capabilities,
    merge_model_lists,
    supports_agent_chat_model,
)
from .registry import (
    DEFAULT_PROVIDERS,
    ModelCapabilities,
    ModelInfo,
    ModelKind,
    ProviderConfig,
    ProviderInferenceService,
    parse_inference_service,
)

logger = logging.getLogger(__name__)
_SHARED_AI_PROVIDER_STORE: AiProviderStore | None = None


def _capabilities_to_dict(caps: ModelCapabilities) -> dict[str, Any]:
    return {
        "model_kind": caps.model_kind,
        "supports_agent_chat": caps.supports_agent_chat,
        "supports_reasoning": caps.supports_reasoning,
        "supports_vision": caps.supports_vision,
        "reasoning_levels": list(caps.reasoning_levels),
        "max_context_tokens": caps.max_context_tokens,
    }


def _capabilities_from_dict(payload: dict[str, Any]) -> ModelCapabilities:
    if "model_kind" not in payload:
        raise ValueError("AI model capabilities are missing model_kind.")
    if "supports_agent_chat" not in payload:
        raise ValueError("AI model capabilities are missing supports_agent_chat.")

    model_kind_raw = str(payload["model_kind"])
    if model_kind_raw not in ("agent", "image", "embedding", "audio", "realtime", "moderation", "video", "tool"):
        raise ValueError(f"Unsupported AI model kind: {model_kind_raw}")
    return ModelCapabilities(
        model_kind=cast(ModelKind, model_kind_raw),
        supports_agent_chat=bool(payload["supports_agent_chat"]),
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
    model_id = str(payload.get("model_id", ""))
    capabilities_raw = payload.get("capabilities")
    if not isinstance(capabilities_raw, dict):
        raise ValueError(f"AI model config is missing capabilities for model_id={model_id!r}.")
    capabilities_payload = dict(capabilities_raw)
    capabilities = _capabilities_from_dict(capabilities_payload)
    return ModelInfo(
        model_id=model_id,
        display_name=str(payload.get("display_name", model_id)),
        capabilities=capabilities,
        health_status=str(payload.get("health_status", "unknown")),
    )


def _provider_to_dict(provider: ProviderConfig) -> dict[str, Any]:
    return {
        "provider_id": provider.provider_id,
        "display_name": provider.display_name,
        "inference_service": provider.inference_service,
        "api_key": provider.api_key,
        "endpoint": provider.endpoint,
        "api_version": provider.api_version,
        "cached_models": [_model_to_dict(model) for model in provider.cached_models],
        "chat_model_id": provider.chat_model_id,
        "reasoning_level": provider.reasoning_level,
    }


def _provider_from_dict(payload: dict[str, Any]) -> ProviderConfig:
    service_raw = payload.get("inference_service")
    if not isinstance(service_raw, str) or not service_raw.strip():
        raise ValueError("AI provider config is missing inference_service.")
    inference_service = parse_inference_service(service_raw.strip())

    return ProviderConfig(
        provider_id=str(payload["provider_id"]),
        display_name=str(payload.get("display_name", payload["provider_id"])),
        inference_service=cast(ProviderInferenceService, inference_service),
        api_key=str(payload.get("api_key", "")),
        endpoint=str(payload.get("endpoint", "")),
        api_version=str(payload.get("api_version", "")),
        cached_models=[_model_from_dict(dict(model)) for model in payload.get("cached_models", [])],
        chat_model_id=str(payload.get("chat_model_id", "")),
        reasoning_level=str(payload.get("reasoning_level", "")),
    )


class AiProviderStore(QtCore.QObject):
    providers_changed = QtCore.Signal()
    models_fetched = QtCore.Signal(str, bool, str)
    model_tested = QtCore.Signal(str, str, bool, str)
    _models_fetch_finished = QtCore.Signal(str, bool, str, object)
    _model_test_finished = QtCore.Signal(str, str, bool, str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._providers: list[ProviderConfig] = []
        self.active_chat_provider: str = ""
        self._storage_path = self._resolve_storage_path()
        self._models_fetch_finished.connect(self._record_models_fetch_result)  # type: ignore[attr-defined]
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

    def save_active_chat_provider(self, chat_id: str) -> None:
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

        model = ModelInfo(
            model_id=normalized_model_id,
            display_name=normalized_display_name,
            capabilities=infer_model_capabilities(normalized_model_id),
            health_status="unknown",
        )
        cfg.cached_models.append(model)
        if supports_agent_chat_model(model) and not cfg.chat_model_id:
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
        if cfg.chat_model_id in ids_to_remove:
            cfg.chat_model_id = ""
        self.save_provider(cfg)
        return removed_count

    def discover_endpoint_models_async(self, provider_id: str) -> None:
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            logger.warning("discover_endpoint_models_async: unknown provider_id=%r", provider_id)
            self.models_fetched.emit(provider_id, False, "Provider not found")
            return

        provider_snapshot = copy_provider(cfg)

        def _worker() -> None:
            try:
                result = discover_endpoint_model_catalog(provider_snapshot)
                if result.success:
                    logger.info(
                        "Endpoint model discovery finished provider=%s count=%s",
                        provider_snapshot.provider_id,
                        len(result.models),
                    )
                else:
                    logger.warning(
                        "Endpoint model discovery failed provider=%s error=%s",
                        provider_snapshot.provider_id,
                        result.message,
                    )
                self._models_fetch_finished.emit(
                    provider_snapshot.provider_id,
                    result.success,
                    result.message,
                    list(result.models),
                )
            except Exception as exc:
                logger.exception("Endpoint model discovery failed provider=%s", provider_snapshot.provider_id)
                self._models_fetch_finished.emit(
                    provider_snapshot.provider_id,
                    False,
                    f"{type(exc).__name__}: {exc}",
                    [],
                )

        self._start_catalog_worker(provider_id, _worker)

    def _start_catalog_worker(self, provider_id: str, worker: Callable[[], None]) -> None:
        threading.Thread(
            target=worker,
            daemon=True,
            name=f"f8-agent-model-catalog-{provider_id}",
        ).start()

    def test_models_async(self, provider_id: str, model_ids: list[str] | None = None) -> bool:
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            logger.warning("test_models_async: unknown provider_id=%r", provider_id)
            return False
        to_test = [
            model
            for model in cfg.cached_models
            if supports_agent_chat_model(model) and (model_ids is None or model.model_id in model_ids)
        ]
        if not to_test:
            logger.warning("test_models_async: no models selected provider_id=%r model_ids=%r", provider_id, model_ids)
            return False

        provider_snapshot = copy_provider(cfg)
        for model in to_test:
            self._test_model_with_provider(provider=provider_snapshot, model_id=model.model_id)
        return True

    def _test_model_with_provider(self, *, provider: ProviderConfig, model_id: str) -> None:
        def _worker() -> None:
            try:
                result = check_model_connectivity(provider, model_id)
                if result.success:
                    logger.info(
                        "Agent Framework model connectivity check finished provider=%s model=%s",
                        provider.provider_id,
                        model_id,
                    )
                else:
                    logger.warning(
                        "Agent Framework model connectivity check failed provider=%s model=%s error=%s",
                        provider.provider_id,
                        model_id,
                        result.error,
                    )
                self._model_test_finished.emit(provider.provider_id, model_id, result.success, result.error)
            except Exception as exc:
                logger.exception(
                    "Agent Framework model connectivity check failed provider=%s model=%s",
                    provider.provider_id,
                    model_id,
                )
                self._model_test_finished.emit(provider.provider_id, model_id, False, f"{type(exc).__name__}: {exc}")

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"f8-agent-provider-test-{provider.provider_id}-{model_id}",
        ).start()

    @QtCore.Slot(str, bool, str, object)
    def _record_models_fetch_result(self, provider_id: str, success: bool, error: str, models_payload: object) -> None:
        if success:
            cfg = self.provider_by_id(provider_id)
            if cfg is not None:
                incoming_models: list[ModelInfo] = []
                if isinstance(models_payload, list):
                    incoming_models = [copy_model(model) for model in models_payload if isinstance(model, ModelInfo)]
                cfg.cached_models = merge_model_lists(cfg.cached_models, incoming_models)
                self.save_provider(cfg, emit=False)
                if not error:
                    error = f"Discovered {len(incoming_models)} agent model(s)."
        self.models_fetched.emit(provider_id, bool(success), str(error or ""))

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
        self._providers = [copy_provider(provider) for provider in DEFAULT_PROVIDERS]

    def _persist(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_chat_provider": self.active_chat_provider,
                "providers": [_provider_to_dict(provider) for provider in self._providers],
            }
            self._storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Failed to persist AI provider config to %s", self._storage_path)


def shared_ai_provider_store() -> AiProviderStore:
    global _SHARED_AI_PROVIDER_STORE
    if _SHARED_AI_PROVIDER_STORE is None:
        _SHARED_AI_PROVIDER_STORE = AiProviderStore()
    return _SHARED_AI_PROVIDER_STORE
