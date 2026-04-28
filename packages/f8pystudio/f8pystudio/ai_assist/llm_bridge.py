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

from .graph_context import GraphContextSnapshot, format_graph_context_report, format_graph_context_snapshot
from .http_client import AiHttpClient
from .registry import ModelInfo, ProviderConfig
from .state_store import AiPanelStateStore, MemoryAiPanelStateStore
from .store import AiProviderStore
from f8pystudio.ui.support.editor_assist_bridge import PythonEditorAssistBridge
from ..editor_assist.workspace import EditorAssistContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_CODE = (
    "You are an expert assistant embedded in an IDE. "
    "You help the user write, refactor, and debug code or structured documents. "
    "Be concise and precise. When providing code, use proper syntax. "
    "When providing explanations, be brief."
)

_SYSTEM_PROMPT_EDIT = (
    "You are a document editing assistant. "
    "The user will provide the current document and an instruction. "
    "Return ONLY the complete rewritten document — no explanation, no markdown fences, no comments about changes."
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
    chat_context_snapshot_changed = QtCore.Signal(bool, str)  # has_context, node_name

    def __init__(
        self,
        store: AiProviderStore,
        state_store: AiPanelStateStore | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._state_store = state_store or MemoryAiPanelStateStore()
        self._http = AiHttpClient(self)

        # Active provider/model selections (changeable from quick panel)
        self._inline_provider_id: str = ""
        self._inline_model_id: str = ""
        self._chat_provider_id: str = ""
        self._chat_model_id: str = ""
        self._document_language: str = "plaintext"
        self._chat_context_snapshot: GraphContextSnapshot | None = None
        self._chat_context_summary: str = ""

        # Context tracking
        self._system_tokens: int = 0
        self._code_tokens: int = 0
        self._chat_tokens: int = 0
        
        # Debug tracking (holds last seen data for inspection)
        self._last_code: str = ""
        self._last_messages: list[dict] = []

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
        self._refresh_system_tokens()

    def set_lsp_bridge(self, bridge: PythonEditorAssistBridge | None) -> None:
        """Inject the Python LSP bridge so we can route completions to it."""
        self._lsp_bridge = bridge

    def set_assist_context(self, context: EditorAssistContext | None) -> None:
        self._assist_context = context
        if context is not None:
            language = str(context.language or "").strip().lower()
            if language:
                self._document_language = language
        self._refresh_system_tokens()

    def set_document_language(self, language: str) -> None:
        self._document_language = str(language or "plaintext").strip().lower() or "plaintext"
        self._refresh_system_tokens()

    def set_chat_context_snapshot(self, snapshot: GraphContextSnapshot | None) -> None:
        self._chat_context_snapshot = snapshot
        self._chat_context_summary = format_graph_context_snapshot(snapshot)
        node_name = snapshot.node_name if snapshot is not None else ""
        self._refresh_system_tokens()
        self.chat_context_snapshot_changed.emit(snapshot is not None, node_name)

    @QtCore.Slot()
    def clear_chat_context_snapshot(self) -> None:
        self.set_chat_context_snapshot(None)

    @QtCore.Slot(result=str)
    def get_chat_context_report(self) -> str:
        return format_graph_context_report(self._chat_context_snapshot)

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
        target_lines = []
        if ctx.language:
            target_lines.append(f"- Document language: `{ctx.language}`")
        if ctx.target_field_kind:
            target_lines.append(f"- Target kind: `{ctx.target_field_kind}`")
        if ctx.target_field_name:
            target_lines.append(f"- Target field: `{ctx.target_field_name}`")
        if ctx.target_field_label:
            target_lines.append(f"- Target label: {ctx.target_field_label}")
        if ctx.target_ui_language and ctx.target_ui_language != ctx.language:
            target_lines.append(f"- Target UI language: `{ctx.target_ui_language}`")
        if ctx.target_field_description:
            target_lines.append(f"- Target description: {ctx.target_field_description}")
        if ctx.target_value_schema:
            target_lines.append(f"- Target schema: `{_schema_summary(ctx.target_value_schema)}`")
        if target_lines:
            lines.append("## Editing Target")
            lines.extend(target_lines)
        meta_lines = []
        if ctx.node_kind:
            meta_lines.append(f"- Kind: `{ctx.node_kind}`")
        if ctx.service_class:
            meta_lines.append(f"- Service: `{ctx.service_class}`")
        if ctx.operator_class:
            meta_lines.append(f"- Operator: `{ctx.operator_class}`")
        if ctx.node_description:
            meta_lines.append(f"- Type Description: {ctx.node_description}")
        if ctx.node_instance_purpose:
            meta_lines.append(f"- Instance Purpose: {ctx.node_instance_purpose}")
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

    def _current_document_language(self) -> str:
        if self._assist_context is not None:
            language = str(self._assist_context.language or "").strip().lower()
            if language:
                return language
        return str(self._document_language or "plaintext").strip().lower() or "plaintext"

    def _get_system_prompt(self, base_prompt: str) -> str:
        document_language = self._current_document_language()
        language_guidance = ""
        if document_language == "json":
            language_guidance = (
                "You are editing a JSON document. "
                "When generating or rewriting document content, return valid JSON for this document unless the user explicitly asks for another language. "
                "Do not default to Python code for JSON-authoring requests."
            )
        elif document_language not in {"", "plaintext"}:
            language_guidance = (
                f"You are editing a {document_language} document. "
                f"Prefer {document_language} syntax when writing or rewriting the document unless the user explicitly asks for another format."
            )
        ctx_str = self._format_assist_context()
        blocks = [base_prompt]
        if language_guidance:
            blocks.append(language_guidance)
        if ctx_str:
            blocks.append(ctx_str)
        if self._chat_context_summary:
            blocks.append(self._chat_context_summary)
        return "\n\n".join(blocks)

    def _log_prompt_payload(self, *, mode: str, system_prompt: str, messages: list[dict]) -> None:
        if not self._debug_prompt:
            return
        try:
            payload = {
                "mode": str(mode or ""),
                "system_prompt": str(system_prompt or ""),
                "messages": messages,
                "context_block": self._format_assist_context(),
                "chat_context_block": self._chat_context_summary,
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
        logger.debug("AI chat history reset requested by user")
        # In current design, history is held by JS, so this is mainly a signal
        # for backend to clear any ephemeral cached context if it had any.
        self.clear_chat_context_snapshot()

    @QtCore.Slot(str)
    def abort_request(self, request_id: str) -> None:
        """Called from JS to abort an in-flight LLM request."""
        self._http.abort_request(request_id)

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
        self._state_store.set_value(str(key), value)

    @QtCore.Slot(str, "QVariant", result="QVariant")
    def get_ui_state(self, key: str, default: Any = None) -> Any:
        """Load persistent UI state (called from JS)."""
        return self._state_store.get_value(str(key), default)

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
            request_id=rid,
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
            request_id=rid,
        )

    def _build_chat_messages(
        self,
        history: list[dict],
        code: str,
        selection: str,
        system_prompt: str,
        attachments: list[dict[str, str]] | None = None
    ) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        document_language = self._current_document_language()
        fence_language = "" if document_language in {"", "plaintext"} else document_language
        if code:
            context_content = f"Current editor content ({document_language}):\n```{fence_language}\n{code}\n```"
            if selection:
                context_content += f"\n\nSelected text:\n```{fence_language}\n{selection}\n```"
            messages.append({"role": "user", "content": context_content})
            messages.append({"role": "assistant", "content": "I can see the current document. How can I help?"})
        
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
        document_language = self._current_document_language()
        fence_language = "" if document_language in {"", "plaintext"} else document_language
        
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
            f"Current {document_language} document:\n```{fence_language}\n{code}\n```"
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
            request_id=rid,
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
        document_language = self._current_document_language()
        fence_language = "" if document_language in {"", "plaintext"} else document_language
        if code:
            messages.append(
                {
                    "role": "user",
                    "content": f"Current {document_language} document:\n```{fence_language}\n{code}\n```",
                }
            )
            messages.append({"role": "assistant", "content": "I can see the current document. What would you like me to do?"})
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
            request_id=rid,
        )

    # ------------------------------------------------------------------
    # Context usage snapshot (called from JS)
    # ------------------------------------------------------------------

    @QtCore.Slot(str)
    def update_code_context(self, code: str) -> None:
        """JS notifies us of current editor content so we can track token usage."""
        self._last_code = str(code or "")
        self._code_tokens = _approx_tokens(self._last_code)
        self._emit_context_usage()

    @QtCore.Slot(str)
    def update_chat_context(self, messages_json: str) -> None:
        """JS notifies us of current chat history for token accounting."""
        try:
            self._last_messages = json.loads(messages_json)
            if not isinstance(self._last_messages, list):
                self._last_messages = []
        except (json.JSONDecodeError, TypeError):
            self._last_messages = []
        total = sum(_approx_tokens(str(m.get("content", ""))) for m in self._last_messages)
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
        total = sum(
            _approx_tokens(str(message.get("content", "")))
            for message in messages
            if str(message.get("role", "")) != "system"
        )
        self._chat_tokens = total
        self._emit_context_usage()

    def _refresh_system_tokens(self) -> None:
        self._system_tokens = _approx_tokens(self._get_system_prompt(_SYSTEM_PROMPT_CODE))
        self._emit_context_usage()

    @QtCore.Slot(result=str)
    def get_context_report(self) -> str:
        """Returns a formatted Markdown report of the current full payload."""
        system_prompt = self._get_system_prompt(_SYSTEM_PROMPT_CODE)
        
        # We simulate what _build_chat_messages would produce
        messages = self._build_chat_messages(self._last_messages, self._last_code, "", system_prompt)
        
        lines = ["# AI Context Payload Report", ""]
        lines.append(f"- **Total Tokens (Approx):** {self._system_tokens + self._code_tokens + self._chat_tokens}")
        lines.append(f"- **Chat Messages:** {len(self._last_messages)}")
        lines.append("")
        lines.append("## Pinned Graph Context")
        if self._chat_context_snapshot is None:
            lines.append("_No pinned graph context._")
        else:
            lines.append(format_graph_context_snapshot(self._chat_context_snapshot))
        lines.append("")
        
        for i, msg in enumerate(messages):
            role = str(msg.get("role", "unknown")).upper()
            content = msg.get("content", "")
            lines.append(f"### [{i}] {role}")
            if isinstance(content, list):
                # Handle multi-modal content lists
                for part in content:
                    if part.get("type") == "text":
                        lines.append(part.get("text", ""))
                    else:
                        lines.append(f"*[{part.get('type')} attachment]*")
            else:
                lines.append(str(content))
            lines.append("")
        
        return "\n".join(lines)
