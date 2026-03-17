"""
AiHttpClient — Qt-native HTTP client for AI provider APIs.

Supports:
* Non-streaming chat completions  (OpenAI / Anthropic / Ollama compatible)
* Streaming SSE chat completions  (for Chat / Edit / Plan modes)
* FIM completions                 (for inline suggestions)

All network I/O uses ``QNetworkAccessManager`` so the Qt event loop is never
blocked.  Each request gets a caller-supplied callback; the client never
swallows exceptions silently.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from qtpy import QtCore, QtNetwork  # type: ignore[import-not-found]

from .registry import ProviderConfig, ProviderProtocol

logger = logging.getLogger(__name__)

# Type aliases
OnChunk = Callable[[str], None]          # streaming delta text
OnDone = Callable[[str, str | None], None]   # full text, error or None
OnResult = Callable[[str, str | None], None]  # non-streaming: text, error


def _effective_base(cfg: ProviderConfig) -> str:
    ep = str(cfg.endpoint or "").strip().rstrip("/")
    _DEFAULTS: dict[str, str] = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434/v1",
        "custom": "",
    }
    return ep if ep else _DEFAULTS.get(cfg.protocol, "")


def _auth_headers(cfg: ProviderConfig) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = []
    if cfg.protocol == "anthropic":
        if cfg.api_key:
            headers.append((b"x-api-key", cfg.api_key.encode()))
        headers.append((b"anthropic-version", b"2023-06-01"))
    else:
        if cfg.api_key:
            headers.append((b"Authorization", f"Bearer {cfg.api_key}".encode()))
    return headers


def _build_request(url: str, cfg: ProviderConfig) -> QtNetwork.QNetworkRequest:
    req = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
    req.setHeader(QtNetwork.QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
    for name, value in _auth_headers(cfg):
        req.setRawHeader(name, value)
    return req


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _chat_payload_openai(
    model_id: str,
    messages: list[dict],
    *,
    stream: bool,
    reasoning_level: str,
    max_tokens: int,
) -> dict:
    # Translate intermediate multimodal format to OpenAI format
    processed_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for part in content:
                if part.get("type") == "text":
                    new_content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image":
                    mime = part.get("mime_type", "image/png")
                    data = part.get("image", "")
                    new_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"}
                    })
            processed_messages.append({"role": msg["role"], "content": new_content})
        else:
            processed_messages.append(msg)

    payload: dict = {
        "model": model_id,
        "messages": processed_messages,
        "stream": stream,
        "max_tokens": max_tokens,
    }
    if reasoning_level:
        payload["reasoning_effort"] = reasoning_level
    return payload


def _chat_payload_anthropic(
    model_id: str,
    messages: list[dict],
    *,
    system: str,
    stream: bool,
    reasoning_level: str,
    max_tokens: int,
) -> dict:
    # Anthropic splits system from messages and has a specific multimodal format
    processed_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for part in content:
                if part.get("type") == "text":
                    new_content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image":
                    mime = part.get("mime_type", "image/png")
                    data = part.get("image", "")
                    new_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": data,
                        }
                    })
            processed_messages.append({"role": msg["role"], "content": new_content})
        else:
            processed_messages.append(msg)

    payload: dict = {
        "model": model_id,
        "messages": processed_messages,
        "stream": stream,
        "max_tokens": max_tokens,
    }
    system_text = system or next(
        (m.get("content", "") for m in messages if m.get("role") == "system"), ""
    )
    if system_text:
        payload["system"] = system_text
    if reasoning_level:
        payload["thinking"] = {"type": "enabled", "budget_tokens": _reasoning_budget(reasoning_level)}
    return payload


def _reasoning_budget(level: str) -> int:
    return {"low": 2_000, "medium": 8_000, "high": 16_000}.get(level, 4_000)


def _fim_payload_openai(model_id: str, prefix: str, suffix: str, max_tokens: int) -> dict:
    """
    Build a FIM request using chat-based Fill-In-the-Middle prompt since most
    hosted models no longer provide a dedicated completions endpoint.
    """
    return {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a code completion assistant. "
                    "Complete the code between <PREFIX> and <SUFFIX> tags. "
                    "Output ONLY the completion text — no explanations, no markdown fences."
                ),
            },
            {
                "role": "user",
                "content": f"<PREFIX>{prefix}</PREFIX><SUFFIX>{suffix}</SUFFIX>",
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AiHttpClient(QtCore.QObject):
    """
    Stateless AI HTTP client.  One instance can be shared across many requests.

    All callbacks are called on the Qt main thread.
    """

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QtNetwork.QNetworkAccessManager(self)
        # Keep references to in-flight replies so they aren't GC'd early.
        self._active_replies: set[QtNetwork.QNetworkReply] = set()
        # Track replies by request_id for explicit cancellation.
        self._requests: dict[str, QtNetwork.QNetworkReply] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def abort_request(self, request_id: str) -> None:
        """Explicitly abort an in-flight request."""
        rid = str(request_id or "").strip()
        if not rid:
            return
        reply = self._requests.get(rid)
        if reply is not None:
            logger.info("AI HTTP: aborting request id=%s", rid)
            reply.abort()
            # on_done / on_result will be called via finished signal with OperationCanceledError

    def chat_completion(
        self,
        cfg: ProviderConfig,
        *,
        model_id: str,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
        on_result: OnResult,
        request_id: str = "",
    ) -> None:
        """Non-streaming chat completion."""
        payload = self._build_chat_payload(
            cfg,
            model_id=model_id,
            messages=messages,
            system=system,
            stream=False,
            max_tokens=max_tokens,
        )
        url = self._chat_url(cfg)
        self._post_json(cfg, url, payload, on_result=on_result, request_id=request_id)

    def chat_completion_stream(
        self,
        cfg: ProviderConfig,
        *,
        model_id: str,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
        on_chunk: OnChunk,
        on_done: OnDone,
        request_id: str = "",
    ) -> None:
        """Streaming SSE chat completion."""
        payload = self._build_chat_payload(
            cfg,
            model_id=model_id,
            messages=messages,
            system=system,
            stream=True,
            max_tokens=max_tokens,
        )
        url = self._chat_url(cfg)
        self._post_json_stream(cfg, url, payload, on_chunk=on_chunk, on_done=on_done, request_id=request_id)

    def fim_completion(
        self,
        cfg: ProviderConfig,
        *,
        model_id: str,
        prefix: str,
        suffix: str,
        max_tokens: int = 256,
        on_result: OnResult,
        request_id: str = "",
    ) -> None:
        """FIM (Fill-In-the-Middle) completion for inline suggestions."""
        payload = _fim_payload_openai(model_id, prefix, suffix, max_tokens)
        url = self._chat_url(cfg)  # Use chat completions endpoint (FIM via chat)
        self._post_json(cfg, url, payload, on_result=lambda text, err: on_result(text, err), request_id=request_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chat_url(self, cfg: ProviderConfig) -> str:
        base = _effective_base(cfg)
        if getattr(cfg, "chat_path", ""):
            path = cfg.chat_path
            if not path.startswith("/"):
                path = "/" + path
            return f"{base}{path}"
            
        if cfg.protocol == "anthropic":
            return f"{base}/v1/messages"
        return f"{base}/chat/completions"

    def _build_chat_payload(
        self,
        cfg: ProviderConfig,
        *,
        model_id: str,
        messages: list[dict],
        system: str,
        stream: bool,
        max_tokens: int,
    ) -> dict:
        reasoning_level = str(cfg.reasoning_level or "")
        if cfg.protocol == "anthropic":
            return _chat_payload_anthropic(
                model_id,
                messages,
                system=system,
                stream=stream,
                reasoning_level=reasoning_level,
                max_tokens=max_tokens,
            )
        return _chat_payload_openai(
            model_id,
            messages,
            stream=stream,
            reasoning_level=reasoning_level,
            max_tokens=max_tokens,
        )

    def _post_json(
        self,
        cfg: ProviderConfig,
        url: str,
        payload: dict,
        *,
        on_result: OnResult,
        request_id: str = "",
    ) -> None:
        rid = str(request_id or "").strip()
        req = _build_request(url, cfg)
        body = json.dumps(payload, ensure_ascii=False).encode()
        reply = self._nam.post(req, body)
        self._active_replies.add(reply)
        if rid:
            self._requests[rid] = reply
        reply.finished.connect(  # type: ignore[attr-defined]
            lambda: self._on_non_stream_reply(reply, cfg.protocol, on_result, rid)
        )

    def _on_non_stream_reply(
        self,
        reply: QtNetwork.QNetworkReply,
        protocol: ProviderProtocol,
        on_result: OnResult,
        request_id: str = "",
    ) -> None:
        self._active_replies.discard(reply)
        if request_id:
            self._requests.pop(request_id, None)
        try:
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
                err = reply.errorString()
                # Handle user cancellation
                if reply.error() == QtNetwork.QNetworkReply.NetworkError.OperationCanceledError:
                    logger.info("AI HTTP request canceled by user")
                    on_result("", "Canceled")
                    return
                
                logger.warning("AI HTTP error: %s", err)
                on_result("", err)
                return

            if reply.isOpen() and reply.isReadable():
                raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
                text = self._extract_text(raw, protocol)
                on_result(text, None)
            else:
                on_result("", "Reply was closed prematurely")
        except Exception as exc:
            logger.exception("_on_non_stream_reply: unexpected error")
            on_result("", str(exc))
        finally:
            reply.deleteLater()

    def _post_json_stream(
        self,
        cfg: ProviderConfig,
        url: str,
        payload: dict,
        *,
        on_chunk: OnChunk,
        on_done: OnDone,
        request_id: str = "",
    ) -> None:
        rid = str(request_id or "").strip()
        req = _build_request(url, cfg)
        body = json.dumps(payload, ensure_ascii=False).encode()
        reply = self._nam.post(req, body)
        self._active_replies.add(reply)
        if rid:
            self._requests[rid] = reply
        state = _StreamState(protocol=cfg.protocol, on_chunk=on_chunk, on_done=on_done)
        reply.readyRead.connect(lambda: state.feed(bytes(reply.readAll())))  # type: ignore[attr-defined]
        reply.finished.connect(lambda: self._on_stream_done(reply, state, rid))  # type: ignore[attr-defined]

    def _on_stream_done(self, reply: QtNetwork.QNetworkReply, state: "_StreamState", request_id: str = "") -> None:
        self._active_replies.discard(reply)
        if request_id:
            self._requests.pop(request_id, None)
        try:
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
                err = reply.errorString()
                # OperationCanceledError happens when we call .abort()
                if reply.error() == QtNetwork.QNetworkReply.NetworkError.OperationCanceledError:
                    logger.info("AI stream request canceled by user")
                    state.finish("Canceled")
                    return

                logger.warning("AI stream HTTP error: %s", err)
                # Drain any remaining bytes if possible
                if reply.isOpen() and reply.isReadable():
                    state.feed(bytes(reply.readAll()))
                state.finish(err)
                return

            if reply.isOpen() and reply.isReadable():
                state.feed(bytes(reply.readAll()))
            state.finish(None)
        except Exception as exc:
            logger.exception("_on_stream_done: unexpected error")
            state.finish(str(exc))
        finally:
            reply.deleteLater()

    @staticmethod
    def _extract_text(raw: str, protocol: ProviderProtocol) -> str:
        """Extract assistant text from a non-streaming response body."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw

        if protocol == "anthropic":
            for block in data.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""

        # OpenAI / Ollama / custom
        choices = data.get("choices", [])
        if choices and isinstance(choices, list):
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message", {})
                if isinstance(msg, dict):
                    return str(msg.get("content", ""))
        return ""


# ---------------------------------------------------------------------------
# SSE streaming state machine
# ---------------------------------------------------------------------------

class _StreamState:
    """Accumulates SSE bytes and calls on_chunk for each delta fragment."""

    def __init__(
        self,
        *,
        protocol: ProviderProtocol,
        on_chunk: OnChunk,
        on_done: OnDone,
    ) -> None:
        self._protocol = protocol
        self._on_chunk = on_chunk
        self._on_done = on_done
        self._buf = b""
        self._full_text = ""
        self._finished = False

    def feed(self, data: bytes) -> None:
        if self._finished:
            return
        self._buf += data
        while b"\n" in self._buf:
            line_bytes, self._buf = self._buf.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    return
                delta = self._extract_delta(payload_str)
                if delta:
                    self._full_text += delta
                    try:
                        self._on_chunk(delta)
                    except Exception:
                        logger.exception("on_chunk callback raised")

    def finish(self, error: str | None) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._on_done(self._full_text, error)
        except Exception:
            logger.exception("on_done callback raised")

    def _extract_delta(self, payload_str: str) -> str:
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            return ""

        if self._protocol == "anthropic":
            if data.get("type") == "content_block_delta":
                delta = data.get("delta", {})
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    return str(delta.get("text", ""))
            return ""

        # OpenAI / Ollama
        choices = data.get("choices", [])
        if choices and isinstance(choices, list):
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta", {})
                if isinstance(delta, dict):
                    return str(delta.get("content", ""))
        return ""
