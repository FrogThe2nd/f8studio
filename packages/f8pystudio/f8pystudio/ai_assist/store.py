"""
AiProviderStore — persistent storage for AI provider configurations.

Configs are saved as JSON to ``~/.config/Feel8/ai_providers.json``. The store also
handles asynchronous model list fetching via QNetworkAccessManager so the Qt
event loop is never blocked.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from qtpy import QtCore, QtNetwork  # type: ignore[import-not-found]

from .registry import (
    DEFAULT_PROVIDERS,
    ModelCapabilities,
    ModelInfo,
    ProviderApiMode,
    ProviderConfig,
    ProviderProtocol,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON codec helpers (no magic — explicit field mapping)
# ---------------------------------------------------------------------------

def _capabilities_to_dict(caps: ModelCapabilities) -> dict:
    return {
        "supports_fim": caps.supports_fim,
        "supports_reasoning": caps.supports_reasoning,
        "supports_vision": caps.supports_vision,
        "reasoning_levels": list(caps.reasoning_levels),
        "max_context_tokens": caps.max_context_tokens,
    }


def _capabilities_from_dict(d: dict) -> ModelCapabilities:
    return ModelCapabilities(
        supports_fim=bool(d.get("supports_fim", False)),
        supports_reasoning=bool(d.get("supports_reasoning", False)),
        supports_vision=bool(d.get("supports_vision", False)),
        reasoning_levels=tuple(str(l) for l in d.get("reasoning_levels", [])),
        max_context_tokens=int(d.get("max_context_tokens", 128_000)),
    )


def _model_to_dict(m: ModelInfo) -> dict:
    return {
        "model_id": m.model_id,
        "display_name": m.display_name,
        "capabilities": _capabilities_to_dict(m.capabilities),
        "health_status": m.health_status,
    }


def _model_from_dict(d: dict) -> ModelInfo:
    return ModelInfo(
        model_id=str(d.get("model_id", "")),
        display_name=str(d.get("display_name", d.get("model_id", ""))),
        capabilities=_capabilities_from_dict(d.get("capabilities", {})),
        health_status=str(d.get("health_status", "unknown")),
    )


def _provider_to_dict(p: ProviderConfig) -> dict:
    return {
        "provider_id": p.provider_id,
        "display_name": p.display_name,
        "protocol": p.protocol,
        "api_mode": p.api_mode,
        "api_key": p.api_key,
        "endpoint": p.endpoint,
        "models_path": p.models_path,
        "chat_path": p.chat_path,
        "cached_models": [_model_to_dict(m) for m in p.cached_models],
        "inline_model_id": p.inline_model_id,
        "chat_model_id": p.chat_model_id,
        "reasoning_level": p.reasoning_level,
    }


def _provider_from_dict(d: dict) -> ProviderConfig:
    protocol_raw = str(d.get("protocol", "openai"))
    if protocol_raw not in ("openai", "anthropic", "ollama", "custom"):
        protocol_raw = "openai"
    protocol = cast(ProviderProtocol, protocol_raw)

    api_mode_raw = str(d.get("api_mode", "")).strip()
    if api_mode_raw not in ("chat_completions", "responses"):
        raise ValueError("AI provider config is missing valid api_mode.")
    api_mode = cast(ProviderApiMode, api_mode_raw)

    return ProviderConfig(
        provider_id=str(d["provider_id"]),
        display_name=str(d.get("display_name", d["provider_id"])),
        protocol=protocol,
        api_mode=api_mode,
        api_key=str(d.get("api_key", "")),
        endpoint=str(d.get("endpoint", "")),
        models_path=str(d.get("models_path", "")),
        chat_path=str(d.get("chat_path", "")),
        cached_models=[_model_from_dict(m) for m in d.get("cached_models", [])],
        inline_model_id=str(d.get("inline_model_id", "")),
        chat_model_id=str(d.get("chat_model_id", "")),
        reasoning_level=str(d.get("reasoning_level", "")),
    )

# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------

_DEFAULT_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434/v1",
    "custom": "",
}

_MODELS_PATHS: dict[str, str] = {
    "openai": "/models",
    "anthropic": "/v1/models",
    "ollama": "/models",
    "custom": "/models",
}


def _effective_endpoint(cfg: ProviderConfig) -> str:
    ep = str(cfg.endpoint or "").strip().rstrip("/")
    if ep:
        return ep
    return _DEFAULT_ENDPOINTS.get(cfg.protocol, "")


def _normalize_endpoint(endpoint: str) -> str:
    return str(endpoint or "").strip().rstrip("/").lower()


def _join_api_path(base: str, path: str) -> str:
    normalized_base = str(base or "").strip().rstrip("/")
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if normalized_base.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path[3:]
    return f"{normalized_base}{normalized_path}"


def _models_url(cfg: ProviderConfig) -> str:
    base = _effective_endpoint(cfg)
    if cfg.models_path:
        path = cfg.models_path
        if not path.startswith("/"):
            path = "/" + path
    else:
        path = _MODELS_PATHS.get(cfg.protocol, "/models")
    return f"{base}{path}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class AiProviderStore(QtCore.QObject):
    """
    Persistent store for AI provider configurations.

    Signals
    -------
    providers_changed
        Emitted whenever the list of providers is modified.
    models_fetched(provider_id, success, error_message)
        Emitted when an async model fetch completes.
    """

    providers_changed = QtCore.Signal()
    models_fetched = QtCore.Signal(str, bool, str)  # id, success, error
    model_tested = QtCore.Signal(str, str, bool, str)  # provider_id, model_id, success, error

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._providers: list[ProviderConfig] = []
        self.active_inline_provider: str = ""
        self.active_chat_provider: str = ""
        self._nam: QtNetwork.QNetworkAccessManager | None = None
        self._pending_replies: dict[str, QtNetwork.QNetworkReply] = {}
        self._storage_path = self._resolve_storage_path()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def providers(self) -> list[ProviderConfig]:
        """Return a *copy* of the provider list."""
        return list(self._providers)

    def provider_by_id(self, provider_id: str) -> ProviderConfig | None:
        for p in self._providers:
            if p.provider_id == provider_id:
                return p
        return None

    def save_provider(self, cfg: ProviderConfig, emit: bool = True) -> None:
        """Insert or update a provider and persist immediately."""
        for i, existing in enumerate(self._providers):
            if existing.provider_id == cfg.provider_id:
                self._providers[i] = cfg
                self._persist()
                if emit:
                    self.providers_changed.emit()
                return
        self._providers.append(cfg)
        self._persist()
        if emit:
            self.providers_changed.emit()

    def save_active_providers(self, inline_id: str, chat_id: str) -> None:
        """Save the global active provider choices without emitting providers_changed."""
        self.active_inline_provider = str(inline_id or "")
        self.active_chat_provider = str(chat_id or "")
        self._persist()

    def delete_provider(self, provider_id: str) -> None:
        """Remove a provider and persist."""
        before = len(self._providers)
        self._providers = [p for p in self._providers if p.provider_id != provider_id]
        if len(self._providers) != before:
            self._persist()
            self.providers_changed.emit()

    def fetch_models_async(self, provider_id: str) -> None:
        """
        Fetch the model list from the provider's API endpoint.

        Results are delivered via the ``models_fetched`` signal and,
        on success, the matching ``ProviderConfig.cached_models`` is
        updated in place before the provider is persisted.
        """
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            logger.warning("fetch_models_async: unknown provider_id=%r", provider_id)
            self.models_fetched.emit(provider_id, False, "Provider not found")
            return

        url = _models_url(cfg)
        if not url:
            self.models_fetched.emit(provider_id, False, "Cannot determine models URL")
            return

        self._ensure_nam()
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
        request.setHeader(QtNetwork.QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")

        if cfg.api_key:
            if cfg.protocol == "anthropic":
                request.setRawHeader(b"x-api-key", cfg.api_key.encode())
                request.setRawHeader(b"anthropic-version", b"2023-06-01")
            else:
                request.setRawHeader(b"Authorization", f"Bearer {cfg.api_key}".encode())

        assert self._nam is not None
        reply = self._nam.get(request)
        self._pending_replies[provider_id] = reply
        reply.finished.connect(lambda: self._on_models_reply(provider_id, reply))  # type: ignore[attr-defined]
        logger.debug("fetch_models_async: GET %s provider=%s", url, provider_id)

    def test_models_async(self, provider_id: str, model_ids: list[str] | None = None) -> None:
        """
        Verify connectivity for specific models (or all if model_ids is None).
        Sends a minimal ping request to the chat completions endpoint.
        """
        cfg = self.provider_by_id(provider_id)
        if cfg is None:
            return

        to_test = []
        if model_ids is not None:
            to_test = [m for m in cfg.cached_models if m.model_id in model_ids]
        else:
            to_test = cfg.cached_models

        if not to_test:
            return

        self._ensure_nam()
        url = self._test_chat_url(cfg)
        
        for m in to_test:
            # Re-init status to unknown while testing? or keep old? 
            # Better to show it's active.
            request = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
            request.setHeader(QtNetwork.QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            if cfg.api_key:
                if cfg.protocol == "anthropic":
                    request.setRawHeader(b"x-api-key", cfg.api_key.encode())
                    request.setRawHeader(b"anthropic-version", b"2023-06-01")
                else:
                    request.setRawHeader(b"Authorization", f"Bearer {cfg.api_key}".encode())

            payload = self._build_ping_payload(cfg, m.model_id)
            body = json.dumps(payload).encode()
            
            reply = self._nam.post(request, body)
            # Use a slightly different key to avoid collision if multiple models tested
            reply.finished.connect(lambda r=reply, mid=m.model_id: self._on_test_reply(provider_id, mid, r))  # type: ignore[attr-defined]

    def _test_chat_url(self, cfg: ProviderConfig) -> str:
        base = _effective_endpoint(cfg)
        if cfg.chat_path:
            path = cfg.chat_path
            return _join_api_path(base, path)
        
        if cfg.protocol == "anthropic":
            return f"{base}/v1/messages"
        if cfg.protocol in ("openai", "custom") and cfg.api_mode == "responses":
            return f"{base}/responses"
        return f"{base}/chat/completions"

    def _build_ping_payload(self, cfg: ProviderConfig, model_id: str) -> dict:
        if cfg.protocol == "anthropic":
            return {
                "model": model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
        if cfg.protocol in ("openai", "custom") and cfg.api_mode == "responses":
            return {
                "model": model_id,
                "input": [{"role": "user", "content": "ping"}],
                "max_output_tokens": 1,
                "store": False,
            }
        # openai / ollama / custom
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }

    def _on_test_reply(self, provider_id: str, model_id: str, reply: QtNetwork.QNetworkReply) -> None:
        try:
            success = reply.error() == QtNetwork.QNetworkReply.NetworkError.NoError
            err = reply.errorString() if not success else ""
            
            cfg = self.provider_by_id(provider_id)
            if cfg:
                for m in cfg.cached_models:
                    if m.model_id == model_id:
                        # We need to recreate the frozen dataclass if it was frozen, 
                        # but ModelInfo is NOT frozen (ModelCapabilities IS).
                        # Wait, registry.py says: @dataclass (NOT frozen=True) for ModelInfo.
                        # But health_status is added there.
                        m.health_status = "ok" if success else "error"
                        break
                self._persist()
            
            self.model_tested.emit(provider_id, model_id, success, err)
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_nam(self) -> None:
        if self._nam is None:
            self._nam = QtNetwork.QNetworkAccessManager(self)

    def _on_models_reply(self, provider_id: str, reply: QtNetwork.QNetworkReply) -> None:
        self._pending_replies.pop(provider_id, None)
        try:
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
                err = reply.errorString()
                logger.warning("fetch_models_async error: provider=%s error=%s", provider_id, err)
                self.models_fetched.emit(provider_id, False, err)
                return

            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
            models = self._parse_models_response(provider_id, raw)
            cfg = self.provider_by_id(provider_id)
            if cfg is not None:
                cfg.cached_models = models
                self._persist()
            logger.debug("fetch_models_async: got %d models for provider=%s", len(models), provider_id)
            self.models_fetched.emit(provider_id, True, "")
        except Exception:
            logger.exception("fetch_models_async: unexpected error for provider=%s", provider_id)
            self.models_fetched.emit(provider_id, False, "Unexpected error parsing model list")
        finally:
            reply.deleteLater()

    def _parse_models_response(self, provider_id: str, raw: str) -> list[ModelInfo]:
        """Parse OpenAI-compatible /models response."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON from models endpoint: {exc}") from exc

        cfg = self.provider_by_id(provider_id)
        protocol = cfg.protocol if cfg else "openai"

        items: list[dict] = []
        if isinstance(data, dict):
            # OpenAI / Ollama style: {"data": [...]}
            if "data" in data and isinstance(data["data"], list):
                items = data["data"]
            # Anthropic style: {"models": [...]}
            elif "models" in data and isinstance(data["models"], list):
                items = data["models"]
        elif isinstance(data, list):
            items = data

        result: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id", item.get("name", ""))).strip()
            if not model_id:
                continue
            display_name = str(item.get("display_name", item.get("name", model_id)))
            
            # Filter out non-chat models for non-ollama providers
            if protocol != "ollama":
                obj_type = str(item.get("object", item.get("type", ""))).lower()
                if obj_type and obj_type not in ("model", "language_model", ""):
                    continue
                    
            # Parse OpenRouter or other extended metadata
            ctx_len = 128_000 # default
            if "context_length" in item:
                try: ctx_len = int(item["context_length"])
                except ValueError: pass
            elif "top_provider" in item and isinstance(item["top_provider"], dict) and "context_length" in item["top_provider"]:
                try: ctx_len = int(item["top_provider"]["context_length"])
                except ValueError: pass
                
            supports_vision = False
            arch = item.get("architecture")
            if isinstance(arch, dict):
                modality = str(arch.get("modality", "")).lower()
                if "image" in modality or "vision" in modality:
                    supports_vision = True
            
            # Guessing by name if metadata missing
            lname = model_id.lower()
            supports_reasoning = any(x in lname for x in ("o1", "o3", "r1", "think", "reasoning", "deepseek-reasoner"))
            if not supports_vision:
                supports_vision = any(x in lname for x in ("vision", "vl", "omni", "gpt-4o", "claude-3.5", "claude-3-5", "gemini-1.5", "gemini-2.0", "llava", "qwen-vl"))
            
            caps = ModelCapabilities(
                supports_reasoning=supports_reasoning,
                supports_vision=supports_vision,
                max_context_tokens=ctx_len,
            )
            result.append(ModelInfo(model_id=model_id, display_name=display_name, capabilities=caps))

        result.sort(key=lambda m: m.model_id)
        return result

    @staticmethod
    def _resolve_storage_path() -> Path:
        return Path.home() / ".config" / "Feel8" / "ai_providers.json"

    def _load(self) -> None:
        """Load persisted providers. If no config exists, initialize with defaults."""
        defaults: dict[str, ProviderConfig] = {p.provider_id: p for p in DEFAULT_PROVIDERS}

        if self._storage_path.exists():
            try:
                raw = self._storage_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                
                provider_list = []
                if isinstance(data, list):
                    provider_list = data
                elif isinstance(data, dict):
                    self.active_inline_provider = str(data.get("active_inline_provider", ""))
                    self.active_chat_provider = str(data.get("active_chat_provider", ""))
                    provider_list = data.get("providers", [])

                loaded: dict[str, ProviderConfig] = {}
                if isinstance(provider_list, list):
                    for item in provider_list:
                        try:
                            cfg = _provider_from_dict(item)
                            loaded[cfg.provider_id] = cfg
                        except (KeyError, ValueError):
                            logger.warning("Skipping malformed provider entry: %r", item)
                
                # Use exactly what was saved, allowing deleted defaults to stay deleted
                self._providers = list(loaded.values())
                return
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to load AI provider config from %s", self._storage_path)

        # File doesn't exist or couldn't be parsed -> fallback to defaults
        self._providers = list(defaults.values())

    def _persist(self) -> None:
        """Write current provider list to disk."""
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_inline_provider": self.active_inline_provider,
                "active_chat_provider": self.active_chat_provider,
                "providers": [_provider_to_dict(p) for p in self._providers]
            }
            self._storage_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("Failed to persist AI provider config to %s", self._storage_path)
