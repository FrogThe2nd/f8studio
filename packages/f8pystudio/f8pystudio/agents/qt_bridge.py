from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qtpy import QtCore, QtGui  # type: ignore[import-not-found]

from f8pystudio.agents.conversations import StudioConversationStore, decode_conversation_messages
from f8pystudio.agents.graph_context import GraphContextSnapshot, format_graph_context_report
from f8pystudio.agents.codeact import StudioAgentSkillStatus
from f8pystudio.editor_assist.workspace import EditorAssistContext
from f8pystudio.ui.support.editor_assist_bridge import PythonEditorAssistBridge

from .prompts import (
    SYSTEM_PROMPT_CODE,
    SYSTEM_PROMPT_EDIT,
    SYSTEM_PROMPT_PLAN,
    approx_tokens,
    build_chat_messages,
    build_system_prompt,
    current_document_language,
    format_assist_context,
    strip_code_fence,
)
from .model_catalog import supports_agent_chat_model
from .registry import ProviderConfig, ProviderInferenceService
from .runtime import (
    AgentRequestMode,
    AgentRuntimeError,
    StudioAgentAttachment,
    StudioAgentEvent,
    StudioAgentRequest,
    StudioAgentRuntime,
)
from .sessions import StudioAgentSessionKey
from .state_store import AiPanelStateStore, MemoryAiPanelStateStore
from .store import AiProviderStore

logger = logging.getLogger(__name__)

_LSP_INLINE_BOUNDARY_ERRORS = (Exception,)


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


_AGENT_SESSION_DISABLED_SERVICES: frozenset[ProviderInferenceService] = frozenset(
    {"azure_openai_responses", "openai_responses"}
)


class AiLlmBridge(QtCore.QObject):
    inline_suggestion_ready = QtCore.Signal(str, str)
    chat_chunk_ready = QtCore.Signal(str, str)
    chat_done = QtCore.Signal(str, str)
    edit_result_ready = QtCore.Signal(str, str, str)
    plan_step_ready = QtCore.Signal(str, str)
    plan_done = QtCore.Signal(str, str)
    context_usage_updated = QtCore.Signal(int, int)
    chat_context_snapshot_changed = QtCore.Signal(bool, str)
    tool_trace_ready = QtCore.Signal(str, str)
    tool_approval_requested = QtCore.Signal(str, str)

    def __init__(
        self,
        store: AiProviderStore,
        state_store: AiPanelStateStore | None = None,
        conversation_store: StudioConversationStore | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._state_store = state_store or MemoryAiPanelStateStore()
        self._conversation_store = conversation_store or StudioConversationStore()
        self._runtime = StudioAgentRuntime(self._store, log_prompt_payload=self._log_prompt_payload)

        self._inline_provider_id = self._store.active_inline_provider
        self._inline_model_id = ""
        if self._inline_provider_id:
            inline_cfg = self._store.provider_by_id(self._inline_provider_id)
            if inline_cfg is not None:
                self._inline_model_id = inline_cfg.inline_model_id

        self._chat_provider_id = self._store.active_chat_provider
        self._chat_model_id = ""
        if self._chat_provider_id:
            chat_cfg = self._store.provider_by_id(self._chat_provider_id)
            if chat_cfg is not None:
                self._chat_model_id = chat_cfg.chat_model_id

        self._document_language = "plaintext"
        self._auto_chat_context_snapshot: GraphContextSnapshot | None = None
        self._active_conversation_id = ""
        self._agent_tools: tuple[Any, ...] = ()
        self._agent_codeact_context_providers: tuple[Any, ...] = ()
        self._agent_skill_statuses: tuple[StudioAgentSkillStatus, ...] = ()
        self._assist_context: EditorAssistContext | None = None
        self._lsp_bridge: PythonEditorAssistBridge | None = None
        self._tool_approval_resolver: Callable[[str, bool], None] | None = None
        self._active_stream_request_id = ""
        self._active_stream_lock = threading.Lock()
        self._tool_request_ids_by_thread: dict[int, str] = {}
        self._tool_request_ids_lock = threading.Lock()
        self._restored_agent_session_keys: set[str] = set()

        self._system_tokens = 0
        self._code_tokens = 0
        self._chat_tokens = 0
        self._last_code = ""
        self._last_messages: list[dict[str, Any]] = []
        self._debug_prompt = _env_flag("F8_AI_DEBUG_PROMPT")
        self._refresh_system_tokens()

    def set_lsp_bridge(self, bridge: PythonEditorAssistBridge | None) -> None:
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

    def set_auto_chat_context_snapshot(self, snapshot: GraphContextSnapshot | None) -> None:
        self._auto_chat_context_snapshot = snapshot
        node_name = snapshot.node_name if snapshot is not None else ""
        self._refresh_system_tokens()
        self.chat_context_snapshot_changed.emit(snapshot is not None, node_name)

    def set_agent_tools(self, tools: tuple[Any, ...]) -> None:
        self._agent_tools = tuple(tools)
        self._refresh_system_tokens()

    def set_agent_codeact_context_providers(self, context_providers: tuple[Any, ...]) -> None:
        self._agent_codeact_context_providers = tuple(context_providers)
        self._refresh_system_tokens()

    def set_agent_skill_statuses(self, statuses: tuple[StudioAgentSkillStatus, ...]) -> None:
        self._agent_skill_statuses = tuple(statuses)

    def agent_skill_statuses(self) -> tuple[StudioAgentSkillStatus, ...]:
        return self._agent_skill_statuses

    def set_tool_approval_resolver(self, resolver: Callable[[str, bool], None] | None) -> None:
        self._tool_approval_resolver = resolver

    def publish_tool_trace(self, payload: dict[str, Any]) -> None:
        self.tool_trace_ready.emit(self._resolve_tool_event_request_id(payload), _json_payload(payload))

    def publish_tool_approval(self, payload: dict[str, Any]) -> None:
        self.tool_approval_requested.emit(self._resolve_tool_event_request_id(payload), _json_payload(payload))

    @QtCore.Slot(str, bool)
    def resolve_tool_approval(self, approval_id: str, approved: bool) -> None:
        resolver = self._tool_approval_resolver
        if resolver is None:
            logger.warning("AI tool approval resolver is not configured approval_id=%s", approval_id)
            return
        try:
            resolver(str(approval_id or ""), bool(approved))
        except (RuntimeError, TypeError, ValueError):
            logger.exception("AI tool approval resolver failed approval_id=%s", approval_id)

    def _clear_chat_context_snapshot(self) -> None:
        self.set_auto_chat_context_snapshot(None)

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
        return format_assist_context(self._assist_context)

    def _current_document_language(self) -> str:
        return current_document_language(
            document_language=self._document_language,
            assist_context=self._assist_context,
        )

    def _get_system_prompt(self, base_prompt: str) -> str:
        return build_system_prompt(
            base_prompt,
            document_language=self._document_language,
            assist_context=self._assist_context,
            graph_context_snapshot=self._effective_chat_context_snapshot(),
            graph_tools_enabled=bool(self._tools_for_mode("chat")),
        )

    def _log_prompt_payload(self, mode: str, system_prompt: str, messages: list[dict[str, Any]]) -> None:
        if not self._debug_prompt:
            return
        try:
            payload = {
                "mode": str(mode or ""),
                "system_prompt": str(system_prompt or ""),
                "messages": messages,
                "context_block": self._format_assist_context(),
                "chat_context_block": format_graph_context_report(self._effective_chat_context_snapshot()),
            }
            logger.warning(
                "F8_AI_DEBUG_PROMPT payload:\n%s",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        except (TypeError, ValueError):
            logger.exception("Failed to dump F8_AI_DEBUG_PROMPT payload")

    @QtCore.Slot(str, str)
    def set_inline_model(self, provider_id: str, model_id: str) -> None:
        self._inline_provider_id = str(provider_id or "")
        self._inline_model_id = str(model_id or "")
        if self._inline_provider_id:
            cfg = self._store.provider_by_id(self._inline_provider_id)
            if cfg is not None:
                cfg.inline_model_id = self._inline_model_id
                self._store.save_provider(cfg, emit=False)
        self._store.save_active_providers(self._inline_provider_id, self._chat_provider_id)
        logger.debug("AI inline model: provider=%s model=%s", self._inline_provider_id, self._inline_model_id)

    @QtCore.Slot(str, str)
    def set_chat_model(self, provider_id: str, model_id: str) -> None:
        self._chat_provider_id = str(provider_id or "")
        self._chat_model_id = str(model_id or "")
        if self._chat_provider_id:
            cfg = self._store.provider_by_id(self._chat_provider_id)
            if cfg is not None:
                cfg.chat_model_id = self._chat_model_id
                self._store.save_provider(cfg, emit=False)
        self._store.save_active_providers(self._inline_provider_id, self._chat_provider_id)
        logger.debug("AI chat model: provider=%s model=%s", self._chat_provider_id, self._chat_model_id)

    @QtCore.Slot(str)
    def set_reasoning_level(self, level: str) -> None:
        cfg = self._store.provider_by_id(self._chat_provider_id)
        if cfg is not None:
            cfg.reasoning_level = str(level or "")
            self._store.save_provider(cfg, emit=False)

    @QtCore.Slot(str)
    def copy_to_clipboard(self, text: str) -> None:
        clipboard = QtGui.QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(text or ""), mode=QtGui.QClipboard.Mode.Clipboard)
            logger.debug("AI Bridge: copied %d chars to system clipboard", len(text))

    @QtCore.Slot()
    def reset_chat_history(self) -> None:
        logger.debug("AI chat history reset requested by user")
        self._clear_chat_context_snapshot()

    @QtCore.Slot(str, result="QVariantList")
    def list_conversations(self, scope: str = "graph") -> list[dict[str, Any]]:
        return [summary.to_dict() for summary in self._conversation_store.list_conversations(scope=str(scope or "graph"))]

    @QtCore.Slot(str, result="QVariantMap")
    def create_conversation(self, scope: str = "graph") -> dict[str, Any]:
        record = self._conversation_store.ensure_conversation(scope=str(scope or "graph"))
        self._active_conversation_id = record.conversation_id
        return record.to_dict()

    @QtCore.Slot(str, result="QVariantMap")
    def load_conversation(self, conversation_id: str) -> dict[str, Any]:
        record = self._conversation_store.get_conversation(str(conversation_id or ""))
        if record is None:
            return {}
        self._active_conversation_id = record.conversation_id
        return record.to_dict()

    @QtCore.Slot(str, result=bool)
    def delete_conversation(self, conversation_id: str) -> bool:
        deleted_id = str(conversation_id or "").strip()
        deleted = self._conversation_store.delete_conversation(deleted_id)
        if deleted and self._active_conversation_id == deleted_id:
            self._active_conversation_id = ""
        return deleted

    @QtCore.Slot(str, str, str, result="QVariantMap")
    def save_conversation_messages(self, conversation_id: str, scope: str, messages_json: str) -> dict[str, Any]:
        try:
            messages = decode_conversation_messages(str(messages_json or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.exception("Failed to decode AI conversation messages")
            return {}
        record = self._conversation_store.save_messages(
            str(conversation_id or ""),
            scope=str(scope or "graph"),
            messages=messages,
        )
        self._active_conversation_id = record.conversation_id
        self._last_messages = [message.to_dict() for message in messages]
        self._chat_tokens = sum(approx_tokens(message.content) for message in messages)
        self._emit_context_usage()
        return record.to_dict()

    @QtCore.Slot(str)
    def set_active_conversation(self, conversation_id: str) -> None:
        self._active_conversation_id = str(conversation_id or "").strip()

    @QtCore.Slot(str)
    def abort_request(self, request_id: str) -> None:
        self._runtime.abort_request(request_id)

    def abort_all_requests(self) -> None:
        self._runtime.abort_all_requests()

    @QtCore.Slot(result="QVariantList")
    def select_images(self) -> list[dict[str, str]]:
        from qtpy import QtWidgets

        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.parent(),  # type: ignore[arg-type]
            "Select Images",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        results: list[dict[str, str]] = []
        for path in files:
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError:
                logger.exception("Failed to read selected AI image path=%s", path)
                continue
            encoded = base64.b64encode(data).decode("utf-8")
            mime, _ = mimetypes.guess_type(path)
            results.append(
                {
                    "name": os.path.basename(path),
                    "content": encoded,
                    "mime": mime or "image/png",
                }
            )
        return results

    @QtCore.Slot(result="QVariantMap")
    def get_clipboard_image(self) -> dict[str, str]:
        clipboard = QtGui.QGuiApplication.clipboard()
        img = clipboard.image()
        if not img.isNull():
            ba = QtCore.QByteArray()
            buffer = QtCore.QBuffer(ba)
            buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            img.save(buffer, "PNG")
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
        self._state_store.set_value(str(key), value)

    @QtCore.Slot(str, "QVariant", result="QVariant")
    def get_ui_state(self, key: str, default: Any = None) -> Any:
        return self._state_store.get_value(str(key), default)

    @QtCore.Slot(str, str, str, int, int)
    def request_inline_suggestion(self, request_id: str, prefix: str, suffix: str, line: int, column: int) -> None:
        rid = str(request_id or "")
        if self._inline_provider_id == "lsp":
            self._request_lsp_inline_suggestion(rid=rid, line=int(line), column=int(column))
            return

        cfg = self._chat_provider(for_inline=True)
        if cfg is None:
            self.inline_suggestion_ready.emit(rid, "")
            return
        model_id = self._inline_model_id or cfg.inline_model_id or self._first_model_id(cfg)
        if not model_id:
            self.inline_suggestion_ready.emit(rid, "")
            return

        request = self._agent_request(
            request_id=rid,
            mode="inline",
            prefix=str(prefix or ""),
            suffix=str(suffix or ""),
            inline_model_id=model_id,
        )
        self._start_text_request(request, lambda text, err: self._on_inline_result(rid, text, err))

    def _request_lsp_inline_suggestion(self, *, rid: str, line: int, column: int) -> None:
        bridge = self._lsp_bridge
        if bridge is None:
            self.inline_suggestion_ready.emit(rid, "")
            return

        def _handle_lsp() -> None:
            try:
                items = bridge.inline_completion_items(
                    line=line,
                    column=column,
                    request_id="inline-" + rid,
                )
                text = ""
                if items:
                    first_item = items[0]
                    text = str(first_item.get("insertText") or first_item.get("label", ""))
                self.inline_suggestion_ready.emit(rid, text)
            except _LSP_INLINE_BOUNDARY_ERRORS:
                logger.exception("LSP inline suggestion failed request_id=%s line=%s column=%s", rid, line, column)
                self.inline_suggestion_ready.emit(rid, "")

        threading.Thread(target=_handle_lsp, daemon=True, name=f"f8-ai-lsp-inline-{rid}").start()

    def _on_inline_result(self, rid: str, text: str, err: str | None) -> None:
        if err:
            logger.debug("inline suggestion error request_id=%s error=%s", rid, err)
            self.inline_suggestion_ready.emit(rid, "")
            return
        self.inline_suggestion_ready.emit(rid, self._clean_inline_text(str(text or "")))

    @staticmethod
    def _clean_inline_text(text: str) -> str:
        lines = str(text or "").splitlines()
        if lines and lines[0].startswith("```"):
            lines.pop(0)
        if lines and lines[-1].strip() == "```":
            lines.pop()
        return "\n".join(lines)

    @QtCore.Slot(str, str, str, str, str)
    def request_chat(
        self,
        request_id: str,
        messages_json: str,
        code: str,
        selection: str,
        attachments_json: str = "",
    ) -> None:
        rid = str(request_id or "")
        history = self._history_from_json(messages_json, purpose="chat")
        attachments = self._attachments_from_json(attachments_json, purpose="chat")

        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.chat_done.emit(rid, "No AI provider configured")
            return
        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.chat_done.emit(rid, "No model selected")
            return

        system_prompt = self._get_system_prompt(SYSTEM_PROMPT_CODE)
        messages = self._build_chat_messages(history, code, selection, system_prompt, _attachments_to_dicts(attachments))
        self._update_context_tokens(messages)

        request = self._agent_request(
            request_id=rid,
            mode="chat",
            messages=tuple(history),
            code=code,
            selection=selection,
            attachments=tuple(attachments),
            chat_model_id=model_id,
        )
        self._restore_agent_session_for_request(request)
        self._start_stream_request(
            request,
            on_chunk=lambda text: self.chat_chunk_ready.emit(rid, text),
            on_done=lambda error: self.chat_done.emit(rid, error),
        )

    def _build_chat_messages(
        self,
        history: list[dict[str, Any]],
        code: str,
        selection: str,
        system_prompt: str,
        attachments: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        return build_chat_messages(
            history=history,
            code=str(code or ""),
            selection=str(selection or ""),
            system_prompt=system_prompt,
            document_language=self._current_document_language(),
            attachments=attachments,
        )

    @QtCore.Slot(str, str, str, str, str)
    def request_edit(
        self,
        request_id: str,
        code: str,
        instruction: str,
        messages_json: str,
        attachments_json: str = "",
    ) -> None:
        rid = str(request_id or "")
        history = self._history_from_json(messages_json, purpose="edit")
        attachments = self._attachments_from_json(attachments_json, purpose="edit")

        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.edit_result_ready.emit(rid, "", "No AI provider configured")
            return
        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.edit_result_ready.emit(rid, "", "No model selected")
            return

        request = self._agent_request(
            request_id=rid,
            mode="edit",
            messages=tuple(history),
            code=code,
            instruction=instruction,
            attachments=tuple(attachments),
            chat_model_id=model_id,
        )
        self._restore_agent_session_for_request(request)
        self._start_text_request(request, lambda text, err: self._on_edit_result(rid, text, err))

    def _on_edit_result(self, rid: str, text: str, err: str | None) -> None:
        if err:
            self.edit_result_ready.emit(rid, "", str(err))
            return
        self.edit_result_ready.emit(rid, self._strip_code_fence(str(text or "")), "")

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        return strip_code_fence(text)

    @QtCore.Slot(str, str, str, str, str)
    def request_plan(
        self,
        request_id: str,
        task_description: str,
        code: str,
        messages_json: str,
        attachments_json: str = "",
    ) -> None:
        rid = str(request_id or "")
        history = self._history_from_json(messages_json, purpose="plan")
        attachments = self._attachments_from_json(attachments_json, purpose="plan")

        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            self.plan_done.emit(rid, "No AI provider configured")
            return
        model_id = self._chat_model_id or cfg.chat_model_id or self._first_model_id(cfg)
        if not model_id:
            self.plan_done.emit(rid, "No model selected")
            return

        request = self._agent_request(
            request_id=rid,
            mode="plan",
            messages=tuple(history),
            code=code,
            task_description=task_description,
            attachments=tuple(attachments),
            chat_model_id=model_id,
        )
        self._restore_agent_session_for_request(request)
        self._start_stream_request(
            request,
            on_chunk=lambda text: self.plan_step_ready.emit(rid, text),
            on_done=lambda error: self.plan_done.emit(rid, error),
        )

    @QtCore.Slot(str)
    def update_code_context(self, code: str) -> None:
        self._last_code = str(code or "")
        self._code_tokens = approx_tokens(self._last_code)
        self._emit_context_usage()

    @QtCore.Slot(str)
    def update_chat_context(self, messages_json: str) -> None:
        self._last_messages = self._history_from_json(messages_json, purpose="context accounting")
        self._chat_tokens = sum(approx_tokens(str(message.get("content", ""))) for message in self._last_messages)
        self._emit_context_usage()

    def _emit_context_usage(self) -> None:
        used = self._system_tokens + self._code_tokens + self._chat_tokens
        total = self._max_context_tokens()
        self.context_usage_updated.emit(used, total)

    def _max_context_tokens(self) -> int:
        cfg = self._chat_provider(for_inline=False)
        if cfg is None:
            return 128_000
        model_id = self._chat_model_id or cfg.chat_model_id
        if model_id:
            for model in cfg.cached_models:
                if model.model_id == model_id:
                    return model.capabilities.max_context_tokens
        return 128_000

    def _chat_provider(self, *, for_inline: bool) -> ProviderConfig | None:
        provider_id = self._inline_provider_id if for_inline else self._chat_provider_id
        if provider_id:
            cfg = self._store.provider_by_id(provider_id)
            if cfg is not None:
                return cfg
        providers = self._store.providers()
        if providers:
            return providers[0]
        return None

    @staticmethod
    def _first_model_id(cfg: ProviderConfig) -> str:
        for model in cfg.cached_models:
            if supports_agent_chat_model(model):
                return model.model_id
        return ""

    @QtCore.Slot(result="QVariantMap")
    def get_context_breakdown(self) -> dict[str, Any]:
        return {
            "system_tokens": self._system_tokens,
            "code_tokens": self._code_tokens,
            "chat_tokens": self._chat_tokens,
            "used_tokens": self._system_tokens + self._code_tokens + self._chat_tokens,
            "total_tokens": self._max_context_tokens(),
        }

    def _update_context_tokens(self, messages: list[dict[str, Any]]) -> None:
        self._chat_tokens = sum(
            approx_tokens(str(message.get("content", "")))
            for message in messages
            if str(message.get("role", "")) != "system"
        )
        self._emit_context_usage()

    def _refresh_system_tokens(self) -> None:
        self._system_tokens = approx_tokens(self._get_system_prompt(SYSTEM_PROMPT_CODE))
        self._emit_context_usage()

    def _agent_request(
        self,
        *,
        request_id: str,
        mode: AgentRequestMode,
        messages: tuple[dict[str, Any], ...] = (),
        code: str = "",
        selection: str = "",
        instruction: str = "",
        task_description: str = "",
        prefix: str = "",
        suffix: str = "",
        attachments: tuple[StudioAgentAttachment, ...] = (),
        inline_model_id: str = "",
        chat_model_id: str = "",
    ) -> StudioAgentRequest:
        return StudioAgentRequest(
            request_id=str(request_id or ""),
            mode=mode,
            messages=messages,
            code=str(code or ""),
            selection=str(selection or ""),
            instruction=str(instruction or ""),
            task_description=str(task_description or ""),
            prefix=str(prefix or ""),
            suffix=str(suffix or ""),
            document_language=self._current_document_language(),
            assist_context=self._assist_context,
            graph_context_snapshot=self._effective_chat_context_snapshot(),
            attachments=attachments,
            tools=self._tools_for_mode(mode),
            context_providers=self._context_providers_for_mode(mode),
            session_key=self._session_key_for_mode(mode, self._active_conversation_id),
            inline_provider_id=self._inline_provider_id,
            inline_model_id=str(inline_model_id or self._inline_model_id or ""),
            chat_provider_id=self._chat_provider_id,
            chat_model_id=str(chat_model_id or self._chat_model_id or ""),
            reasoning_level=self.selection_state().reasoning_level,
        )

    def _effective_chat_context_snapshot(self) -> GraphContextSnapshot | None:
        return self._auto_chat_context_snapshot

    def _tools_for_mode(self, mode: AgentRequestMode) -> tuple[Any, ...]:
        if mode == "chat":
            return self._agent_tools
        return ()

    def _context_providers_for_mode(self, mode: AgentRequestMode) -> tuple[Any, ...]:
        if mode == "chat":
            return self._agent_codeact_context_providers
        return ()

    @staticmethod
    def _session_key_for_mode(mode: AgentRequestMode, conversation_id: str = "") -> StudioAgentSessionKey:
        if mode == "inline":
            return StudioAgentSessionKey.editor(editor_id="inline")
        return StudioAgentSessionKey.sidebar(conversation_id=str(conversation_id or ""))

    def _restore_agent_session_for_request(self, request: StudioAgentRequest) -> None:
        key = request.session_key
        if key is None:
            return
        provider = self._chat_provider(for_inline=request.mode == "inline")
        if provider is None or provider.inference_service in _AGENT_SESSION_DISABLED_SERVICES:
            return
        session_key = key.as_id()
        if session_key in self._restored_agent_session_keys:
            return
        record = self._conversation_store.get_conversation(key.conversation_id)
        if record is None or record.agent_session is None:
            return
        payload = dict(record.agent_session)
        if not _agent_session_payload_matches_request(payload, request=request, provider=provider):
            return
        state = payload.get("state")
        if not isinstance(state, dict):
            return
        restored = self._runtime.restore_session(key, state)
        if restored is not None:
            self._restored_agent_session_keys.add(session_key)

    def _save_agent_session_for_request(self, request: StudioAgentRequest) -> None:
        key = request.session_key
        if key is None:
            return
        provider = self._chat_provider(for_inline=request.mode == "inline")
        if provider is None or provider.inference_service in _AGENT_SESSION_DISABLED_SERVICES:
            return
        conversation_id = key.conversation_id
        if not conversation_id:
            return
        state = self._runtime.serialize_session(key)
        if state is None:
            return
        payload = _agent_session_payload_for_request(state, request=request, provider=provider)
        self._conversation_store.save_agent_session(
            conversation_id,
            scope="graph",
            agent_session=payload,
        )

    def _start_text_request(
        self,
        request: StudioAgentRequest,
        on_result: Callable[[str, str | None], None],
    ) -> None:
        def _worker() -> None:
            try:
                text = asyncio.run(self._runtime.run_text(request))
            except AgentRuntimeError as exc:
                logger.warning(
                    "Studio agent text request failed mode=%s request_id=%s error=%s",
                    request.mode,
                    request.request_id,
                    exc,
                )
                on_result("", str(exc))
                return
            except Exception as exc:
                logger.exception("Studio agent text request crashed mode=%s request_id=%s", request.mode, request.request_id)
                on_result("", f"{type(exc).__name__}: {exc}")
                return
            self._save_agent_session_for_request(request)
            on_result(text, None)

        threading.Thread(target=_worker, daemon=True, name=f"f8-agent-text-{request.request_id}").start()

    def _start_stream_request(
        self,
        request: StudioAgentRequest,
        *,
        on_chunk: Callable[[str], None],
        on_done: Callable[[str], None],
    ) -> None:
        async def _consume() -> None:
            terminal_event_seen = False
            async for event in self._runtime.run_stream(request):
                if event.kind == "chunk":
                    on_chunk(event.text)
                elif event.kind == "error":
                    terminal_event_seen = True
                    on_done(event.error)
                    return
                elif event.kind == "done":
                    terminal_event_seen = True
                    self._save_agent_session_for_request(request)
                    on_done("")
                    return
            if not terminal_event_seen:
                on_done("")

        def _worker() -> None:
            self._set_current_tool_request_id(request.request_id)
            try:
                self._set_active_stream_request_id(request.request_id)
                asyncio.run(_consume())
            except Exception as exc:
                logger.exception("Studio agent stream request crashed mode=%s request_id=%s", request.mode, request.request_id)
                on_done(f"{type(exc).__name__}: {exc}")
            finally:
                self._clear_current_tool_request_id()
                self._clear_active_stream_request_id(request.request_id)

        threading.Thread(target=_worker, daemon=True, name=f"f8-agent-stream-{request.request_id}").start()

    def _resolve_tool_event_request_id(self, payload: dict[str, Any]) -> str:
        payload_request_id = str(payload.get("requestId") or "").strip()
        if payload_request_id:
            return payload_request_id
        thread_id = threading.get_ident()
        with self._tool_request_ids_lock:
            thread_request_id = str(self._tool_request_ids_by_thread.get(thread_id, "") or "").strip()
        if thread_request_id:
            return thread_request_id
        with self._active_stream_lock:
            return self._active_stream_request_id

    def _set_current_tool_request_id(self, request_id: str) -> None:
        thread_id = threading.get_ident()
        with self._tool_request_ids_lock:
            self._tool_request_ids_by_thread[thread_id] = str(request_id or "")

    def _clear_current_tool_request_id(self) -> None:
        thread_id = threading.get_ident()
        with self._tool_request_ids_lock:
            self._tool_request_ids_by_thread.pop(thread_id, None)

    def _set_active_stream_request_id(self, request_id: str) -> None:
        with self._active_stream_lock:
            self._active_stream_request_id = str(request_id or "")

    def _clear_active_stream_request_id(self, request_id: str) -> None:
        with self._active_stream_lock:
            if self._active_stream_request_id == str(request_id or ""):
                self._active_stream_request_id = ""

    def _history_from_json(self, raw: str, *, purpose: str) -> list[dict[str, Any]]:
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid AI %s JSON payload", purpose)
            return []
        if not isinstance(payload, list):
            logger.warning("Ignoring AI %s payload because it is not a JSON list", purpose)
            return []
        history: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                history.append(dict(item))
            else:
                logger.warning("Ignoring non-object AI %s message item: %r", purpose, item)
        return history

    def _attachments_from_json(self, raw: str, *, purpose: str) -> list[StudioAgentAttachment]:
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid AI %s attachments JSON payload", purpose)
            return []
        if not isinstance(payload, list):
            logger.warning("Ignoring AI %s attachments because payload is not a JSON list", purpose)
            return []
        attachments: list[StudioAgentAttachment] = []
        for item in payload:
            if not isinstance(item, dict):
                logger.warning("Ignoring non-object AI %s attachment item: %r", purpose, item)
                continue
            attachments.append(
                StudioAgentAttachment(
                    name=str(item.get("name", "")),
                    content=str(item.get("content", "")),
                    mime=str(item.get("mime", "image/png")),
                )
            )
        return attachments


def _attachments_to_dicts(attachments: list[StudioAgentAttachment]) -> list[dict[str, str]]:
    return [{"name": item.name, "content": item.content, "mime": item.mime} for item in attachments]


def _json_payload(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        logger.exception("Failed to encode AI bridge payload")
        return "{}"


def _agent_session_payload_for_request(
    state: dict[str, Any],
    *,
    request: StudioAgentRequest,
    provider: ProviderConfig,
) -> dict[str, Any]:
    return {
        "schemaVersion": "f8studio-maf-agent-session/1",
        "providerId": provider.provider_id,
        "inferenceService": provider.inference_service,
        "endpoint": provider.endpoint,
        "modelId": request.inline_model_id if request.mode == "inline" else request.chat_model_id,
        "state": dict(state),
    }


def _agent_session_payload_matches_request(
    payload: dict[str, Any],
    *,
    request: StudioAgentRequest,
    provider: ProviderConfig,
) -> bool:
    model_id = request.inline_model_id if request.mode == "inline" else request.chat_model_id
    return (
        str(payload.get("schemaVersion") or "") == "f8studio-maf-agent-session/1"
        and str(payload.get("providerId") or "") == provider.provider_id
        and str(payload.get("inferenceService") or "") == provider.inference_service
        and str(payload.get("endpoint") or "") == provider.endpoint
        and str(payload.get("modelId") or "") == model_id
    )
