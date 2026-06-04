from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Literal

from f8pystudio.agents.graph_context import GraphContextSnapshot
from f8pystudio.editor_assist.workspace import EditorAssistContext

from .clients import (
    AgentClientSelection,
    AgentFrameworkImportError,
    build_chat_client,
    effective_chat_model_id,
    effective_inline_model_id,
)
from .prompts import (
    SYSTEM_PROMPT_CODE,
    SYSTEM_PROMPT_EDIT,
    SYSTEM_PROMPT_PLAN,
    build_chat_messages,
    build_edit_messages,
    build_plan_messages,
    build_system_prompt,
    current_document_language,
    strip_code_fence,
)
from .registry import ProviderConfig
from .sessions import StudioAgentSessionKey, StudioAgentSessionRegistry
from .store import AiProviderStore

logger = logging.getLogger(__name__)

AgentRequestMode = Literal["chat", "edit", "plan", "inline"]


class AgentRuntimeError(RuntimeError):
    pass


class AgentRuntimeUnavailableError(AgentRuntimeError):
    pass


@dataclass(frozen=True)
class StudioAgentAttachment:
    name: str
    content: str
    mime: str


@dataclass(frozen=True)
class StudioAgentRequest:
    request_id: str
    mode: AgentRequestMode
    messages: tuple[dict[str, Any], ...] = ()
    code: str = ""
    selection: str = ""
    instruction: str = ""
    task_description: str = ""
    prefix: str = ""
    suffix: str = ""
    document_language: str = "plaintext"
    assist_context: EditorAssistContext | None = None
    graph_context_snapshot: GraphContextSnapshot | None = None
    attachments: tuple[StudioAgentAttachment, ...] = ()
    tools: tuple[Any, ...] = ()
    session_key: StudioAgentSessionKey | None = None
    inline_provider_id: str = ""
    inline_model_id: str = ""
    chat_provider_id: str = ""
    chat_model_id: str = ""
    reasoning_level: str = ""


@dataclass(frozen=True)
class StudioAgentEvent:
    kind: Literal["chunk", "done", "error"]
    text: str = ""
    error: str = ""


class StudioAgentRuntime:
    def __init__(
        self,
        store: AiProviderStore,
        *,
        log_prompt_payload: Callable[[str, str, list[dict[str, Any]]], None] | None = None,
        session_registry: StudioAgentSessionRegistry | None = None,
    ) -> None:
        self._store = store
        self._log_prompt_payload = log_prompt_payload
        self._session_registry = session_registry or StudioAgentSessionRegistry()
        self._abort_events: dict[str, asyncio.Event] = {}

    def abort_request(self, request_id: str) -> None:
        event = self._abort_events.get(str(request_id or ""))
        if event is not None:
            event.set()

    def abort_all_requests(self) -> None:
        for event in self._abort_events.values():
            event.set()
        self._abort_events.clear()

    async def run_text(self, request: StudioAgentRequest) -> str:
        if request.mode == "edit":
            result = await self._run_non_streaming(request, max_tokens=8192)
            return strip_code_fence(result)
        if request.mode == "inline":
            result = await self._run_non_streaming(request, max_tokens=256)
            return _clean_inline_text(result)
        return await self._run_non_streaming(request, max_tokens=4096)

    async def run_stream(self, request: StudioAgentRequest) -> AsyncIterator[StudioAgentEvent]:
        abort_event = asyncio.Event()
        self._abort_events[request.request_id] = abort_event
        try:
            async for event in self._run_streaming(request, abort_event=abort_event):
                yield event
        except AgentRuntimeError as exc:
            yield StudioAgentEvent(kind="error", error=str(exc))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.exception("Studio agent stream failed mode=%s request_id=%s", request.mode, request.request_id)
            yield StudioAgentEvent(kind="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._abort_events.pop(request.request_id, None)

    async def _run_non_streaming(self, request: StudioAgentRequest, *, max_tokens: int) -> str:
        provider = self._provider_for_request(request)
        model_id = self._model_for_request(request, provider)
        messages, system_prompt = self._messages_for_request(request)
        self._log_prompt(request.mode, system_prompt, messages)
        agent = self._build_agent(provider=provider, model_id=model_id, system_prompt=system_prompt)
        try:
            response = await agent.run(
                messages,
                session=self._session_for_request(request),
                tools=list(request.tools) or None,
                options=self._options_for_request(request, provider=provider, max_tokens=max_tokens),
            )
        except AgentFrameworkImportError:
            raise
        except ModuleNotFoundError as exc:
            raise AgentRuntimeUnavailableError(str(exc)) from exc
        return _response_text(response)

    async def _run_streaming(
        self,
        request: StudioAgentRequest,
        *,
        abort_event: asyncio.Event,
    ) -> AsyncIterator[StudioAgentEvent]:
        provider = self._provider_for_request(request)
        model_id = self._model_for_request(request, provider)
        messages, system_prompt = self._messages_for_request(request)
        self._log_prompt(request.mode, system_prompt, messages)
        agent = self._build_agent(provider=provider, model_id=model_id, system_prompt=system_prompt)
        try:
            stream = agent.run(
                messages,
                stream=True,
                session=self._session_for_request(request),
                tools=list(request.tools) or None,
                options=self._options_for_request(request, provider=provider, max_tokens=4096),
            )
        except ModuleNotFoundError as exc:
            raise AgentRuntimeUnavailableError(str(exc)) from exc

        async for update in stream:
            if abort_event.is_set():
                break
            delta = _response_update_text(update)
            if delta:
                yield StudioAgentEvent(kind="chunk", text=delta)
        if not abort_event.is_set():
            yield StudioAgentEvent(kind="done")

    def _build_agent(self, *, provider: ProviderConfig, model_id: str, system_prompt: str) -> Any:
        try:
            from agent_framework import Agent
        except ModuleNotFoundError as exc:
            raise AgentRuntimeUnavailableError("agent-framework-core is not installed.") from exc

        client = build_chat_client(
            AgentClientSelection(
                provider=provider,
                model_id=model_id,
                reasoning_level=str(provider.reasoning_level or ""),
            )
        )
        return Agent(client, instructions=system_prompt, name="f8studio-agent")

    def _session_for_request(self, request: StudioAgentRequest) -> object | None:
        key = request.session_key
        if key is None:
            return None
        return self._session_registry.session_for(key)

    def _provider_for_request(self, request: StudioAgentRequest) -> ProviderConfig:
        provider_id = request.inline_provider_id if request.mode == "inline" else request.chat_provider_id
        if provider_id:
            provider = self._store.provider_by_id(provider_id)
            if provider is not None:
                return provider
        providers = self._store.providers()
        if providers:
            return providers[0]
        raise AgentRuntimeUnavailableError("No AI provider configured")

    def _model_for_request(self, request: StudioAgentRequest, provider: ProviderConfig) -> str:
        if request.mode == "inline":
            model_id = effective_inline_model_id(provider, request.inline_model_id)
        else:
            model_id = effective_chat_model_id(provider, request.chat_model_id)
        if not model_id:
            raise AgentRuntimeUnavailableError("No model selected")
        return model_id

    def _messages_for_request(self, request: StudioAgentRequest) -> tuple[list[dict[str, Any]], str]:
        document_language = current_document_language(
            document_language=request.document_language,
            assist_context=request.assist_context,
        )
        if request.mode == "edit":
            system_prompt = build_system_prompt(
                SYSTEM_PROMPT_EDIT,
                document_language=document_language,
                assist_context=request.assist_context,
                graph_context_snapshot=request.graph_context_snapshot,
            )
            messages = build_edit_messages(
                history=list(request.messages),
                code=request.code,
                instruction=request.instruction,
                system_prompt=system_prompt,
                document_language=document_language,
                attachments=_attachments_to_dicts(request.attachments),
            )
            return _messages_to_agent_framework_content(messages), system_prompt
        if request.mode == "plan":
            system_prompt = build_system_prompt(
                SYSTEM_PROMPT_PLAN,
                document_language=document_language,
                assist_context=request.assist_context,
                graph_context_snapshot=request.graph_context_snapshot,
            )
            messages = build_plan_messages(
                history=list(request.messages),
                code=request.code,
                task_description=request.task_description,
                system_prompt=system_prompt,
                document_language=document_language,
                attachments=_attachments_to_dicts(request.attachments),
            )
            return _messages_to_agent_framework_content(messages), system_prompt
        if request.mode == "inline":
            system_prompt = build_system_prompt(
                SYSTEM_PROMPT_CODE,
                document_language=document_language,
                assist_context=request.assist_context,
                graph_context_snapshot=request.graph_context_snapshot,
            )
            user_text = (
                "Return only the code text that should be inserted at the cursor. "
                "Do not include markdown fences or explanations.\n\n"
                f"Prefix:\n```{document_language}\n{request.prefix}\n```\n\n"
                f"Suffix:\n```{document_language}\n{request.suffix}\n```"
            )
            return [{"role": "user", "content": user_text}], system_prompt

        system_prompt = build_system_prompt(
            SYSTEM_PROMPT_CODE,
            document_language=document_language,
            assist_context=request.assist_context,
            graph_context_snapshot=request.graph_context_snapshot,
        )
        messages = build_chat_messages(
            history=list(request.messages),
            code=request.code,
            selection=request.selection,
            system_prompt=system_prompt,
            document_language=document_language,
            attachments=_attachments_to_dicts(request.attachments),
        )
        return _messages_to_agent_framework_content(messages), system_prompt

    @staticmethod
    def _options_for_request(
        request: StudioAgentRequest,
        *,
        provider: ProviderConfig,
        max_tokens: int,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if max_tokens > 0:
            options["max_tokens"] = int(max_tokens)
        if request.reasoning_level:
            if provider.api_mode == "responses":
                options["reasoning"] = {"effort": str(request.reasoning_level)}
        return options

    def _log_prompt(self, mode: str, system_prompt: str, messages: list[dict[str, Any]]) -> None:
        if self._log_prompt_payload is not None:
            self._log_prompt_payload(mode, system_prompt, messages)


def _attachments_to_dicts(attachments: tuple[StudioAgentAttachment, ...]) -> list[dict[str, str]]:
    return [{"name": item.name, "content": item.content, "mime": item.mime} for item in attachments]


def _messages_to_agent_framework_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            continue
        content = message.get("content", "")
        out.append({"role": role, "content": _content_to_agent_framework_content(content)})
    return out


def _content_to_agent_framework_content(content: Any) -> Any:
    if not isinstance(content, list):
        return str(content or "")
    parts: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type == "text":
            parts.append(str(part.get("text") or ""))
        elif part_type == "image":
            mime = str(part.get("mime_type") or "image/png")
            data = str(part.get("image") or "")
            parts.append(_image_content_from_base64(data, mime))
    return parts


def _image_content_from_base64(data: str, mime: str) -> Any:
    try:
        from agent_framework import Content
    except ModuleNotFoundError as exc:
        raise AgentRuntimeUnavailableError("agent-framework-core is not installed.") from exc
    try:
        raw = base64.b64decode(str(data or ""))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image attachment") from exc
    return Content.from_data(data=raw, media_type=mime)


def _response_text(response: Any) -> str:
    try:
        from agent_framework import AgentResponse
    except ModuleNotFoundError as exc:
        raise AgentRuntimeUnavailableError("agent-framework-core is not installed.") from exc
    if isinstance(response, AgentResponse):
        return response.text
    raise TypeError(f"Expected AgentResponse, got {type(response).__name__}")


def _response_update_text(update: Any) -> str:
    try:
        from agent_framework import AgentResponseUpdate
    except ModuleNotFoundError as exc:
        raise AgentRuntimeUnavailableError("agent-framework-core is not installed.") from exc
    if isinstance(update, AgentResponseUpdate):
        return update.text
    raise TypeError(f"Expected AgentResponseUpdate, got {type(update).__name__}")


def _clean_inline_text(text: str) -> str:
    lines = str(text or "").splitlines()
    if lines and lines[0].startswith("```"):
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines)
