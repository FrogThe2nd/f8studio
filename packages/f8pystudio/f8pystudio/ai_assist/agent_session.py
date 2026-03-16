from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from typing import Any, Callable, Literal

from .graph_agent_tools import AgentToolCall, AgentToolResult, GraphAgentToolExecutor
from .graph_context import GraphAgentSeedContext, format_graph_agent_seed_context
from .http_client import AiHttpClient
from .registry import ProviderConfig
from .tool_registry import (
    load_graph_agent_tool_guide,
    render_graph_agent_response_shapes,
    render_graph_agent_tool_examples,
    render_graph_agent_tool_registry,
)

logger = logging.getLogger(__name__)

AgentResponseType = Literal["tool_call", "final_answer", "clarifying_question"]
AgentTraceEventType = Literal["tool_call", "tool_result", "parse_error", "final_answer", "clarifying_question", "error"]

_MAX_AGENT_TOOL_STEPS = 6
_MAX_AGENT_OUTPUT_TOKENS = 4096


@dataclass(frozen=True)
class AgentTurnTraceEvent:
    event_type: AgentTraceEventType
    step_index: int
    tool_name: str = ""
    reason: str = ""
    summary: str = ""
    error: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentTurnOutcome:
    answer_markdown: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _ParsedAgentResponse:
    response_type: AgentResponseType
    tool_call: AgentToolCall | None = None
    answer_markdown: str = ""
    question: str = ""


class GraphAgentSession:
    def __init__(
        self,
        *,
        http_client: AiHttpClient,
        provider_config: ProviderConfig,
        model_id: str,
        tool_executor: GraphAgentToolExecutor,
        seed_context: GraphAgentSeedContext,
        history: list[dict[str, Any]],
        attachments: list[dict[str, str]],
        on_trace: Callable[[AgentTurnTraceEvent], None],
        on_chunk: Callable[[str], None],
        on_done: Callable[[AgentTurnOutcome], None],
        on_messages_changed: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self._http_client = http_client
        self._provider_config = provider_config
        self._model_id = model_id
        self._tool_executor = tool_executor
        self._seed_context = seed_context
        self._on_trace = on_trace
        self._on_chunk = on_chunk
        self._on_done = on_done
        self._on_messages_changed = on_messages_changed
        self._messages = self._build_messages(history=history, attachments=attachments)
        self._tool_cache: dict[str, AgentToolResult] = {}
        self._step_index = 0
        self._repair_attempts = 0
        self._finished = False
        self._system_prompt = self._build_system_prompt(seed_context)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def start(self) -> None:
        direct_answer = self._try_answer_from_seed_context()
        if direct_answer is not None:
            self._emit_trace(
                AgentTurnTraceEvent(
                    event_type="final_answer",
                    step_index=0,
                    summary="Answered directly from seed context.",
                    payload={"source": "seed_context_fast_path"},
                )
            )
            self._on_chunk(direct_answer)
            self._finish(AgentTurnOutcome(answer_markdown=direct_answer))
            return
        self._request_model_turn()

    def _request_model_turn(self) -> None:
        if self._finished:
            return
        if self._on_messages_changed is not None:
            self._on_messages_changed(self._messages)
        self._http_client.chat_completion(
            self._provider_config,
            model_id=self._model_id,
            messages=self._messages,
            system=self._system_prompt,
            max_tokens=_MAX_AGENT_OUTPUT_TOKENS,
            on_result=self._handle_model_result,
        )

    def _handle_model_result(self, text: str, err: str | None) -> None:
        if self._finished:
            return
        if err:
            self._finish_with_error(str(err))
            return
        parsed = self._parse_model_response(text)
        if parsed is None:
            return
        if parsed.response_type == "final_answer":
            self._emit_trace(
                AgentTurnTraceEvent(
                    event_type="final_answer",
                    step_index=self._step_index,
                    summary="Agent returned a final answer.",
                )
            )
            self._on_chunk(parsed.answer_markdown)
            self._finish(AgentTurnOutcome(answer_markdown=parsed.answer_markdown))
            return
        if parsed.response_type == "clarifying_question":
            self._emit_trace(
                AgentTurnTraceEvent(
                    event_type="clarifying_question",
                    step_index=self._step_index,
                    summary="Agent needs clarification.",
                )
            )
            self._on_chunk(parsed.question)
            self._finish(AgentTurnOutcome(answer_markdown=parsed.question))
            return
        tool_call = parsed.tool_call
        if tool_call is None:
            self._finish_with_error("Graph agent returned an invalid tool call.")
            return
        if self._step_index >= _MAX_AGENT_TOOL_STEPS:
            self._finish_with_error("Graph agent exceeded the tool-call budget for this turn.")
            return
        self._step_index += 1
        self._emit_trace(
            AgentTurnTraceEvent(
                event_type="tool_call",
                step_index=self._step_index,
                tool_name=tool_call.tool_name,
                reason=tool_call.reason,
                summary=f"Calling {tool_call.tool_name}",
                arguments=dict(tool_call.arguments),
            )
        )
        tool_result = self._execute_tool_call(tool_call)
        self._emit_trace(
            AgentTurnTraceEvent(
                event_type="tool_result",
                step_index=self._step_index,
                tool_name=tool_call.tool_name,
                reason=tool_call.reason,
                summary=tool_result.summary,
                error=tool_result.error,
                arguments=dict(tool_call.arguments),
                payload=tool_result.to_dict(),
            )
        )
        self._messages.append({"role": "assistant", "content": json.dumps(self._tool_call_to_payload(tool_call), ensure_ascii=False)})
        self._messages.append({"role": "user", "content": self._tool_result_message(tool_result)})
        self._request_model_turn()

    def _execute_tool_call(self, tool_call: AgentToolCall) -> AgentToolResult:
        cache_key = json.dumps(
            {
                "tool_name": tool_call.tool_name,
                "arguments": tool_call.arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cached = self._tool_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._tool_executor.execute_tool_call(tool_call)
        self._tool_cache[cache_key] = result
        return result

    def _parse_model_response(self, text: str) -> _ParsedAgentResponse | None:
        cleaned = _strip_model_json_text(text)
        payload, decode_error = _load_response_json(cleaned)
        if payload is None:
            coerced = self._coerce_plaintext_final_answer(cleaned)
            if coerced is not None:
                self._repair_attempts = 0
                return coerced
            error_text = decode_error or "Model response could not be parsed as JSON."
            return self._repair_invalid_json(text=text, error=error_text)
        if not isinstance(payload, dict):
            return self._repair_invalid_json(text=text, error="Model response must be a JSON object.")

        response_type = str(payload.get("type") or "").strip()
        if response_type == "tool_call":
            tool_name = str(payload.get("tool_name") or "").strip()
            arguments = payload.get("arguments")
            reason = str(payload.get("reason") or "").strip()
            if not isinstance(arguments, dict):
                return self._repair_invalid_json(text=text, error="`tool_call.arguments` must be an object.")
            try:
                tool_call = AgentToolCall(tool_name=tool_name, arguments=arguments, reason=reason)
            except TypeError as exc:
                return self._repair_invalid_json(text=text, error=f"Invalid tool call: {exc}")
            self._repair_attempts = 0
            return _ParsedAgentResponse(response_type="tool_call", tool_call=tool_call)
        if response_type == "final_answer":
            answer_markdown = str(payload.get("answer_markdown") or "").strip()
            if not answer_markdown:
                return self._repair_invalid_json(text=text, error="`final_answer.answer_markdown` must be non-empty.")
            self._repair_attempts = 0
            return _ParsedAgentResponse(response_type="final_answer", answer_markdown=answer_markdown)
        if response_type == "clarifying_question":
            question = str(payload.get("question") or "").strip()
            if not question:
                return self._repair_invalid_json(text=text, error="`clarifying_question.question` must be non-empty.")
            self._repair_attempts = 0
            return _ParsedAgentResponse(response_type="clarifying_question", question=question)
        return self._repair_invalid_json(text=text, error=f"Unsupported response type: {response_type or '<empty>'}")

    def _coerce_plaintext_final_answer(self, text: str) -> _ParsedAgentResponse | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if self._step_index <= 0:
            return None
        lowered = cleaned.lower()
        disallowed_signals = (
            "upload or paste",
            "upload or paste the",
            "please upload",
            "please paste",
            "i don’t see any attachments",
            "i don't see any attachments",
        )
        if any(signal in lowered for signal in disallowed_signals):
            return None
        if len(cleaned) < 24 or "\n" not in cleaned and len(cleaned.split()) < 8:
            return None
        return _ParsedAgentResponse(response_type="final_answer", answer_markdown=cleaned)

    def _repair_invalid_json(self, *, text: str, error: str) -> None:
        excerpt = _response_excerpt(text)
        self._emit_trace(
            AgentTurnTraceEvent(
                event_type="parse_error",
                step_index=self._step_index,
                summary="Agent returned invalid JSON.",
                error=error,
                payload={"raw_response_excerpt": excerpt},
            )
        )
        if self._repair_attempts >= 1:
            self._finish_with_error(f"Graph agent returned invalid JSON twice. Last error: {error}")
            return None
        self._repair_attempts += 1
        raw_text = str(text or "").strip()
        if raw_text:
            self._messages.append({"role": "assistant", "content": raw_text})
        self._messages.append(
            {
                "role": "user",
                "content": self._repair_prompt(error=error, raw_response_excerpt=excerpt),
            }
        )
        self._request_model_turn()
        return None

    def _finish_with_error(self, error: str) -> None:
        self._emit_trace(
            AgentTurnTraceEvent(
                event_type="error",
                step_index=self._step_index,
                summary="Graph agent failed.",
                error=error,
            )
        )
        self._finish(AgentTurnOutcome(answer_markdown="", error=error))

    def _finish(self, outcome: AgentTurnOutcome) -> None:
        if self._finished:
            return
        self._finished = True
        self._on_done(outcome)

    def _emit_trace(self, event: AgentTurnTraceEvent) -> None:
        self._on_trace(event)

    def _build_messages(self, *, history: list[dict[str, Any]], attachments: list[dict[str, str]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in history:
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content", "")
            messages.append({"role": role, "content": content})
        if attachments and messages:
            last_message = messages[-1]
            if str(last_message.get("role") or "") == "user":
                text_content = str(last_message.get("content") or "")
                multimodal_parts: list[dict[str, Any]] = [{"type": "text", "text": text_content}]
                for attachment in attachments:
                    multimodal_parts.append(
                        {
                            "type": "image",
                            "image": str(attachment.get("content") or ""),
                            "mime_type": str(attachment.get("mime") or "image/png"),
                        }
                    )
                last_message["content"] = multimodal_parts
        return messages

    def _try_answer_from_seed_context(self) -> str | None:
        user_text = self._latest_user_text()
        if not user_text:
            return None
        focus_node_ids = tuple(self._seed_context.focus_node_ids)
        focus_node_names = tuple(self._seed_context.focus_node_names)
        if not focus_node_ids:
            return None
        if not _is_selected_node_identity_question(user_text):
            return None
        if len(focus_node_ids) == 1:
            node_name = focus_node_names[0] if focus_node_names else focus_node_ids[0]
            node_id = focus_node_ids[0]
            return f"当前被选中的节点叫 `{node_name}`，id 是 `{node_id}`。"
        pairs = []
        for index, node_id in enumerate(focus_node_ids):
            node_name = focus_node_names[index] if index < len(focus_node_names) else node_id
            pairs.append(f"`{node_name}` (`{node_id}`)")
        return "当前选中了多个节点：" + "，".join(pairs) + "。"

    def _latest_user_text(self) -> str:
        for message in reversed(self._messages):
            if str(message.get("role") or "").strip() != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                text_parts = [str(part.get("text") or "").strip() for part in content if isinstance(part, dict) and part.get("type") == "text"]
                merged = "\n".join(part for part in text_parts if part)
                if merged:
                    return merged
        return ""

    @staticmethod
    def _tool_result_message(result: AgentToolResult) -> str:
        return "\n".join(
            [
                f"Tool result for `{result.tool_name}`:",
                "```json",
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                "```",
            ]
        )

    @staticmethod
    def _tool_call_to_payload(tool_call: AgentToolCall) -> dict[str, Any]:
        return {
            "type": "tool_call",
            "tool_name": tool_call.tool_name,
            "arguments": tool_call.arguments,
            "reason": tool_call.reason,
        }

    @staticmethod
    def _build_system_prompt(seed_context: GraphAgentSeedContext) -> str:
        seed_json = format_graph_agent_seed_context(seed_context)
        guide_text = load_graph_agent_tool_guide()
        return "\n\n".join(
            [
                "You are an AI graph-reading assistant embedded in Feel8 Studio.",
                "Follow the graph-agent tool usage guide below exactly.",
                "## Tool Usage Guide",
                guide_text,
                _selection_focus_summary(seed_context),
                "Never invent node specs, state fields, current values, or connections that were not provided in the seed context or tool results.",
                "You must respond with exactly one JSON object and no surrounding prose or markdown.",
                "Never return an empty response.",
                "If the answer is already supported by the seed context, return `final_answer` immediately.",
                "If information is missing, return exactly one `tool_call` for the next missing detail.",
                render_graph_agent_response_shapes(),
                render_graph_agent_tool_registry(),
                render_graph_agent_tool_examples(),
                "Seed graph context (lightweight only, no full spec/value payloads):",
                seed_json,
            ]
        )

    @staticmethod
    def _repair_prompt(*, error: str, raw_response_excerpt: str) -> str:
        response_shapes = render_graph_agent_response_shapes()
        lines = [
            "Your previous reply did not satisfy the JSON protocol.",
            f"Validation error: {error}",
        ]
        if raw_response_excerpt:
            lines.extend(
                [
                    "Previous response excerpt:",
                    "```text",
                    raw_response_excerpt,
                    "```",
                ]
            )
        lines.extend(
            [
                "Return exactly one valid JSON object with one of these shapes and nothing else:",
                response_shapes,
                "Do not ask the user to upload or paste a graph image. The graph is the Feel8 Studio node graph already provided as structured seed context.",
                "Do not call `resolve_nodes` with the whole user task. Use it only with a short node-like search query.",
                "Do not include markdown fences, prefaces, explanations, or multiple objects.",
            ]
        )
        return "\n".join(lines)


def _strip_model_json_text(text: str) -> str:
    raw = str(text or "").strip()
    think_end = raw.rfind("</think>")
    if think_end != -1:
        raw = raw[think_end + len("</think>") :].strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _load_response_json(text: str) -> tuple[dict[str, Any] | list[Any] | None, str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None, "Model response was empty."
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, (dict, list)):
            return payload, ""
    except json.JSONDecodeError as exc:
        extracted = _extract_first_json_object(cleaned)
        if extracted:
            try:
                payload = json.loads(extracted)
                if isinstance(payload, (dict, list)):
                    return payload, ""
            except json.JSONDecodeError:
                pass
        return None, f"{type(exc).__name__}: {exc}"
    return None, "Model response was not a JSON object or array."


def _extract_first_json_object(text: str) -> str:
    raw = str(text or "")
    start = raw.find("{")
    while start != -1:
        candidate = _balanced_json_object(raw, start)
        if candidate:
            return candidate
        start = raw.find("{", start + 1)
    return ""


def _balanced_json_object(text: str, start_index: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return ""


def _response_excerpt(text: str, *, max_chars: int = 400) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars].rstrip() + "…"


def _selection_focus_summary(seed_context: GraphAgentSeedContext) -> str:
    focus_node_ids = list(seed_context.focus_node_ids)
    focus_node_names = list(seed_context.focus_node_names)
    lines = ["## Current Selection Focus"]
    if not focus_node_ids:
        lines.append("- No selected focus nodes are available in the seed context.")
        return "\n".join(lines)
    lines.append(f"- focus_node_ids: {json.dumps(focus_node_ids, ensure_ascii=False)}")
    lines.append(f"- focus_node_names: {json.dumps(focus_node_names, ensure_ascii=False)}")
    lines.append("- These are the nodes the user currently has selected in the pinned Feel8 Studio graph.")
    lines.append("- Treat `focus_node_ids` and `focus_node_names` as the authoritative answer for questions like `which node is selected?`, `what is the selected node?`, `which nodes are selected?`, `当前被选中的节点`, and `我选中的节点`.")
    if len(focus_node_ids) == 1:
        node_name = focus_node_names[0] if focus_node_names else focus_node_ids[0]
        node_id = focus_node_ids[0]
        lines.append(f"- If the user asks `which node is selected?`, `what is the selected node called?`, or asks for the selected node id, answer directly from this block: name=`{node_name}`, id=`{node_id}`.")
    else:
        lines.append("- If the user asks about the selected nodes, answer from this block directly unless more detail is needed.")
    return "\n".join(lines)


def _is_selected_node_identity_question(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    selection_signals = (
        "selected node",
        "selected nodes",
        "node is selected",
        "nodes are selected",
        "which node is selected",
        "which nodes are selected",
        "what node is selected",
        "what nodes are selected",
        "currently selected",
        "current selected",
        "focus node",
        "focus nodes",
        "我选中的节点",
        "当前被选中的节点",
        "被选中的节点",
        "当前选中的节点",
        "选中的节点",
    )
    if not any(signal in lowered for signal in selection_signals):
        return False
    identity_signals = (
        "叫什么",
        "叫啥",
        "名字",
        "名称",
        "name",
        "id",
        "是哪一个",
        "which node",
        "what is",
        "what's",
    )
    return any(signal in lowered for signal in identity_signals)


__all__ = [
    "AgentTurnOutcome",
    "AgentTurnTraceEvent",
    "GraphAgentSession",
]
