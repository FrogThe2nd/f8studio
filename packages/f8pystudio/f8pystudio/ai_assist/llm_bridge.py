"""
AiLlmBridge — QWebChannel bridge that exposes AI capabilities to Monaco JS.

Registered as "aiAssist" on the QWebChannel. The bridge dispatches:
  - inline FIM suggestions
  - streaming chat / plan messages
  - non-streaming edit requests (returns full new code)

Each request carries a client-generated ``request_id`` (UUID string) so the JS
side can match responses to pending promises.

Token usage is tracked approximately and broadcast via ``context_usage_updated``.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from qtpy import QtCore  # type: ignore[import-not-found]

from .http_client import AiHttpClient
from .registry import ModelInfo, ProviderConfig
from .store import AiProviderStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_CODE = (
    "You are an expert coding assistant embedded in an IDE. "
    "You help the user write, refactor, and debug code. "
    "Be concise and precise. When providing code, use proper syntax. "
    "When providing explanations, be brief."
)

_SYSTEM_PROMPT_EDIT = (
    "You are a code refactoring assistant. "
    "The user will provide code and an instruction. "
    "Return ONLY the complete rewritten code — no explanation, no markdown fences, no comments about changes."
)

_SYSTEM_PROMPT_PLAN = (
    "You are a thoughtful coding assistant. First ask any clarifying questions "
    "needed to understand the task. Then create a numbered plan. "
    "When the user approves the plan, you may proceed step by step. "
    "Be concise."
)


def _approx_tokens(text: str) -> int:
    """Rough approximation: 1 token ≈ 4 characters."""
    return max(1, math.ceil(len(text) / 4))


class AiLlmBridge(QtCore.QObject):
    """
    QObject registered on QWebChannel as ``"aiAssist"``.

    All public Slots are called from the JS side (Monaco page).
    All Signals deliver results back to JS.
    """

    # ---- signals → JS ----
    inline_suggestion_ready = QtCore.Signal(str, str)   # request_id, text
    chat_chunk_ready = QtCore.Signal(str, str)           # request_id, delta
    chat_done = QtCore.Signal(str, str)                  # request_id, error_or_empty
    edit_result_ready = QtCore.Signal(str, str, str)     # request_id, new_code, error_or_empty
    plan_step_ready = QtCore.Signal(str, str)            # request_id, delta
    plan_done = QtCore.Signal(str, str)                  # request_id, error_or_empty
    context_usage_updated = QtCore.Signal(int, int)      # used_tokens, total_tokens

    def __init__(
        self,
        store: AiProviderStore,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._http = AiHttpClient(self)

        # Active provider/model selections (changeable from quick panel)
        self._inline_provider_id: str = ""
        self._inline_model_id: str = ""
        self._chat_provider_id: str = ""
        self._chat_model_id: str = ""

        # Context tracking
        self._system_tokens: int = _approx_tokens(_SYSTEM_PROMPT_CODE)
        self._code_tokens: int = 0
        self._chat_tokens: int = 0

        # Read global active provider choices from store
        self._inline_provider_id = self._store.active_inline_provider
        if self._inline_provider_id:
            cfg = self._store.provider_by_id(self._inline_provider_id)
            if cfg:
                self._inline_model_id = cfg.inline_model_id

        self._chat_provider_id = self._store.active_chat_provider
        if self._chat_provider_id:
            cfg = self._store.provider_by_id(self._chat_provider_id)
            if cfg:
                self._chat_model_id = cfg.chat_model_id

    # ------------------------------------------------------------------
    # Configuration slots (called from quick panel via QWebChannel)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str)
    def set_inline_model(self, provider_id: str, model_id: str) -> None:
        self._inline_provider_id = str(provider_id or "")
        self._inline_model_id = str(model_id or "")
        
        # Persist selection
        if self._inline_provider_id:
            cfg = self._store.provider_by_id(self._inline_provider_id)
            if cfg:
                cfg.inline_model_id = self._inline_model_id
                self._store.save_provider(cfg, emit=False)
        self._store.save_active_providers(self._inline_provider_id, self._chat_provider_id)
        
        logger.debug("AI inline model: provider=%s model=%s", self._inline_provider_id, self._inline_model_id)

    @QtCore.Slot(str, str)
    def set_chat_model(self, provider_id: str, model_id: str) -> None:
        self._chat_provider_id = str(provider_id or "")
        self._chat_model_id = str(model_id or "")
        
        # Persist selection
        if self._chat_provider_id:
            cfg = self._store.provider_by_id(self._chat_provider_id)
            if cfg:
                cfg.chat_model_id = self._chat_model_id
                self._store.save_provider(cfg, emit=False)
        self._store.save_active_providers(self._inline_provider_id, self._chat_provider_id)
        
        logger.debug("AI chat model: provider=%s model=%s", self._chat_provider_id, self._chat_model_id)

    @QtCore.Slot(str)
    def set_reasoning_level(self, level: str) -> None:
        """Update reasoning level on the chat provider config in-place."""
        cfg = self._store.provider_by_id(self._chat_provider_id)
        if cfg is not None:
            cfg.reasoning_level = str(level or "")
            self._store.save_provider(cfg, emit=False)

    @QtCore.Slot()
    def reset_chat_history(self) -> None:
        """Called when user clicks the reset button in UI."""
        logger.info("AI chat history reset requested by user")
        # In current design, history is held by JS, so this is mainly a signal
        # for backend to clear any ephemeral cached context if it had any.
        pass

    # ------------------------------------------------------------------
    # Inline suggestion (FIM)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str)
    def request_inline_suggestion(self, request_id: str, prefix: str, suffix: str) -> None:
        rid = str(request_id or "")
        cfg = self._chat_provider(for_inline=True)
        if cfg is None:
            self.inline_suggestion_ready.emit(rid, "")
            return

        model_id = self._inline_model_id or cfg.inline_model_id or self._first_model_id(cfg)
        if not model_id:
            self.inline_suggestion_ready.emit(rid, "")
            return

        self._http.fim_completion(
            cfg,
            model_id=model_id,
            prefix=str(prefix or ""),
            suffix=str(suffix or ""),
            max_tokens=256,
            on_result=lambda text, err: self._on_inline_result(rid, text, err),
        )

    def _on_inline_result(self, rid: str, text: str, err: str | None) -> None:
        if err:
            logger.debug("inline suggestion error: %s", err)
            self.inline_suggestion_ready.emit(rid, "")
            return
        cleaned = self._clean_inline_text(str(text or ""))
        self.inline_suggestion_ready.emit(rid, cleaned)

    @staticmethod
    def _clean_inline_text(text: str) -> str:
        """Strip any accidental markdown fences from inline completions."""
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Chat (streaming)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, str)
    def request_chat(self, request_id: str, messages_json: str, code: str, selection: str) -> None:
        rid = str(request_id or "")
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.chat_done.emit(rid, "No AI provider configured")
            return

        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.chat_done.emit(rid, "No model selected")
            return

        try:
            history: list[dict] = json.loads(messages_json) if messages_json else []
            if not isinstance(history, list):
                history = []
        except json.JSONDecodeError:
            history = []

        messages = self._build_chat_messages(history, code, selection)
        self._update_context_tokens(messages)

        self._http.chat_completion_stream(
            cfg,
            model_id=model_id,
            messages=messages,
            system=_SYSTEM_PROMPT_CODE,
            max_tokens=4096,
            on_chunk=lambda delta: self.chat_chunk_ready.emit(rid, delta),
            on_done=lambda _full, err: self.chat_done.emit(rid, str(err or "")),
        )

    @staticmethod
    def _build_chat_messages(history: list[dict], code: str, selection: str) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT_CODE}]
        if code:
            context_content = f"Current file:\n```\n{code}\n```"
            if selection:
                context_content += f"\n\nSelected text:\n```\n{selection}\n```"
            messages.append({"role": "user", "content": context_content})
            messages.append({"role": "assistant", "content": "I can see your code. How can I help?"})
        messages.extend(history)
        return messages

    # ------------------------------------------------------------------
    # Edit mode (non-streaming → diff view)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str)
    def request_edit(self, request_id: str, code: str, instruction: str) -> None:
        rid = str(request_id or "")
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.edit_result_ready.emit(rid, "", "No AI provider configured")
            return

        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.edit_result_ready.emit(rid, "", "No model selected")
            return

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT_EDIT},
            {
                "role": "user",
                "content": (
                    f"Instruction: {instruction}\n\n"
                    f"Code:\n```\n{code}\n```"
                ),
            },
        ]
        self._http.chat_completion(
            cfg,
            model_id=model_id,
            messages=messages,
            system=_SYSTEM_PROMPT_EDIT,
            max_tokens=8192,
            on_result=lambda text, err: self._on_edit_result(rid, text, err),
        )

    def _on_edit_result(self, rid: str, text: str, err: str | None) -> None:
        if err:
            self.edit_result_ready.emit(rid, "", str(err))
            return
        cleaned = self._strip_code_fence(str(text or ""))
        self.edit_result_ready.emit(rid, cleaned, "")

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        # 1. Strip <think>...</think> blocks (reasoning models)
        # Find the last </think> tag. If present, we only want the text after it.
        # This handles cases where the model outputs reasoning before the code.
        think_end = text.rfind("</think>")
        if think_end != -1:
            text = text[think_end + len("</think>"):]
        text = text.strip()

        # 2. Strip surrounding markdown fences if present
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Plan mode (streaming + two-phase)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, str)
    def request_plan(self, request_id: str, task_description: str, code: str, messages_json: str) -> None:
        rid = str(request_id or "")
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.plan_done.emit(rid, "No AI provider configured")
            return

        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.plan_done.emit(rid, "No model selected")
            return

        try:
            history: list[dict] = json.loads(messages_json) if messages_json else []
            if not isinstance(history, list):
                history = []
        except json.JSONDecodeError:
            history = []

        messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT_PLAN}]
        if code:
            messages.append({"role": "user", "content": f"Context code:\n```\n{code}\n```"})
            messages.append({"role": "assistant", "content": "I see the code. What would you like me to do?"})
        messages.extend(history)
        if task_description:
            messages.append({"role": "user", "content": str(task_description)})

        self._http.chat_completion_stream(
            cfg,
            model_id=model_id,
            messages=messages,
            system=_SYSTEM_PROMPT_PLAN,
            max_tokens=4096,
            on_chunk=lambda delta: self.plan_step_ready.emit(rid, delta),
            on_done=lambda _full, err: self.plan_done.emit(rid, str(err or "")),
        )

    # ------------------------------------------------------------------
    # Context usage snapshot (called from JS)
    # ------------------------------------------------------------------

    @QtCore.Slot(str)
    def update_code_context(self, code: str) -> None:
        """JS notifies us of current editor content so we can track token usage."""
        self._code_tokens = _approx_tokens(str(code or ""))
        self._emit_context_usage()

    @QtCore.Slot(str)
    def update_chat_context(self, messages_json: str) -> None:
        """JS notifies us of current chat history for token accounting."""
        try:
            messages = json.loads(messages_json)
        except (json.JSONDecodeError, TypeError):
            messages = []
        total = sum(_approx_tokens(str(m.get("content", ""))) for m in (messages if isinstance(messages, list) else []))
        self._chat_tokens = total
        self._emit_context_usage()

    def _emit_context_usage(self) -> None:
        used = self._system_tokens + self._code_tokens + self._chat_tokens
        total = self._max_context_tokens()
        self.context_usage_updated.emit(used, total)

    def _max_context_tokens(self) -> int:
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            return 128_000
        mid = self._chat_model_id or cfg.chat_model_id
        if mid:
            for m in cfg.cached_models:
                if m.model_id == mid:
                    return m.capabilities.max_context_tokens
        return 128_000

    # ------------------------------------------------------------------
    # Provider resolution helpers
    # ------------------------------------------------------------------

    def _chat_provider(self, *, for_inline: bool) -> ProviderConfig | None:
        pid = self._inline_provider_id if for_inline else self._chat_provider_id
        if not pid:
            # Fall back to first available provider
            providers = self._store.providers()
            return providers[0] if providers else None
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            providers = self._store.providers()
            return providers[0] if providers else None
        return cfg

    @staticmethod
    def _first_model_id(cfg: ProviderConfig) -> str:
        if cfg.cached_models:
            return cfg.cached_models[0].model_id
        return ""



    # ------------------------------------------------------------------
    # Context token helpers exposed to JS
    # ------------------------------------------------------------------

    @QtCore.Slot(result="QVariantMap")
    def get_context_breakdown(self) -> dict[str, Any]:
        return {
            "system_tokens": self._system_tokens,
            "code_tokens": self._code_tokens,
            "chat_tokens": self._chat_tokens,
            "used_tokens": self._system_tokens + self._code_tokens + self._chat_tokens,
            "total_tokens": self._max_context_tokens(),
        }

    def _update_context_tokens(self, messages: list[dict]) -> None:
        total = sum(_approx_tokens(str(m.get("content", ""))) for m in messages)
        self._chat_tokens = total
        self._emit_context_usage()
