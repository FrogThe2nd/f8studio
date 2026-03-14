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
import os
from dataclasses import dataclass
from typing import Any

from qtpy import QtCore, QtGui  # type: ignore[import-not-found]

from .http_client import AiHttpClient
from .registry import ModelInfo, ProviderConfig
from .store import AiProviderStore
from .ui_state import load_ai_panel_state, save_ai_panel_state
from ..editor_assist.bridge import PythonEditorAssistBridge
from ..editor_assist.workspace import EditorAssistContext

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


def _schema_summary(schema_obj: dict[str, Any] | None) -> str:
    if not isinstance(schema_obj, dict):
        return "Any"
    schema_type = str(schema_obj.get("type") or "any").strip().lower()
    if schema_type == "object":
        properties = schema_obj.get("properties")
        if isinstance(properties, dict) and properties:
            keys = ", ".join(str(key) for key in properties.keys())
            return f"object<{keys}>"
        return "object"
    if schema_type == "array":
        items = schema_obj.get("items")
        if isinstance(items, dict):
            item_type = str(items.get("type") or "any").strip().lower() or "any"
            return f"array<{item_type}>"
        return "array"
    return schema_type or "Any"


def _env_flag(name: str) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "debug"}


@dataclass(frozen=True)
class AiBridgeSelectionState:
    inline_provider_id: str
    inline_model_id: str
    chat_provider_id: str
    chat_model_id: str
    reasoning_level: str


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

        self._assist_context: EditorAssistContext | None = None
        self._lsp_bridge: PythonEditorAssistBridge | None = None
        self._debug_prompt = _env_flag("F8_AI_DEBUG_PROMPT")

    def set_lsp_bridge(self, bridge: PythonEditorAssistBridge | None) -> None:
        """Inject the Python LSP bridge so we can route completions to it."""
        self._lsp_bridge = bridge

    def set_assist_context(self, context: EditorAssistContext | None) -> None:
        self._assist_context = context

    def selection_state(self) -> AiBridgeSelectionState:
        cfg = self._store.provider_by_id(self._chat_provider_id)
        reasoning_level = ""
        if cfg is not None:
            reasoning_level = str(cfg.reasoning_level or "")
        return AiBridgeSelectionState(
            inline_provider_id=self._inline_provider_id,
            inline_model_id=self._inline_model_id,
            chat_provider_id=self._chat_provider_id,
            chat_model_id=self._chat_model_id,
            reasoning_level=reasoning_level,
        )

    def _format_assist_context(self) -> str:
        if not self._assist_context:
            return ""
        ctx = self._assist_context
        lines = []
        meta_lines = []
        if ctx.node_kind:
            meta_lines.append(f"- Kind: `{ctx.node_kind}`")
        if ctx.service_class:
            meta_lines.append(f"- Service: `{ctx.service_class}`")
        if ctx.operator_class:
            meta_lines.append(f"- Operator: `{ctx.operator_class}`")
        if ctx.node_description:
            meta_lines.append(f"- Description: {ctx.node_description}")
        if meta_lines:
            lines.append("## Node Metadata")
            lines.extend(meta_lines)
        if ctx.data_in_ports:
            lines.append("## Input Ports (`dataInPorts`)")
            for p in ctx.data_in_ports:
                req = "required" if p.required else "optional"
                schema_str = _schema_summary(p.value_schema)
                desc = f" | description={p.description}" if p.description else ""
                lines.append(f"- `{p.name}` ({req}, schema={schema_str}){desc}")
        if ctx.data_out_ports:
            lines.append("## Output Ports (`dataOutPorts`)")
            for p in ctx.data_out_ports:
                req = "required" if p.required else "optional"
                schema_str = _schema_summary(p.value_schema)
                desc = f" | description={p.description}" if p.description else ""
                lines.append(f"- `{p.name}` ({req}, schema={schema_str}){desc}")
        if ctx.state_fields:
            lines.append("## State Fields (`stateFields`)")
            for f in ctx.state_fields:
                req = "required" if f.required else "optional"
                schema_str = _schema_summary(f.value_schema)
                desc = f" | description={f.description}" if f.description else ""
                lines.append(f"- `{f.name}` ({req}, access={f.access}, schema={schema_str}){desc}")
        if not lines:
            return ""
        
        out = ["\n# Current Node / Component Structure"]
        out.extend(lines)
        out.append("\n*Note: Use the above node inputs and states context to guide your logic and typing.*")
        return "\n".join(out)

    def _get_system_prompt(self, base_prompt: str) -> str:
        ctx_str = self._format_assist_context()
        if ctx_str:
            return f"{base_prompt}\n\n{ctx_str}"
        return base_prompt

    def _log_prompt_payload(self, *, mode: str, system_prompt: str, messages: list[dict]) -> None:
        if not self._debug_prompt:
            return
        try:
            payload = {
                "mode": str(mode or ""),
                "system_prompt": str(system_prompt or ""),
                "messages": messages,
                "context_block": self._format_assist_context(),
            }
            logger.warning(
                "F8_AI_DEBUG_PROMPT payload:\n%s",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        except (TypeError, ValueError):
            logger.exception("Failed to dump F8_AI_DEBUG_PROMPT payload")

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

    @QtCore.Slot(str)
    def copy_to_clipboard(self, text: str) -> None:
        """Robust clipboard copy for chat code blocks, bypassing JS limitations."""
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(str(text or ""), mode=QtGui.QClipboard.Mode.Clipboard)
            logger.debug("AI Bridge: copied %d chars to system clipboard", len(text))

    @QtCore.Slot()
    def reset_chat_history(self) -> None:
        """Called when user clicks the reset button in UI."""
        logger.info("AI chat history reset requested by user")
        # In current design, history is held by JS, so this is mainly a signal
        # for backend to clear any ephemeral cached context if it had any.

    @QtCore.Slot(result="QVariantList")
    def select_images(self) -> list[dict[str, str]]:
        """Open file dialog to select images and return base64 encoded content."""
        from qtpy import QtWidgets
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.parent(),  # type: ignore
            "Select Images",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        results = []
        for path in files:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                import base64
                import mimetypes
                encoded = base64.b64encode(data).decode("utf-8")
                mime, _ = mimetypes.guess_type(path)
                results.append({
                    "name": os.path.basename(path),
                    "content": encoded,
                    "mime": mime or "image/png",
                })
            except Exception:
                logger.exception("Failed to read image: %s", path)
        return results

    @QtCore.Slot(result="QVariantMap")
    def get_clipboard_image(self) -> dict[str, str]:
        """Try to get an image from the system clipboard (Native Qt)."""
        clipboard = QtGui.QGuiApplication.clipboard()
        img = clipboard.image()
        if not img.isNull():
            ba = QtCore.QByteArray()
            buffer = QtCore.QBuffer(ba)
            buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            img.save(buffer, "PNG")
            import base64
            encoded = base64.b64encode(ba.data()).decode("utf-8")
            return {
                "name": "clipboard_image.png",
                "content": encoded,
                "mime": "image/png",
            }
        return {
            "name": "",
            "content": "",
            "mime": "",
        }

    @QtCore.Slot(str, "QVariant")
    def set_ui_state(self, key: str, value: Any) -> None:
        """Persist UI state (called from JS)."""
        save_ai_panel_state(str(key), value)

    @QtCore.Slot(str, "QVariant", result="QVariant")
    def get_ui_state(self, key: str, default: Any = None) -> Any:
        """Load persistent UI state (called from JS)."""
        return load_ai_panel_state(str(key), default)

    # ------------------------------------------------------------------
    # Inline suggestion (FIM)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, int, int)
    def request_inline_suggestion(self, request_id: str, prefix: str, suffix: str, line: int, column: int) -> None:
        rid = str(request_id or "")

        # 1. Check if LSP is selected
        if self._inline_provider_id == "lsp":
            if self._lsp_bridge:
                # We reuse the sync logic if possible, or just request completions directly
                # However, PythonEditorAssistBridge.request_completions returns its result via signals.
                # Here we need a more direct way or wait for it.
                # Actually, we can just call _completion_items which is internal but available on bridge.
                # IT NEEDS TO RUN IN A THREAD if we want to avoid blocking, but the bridge usually handles its own executor.
                # But request_inline_suggestion is expected to be async (emits signal later).
                
                # We'll use the executor to avoid blocking the main thread
                def _handle_lsp():
                    try:
                        items = self._lsp_bridge.inline_completion_items(
                            line=int(line),
                            column=int(column),
                            request_id="inline-" + rid,
                        )
                        text = ""
                        if items:
                            # Inline suggestions (ghost text) usually want just the tail, 
                            # monaco-editor-dialog.py JS filter handles prefix matching.
                            # But FIM usually returns exactly what should be inserted.
                            text = items[0].get("insertText") or items[0].get("label", "")
                        
                        QtCore.QTimer.singleShot(0, lambda: self.inline_suggestion_ready.emit(rid, text))
                    except Exception:
                        logger.exception("LSP inline suggestion failed")
                        QtCore.QTimer.singleShot(0, lambda: self.inline_suggestion_ready.emit(rid, ""))

                import threading
                threading.Thread(target=_handle_lsp, daemon=True).start()
                return
            else:
                self.inline_suggestion_ready.emit(rid, "")
                return

        # 2. Otherwise route to LLM
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
            lines.pop(0)
        if lines and lines[-1].strip() == "```":
            lines.pop()
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Chat (streaming)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, str, str)
    def request_chat(self, request_id: str, messages_json: str, code: str, selection: str, attachments_json: str = "") -> None:
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

        try:
            attachments: list[dict[str, str]] = json.loads(attachments_json) if attachments_json else []
            if not isinstance(attachments, list):
                attachments = []
        except json.JSONDecodeError:
            attachments = []

        system_prompt = self._get_system_prompt(_SYSTEM_PROMPT_CODE)
        messages = self._build_chat_messages(history, code, selection, system_prompt, attachments)
        self._log_prompt_payload(mode="chat", system_prompt=system_prompt, messages=messages)
        self._update_context_tokens(messages)

        self._http.chat_completion_stream(
            cfg,
            model_id=model_id,
            messages=messages,
            system=system_prompt,
            max_tokens=4096,
            on_chunk=lambda delta: self.chat_chunk_ready.emit(rid, delta),
            on_done=lambda _full, err: self.chat_done.emit(rid, str(err or "")),
        )

    @staticmethod
    def _build_chat_messages(
        history: list[dict],
        code: str,
        selection: str,
        system_prompt: str,
        attachments: list[dict[str, str]] | None = None
    ) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if code:
            context_content = f"Current file:\n```\n{code}\n```"
            if selection:
                context_content += f"\n\nSelected text:\n```\n{selection}\n```"
            messages.append({"role": "user", "content": context_content})
            messages.append({"role": "assistant", "content": "I can see your code. How can I help?"})
        
        # Process history
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                continue
            messages.append({"role": role, "content": content})

        # Add attachments to the LAST message if it's from the user
        if attachments and messages:
            last_msg = messages[-1]
            if last_msg["role"] == "user":
                text_content = last_msg["content"]
                if isinstance(text_content, str):
                    new_content: list[dict] = [{"type": "text", "text": text_content}]
                    for att in attachments:
                        new_content.append({
                            "type": "image",
                            "image": att["content"],
                            "mime_type": att["mime"]
                        })
                    last_msg["content"] = new_content
        
        return messages

    # ------------------------------------------------------------------
    # Edit mode (non-streaming → diff view)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, str, str)
    def request_edit(self, request_id: str, code: str, instruction: str, messages_json: str, attachments_json: str = "") -> None:
        rid = str(request_id or "")
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.edit_result_ready.emit(rid, "", "No AI provider configured")
            return

        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.edit_result_ready.emit(rid, "", "No model selected")
            return

        try:
            attachments: list[dict[str, str]] = json.loads(attachments_json) if attachments_json else []
            if not isinstance(attachments, list):
                attachments = []
        except json.JSONDecodeError:
            attachments = []

        system_prompt = self._get_system_prompt(_SYSTEM_PROMPT_EDIT)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        
        try:
            history = json.loads(messages_json) if messages_json else []
            if isinstance(history, list):
                # Filter out system prompts from history
                history = [m for m in history if m.get("role") != "system"]
                messages.extend(history)
        except json.JSONDecodeError:
            pass
            
        user_content: Any = (
            f"Instruction: {instruction}\n\n"
            f"Code:\n```\n{code}\n```"
        )
        if attachments:
            parts = [{"type": "text", "text": user_content}]
            for att in attachments:
                parts.append({
                    "type": "image",
                    "image": att["content"],
                    "mime_type": att["mime"]
                })
            user_content = parts

        messages.append({
            "role": "user",
            "content": user_content,
        })
        self._log_prompt_payload(mode="edit", system_prompt=system_prompt, messages=messages)

        self._http.chat_completion(
            cfg,
            model_id=model_id,
            messages=messages,
            system=system_prompt,
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
            text = text[think_end + len("</think>"):]  # type: ignore[index,arg-type]
        text = text.strip()

        # 2. Strip surrounding markdown fences if present
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines.pop(0)
        if lines and lines[-1].strip() == "```":
            lines.pop()
        
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Plan mode (streaming + two-phase)
    # ------------------------------------------------------------------

    @QtCore.Slot(str, str, str, str, str)
    def request_plan(self, request_id: str, task_description: str, code: str, messages_json: str, attachments_json: str = "") -> None:
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

        try:
            attachments: list[dict[str, str]] = json.loads(attachments_json) if attachments_json else []
            if not isinstance(attachments, list):
                attachments = []
        except json.JSONDecodeError:
            attachments = []

        system_prompt = self._get_system_prompt(_SYSTEM_PROMPT_PLAN)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if code:
            messages.append({"role": "user", "content": f"Context code:\n```\n{code}\n```"})
            messages.append({"role": "assistant", "content": "I see the code. What would you like me to do?"})
        messages.extend(history)
        
        user_content: Any = str(task_description or "")
        if attachments:
            parts = [{"type": "text", "text": user_content}]
            for att in attachments:
                parts.append({
                    "type": "image",
                    "image": att["content"],
                    "mime_type": att["mime"]
                })
            user_content = parts

        if user_content:
            messages.append({"role": "user", "content": user_content})
        self._log_prompt_payload(mode="plan", system_prompt=system_prompt, messages=messages)

        self._http.chat_completion_stream(
            cfg,
            model_id=model_id,
            messages=messages,
            system=system_prompt,
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
