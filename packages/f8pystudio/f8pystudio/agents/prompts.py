from __future__ import annotations

import math
from typing import Any

from f8pystudio.agents.graph_context import GraphContextSnapshot, format_graph_context_snapshot
from f8pystudio.editor_assist.workspace import EditorAssistContext

SYSTEM_PROMPT_CODE = (
    "You are an expert assistant embedded in an IDE. "
    "You help the user write, refactor, and debug code or structured documents. "
    "Be concise and precise. When providing code, use proper syntax. "
    "When providing explanations, be brief."
)


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def schema_summary(schema_obj: dict[str, Any] | None) -> str:
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


def current_document_language(
    *,
    document_language: str,
    assist_context: EditorAssistContext | None,
) -> str:
    if assist_context is not None:
        language = str(assist_context.language or "").strip().lower()
        if language:
            return language
    return str(document_language or "plaintext").strip().lower() or "plaintext"


def format_assist_context(context: EditorAssistContext | None) -> str:
    if context is None:
        return ""
    lines: list[str] = []
    target_lines: list[str] = []
    if context.language:
        target_lines.append(f"- Document language: `{context.language}`")
    if context.target_field_kind:
        target_lines.append(f"- Target kind: `{context.target_field_kind}`")
    if context.target_field_name:
        target_lines.append(f"- Target field: `{context.target_field_name}`")
    if context.target_field_label:
        target_lines.append(f"- Target label: {context.target_field_label}")
    if context.target_ui_language and context.target_ui_language != context.language:
        target_lines.append(f"- Target UI language: `{context.target_ui_language}`")
    if context.target_field_description:
        target_lines.append(f"- Target description: {context.target_field_description}")
    if context.target_value_schema:
        target_lines.append(f"- Target schema: `{schema_summary(context.target_value_schema)}`")
    if target_lines:
        lines.append("## Editing Target")
        lines.extend(target_lines)

    meta_lines: list[str] = []
    if context.node_kind:
        meta_lines.append(f"- Kind: `{context.node_kind}`")
    if context.service_class:
        meta_lines.append(f"- Service: `{context.service_class}`")
    if context.operator_class:
        meta_lines.append(f"- Operator: `{context.operator_class}`")
    if context.node_description:
        meta_lines.append(f"- Type Description: {context.node_description}")
    if context.node_instance_purpose:
        meta_lines.append(f"- Instance Purpose: {context.node_instance_purpose}")
    if meta_lines:
        lines.append("## Node Metadata")
        lines.extend(meta_lines)

    if context.data_in_ports:
        lines.append("## Input Ports (`dataInPorts`)")
        for port in context.data_in_ports:
            req = "required" if port.required else "optional"
            desc = f" | description={port.description}" if port.description else ""
            lines.append(f"- `{port.name}` ({req}, schema={schema_summary(port.value_schema)}){desc}")
    if context.data_out_ports:
        lines.append("## Output Ports (`dataOutPorts`)")
        for port in context.data_out_ports:
            req = "required" if port.required else "optional"
            desc = f" | description={port.description}" if port.description else ""
            lines.append(f"- `{port.name}` ({req}, schema={schema_summary(port.value_schema)}){desc}")
    if context.state_fields:
        lines.append("## State Fields (`stateFields`)")
        for field in context.state_fields:
            req = "required" if field.required else "optional"
            desc = f" | description={field.description}" if field.description else ""
            lines.append(
                f"- `{field.name}` ({req}, access={field.access}, schema={schema_summary(field.value_schema)}){desc}"
            )
    if not lines:
        return ""
    out = ["\n# Current Node / Component Structure"]
    out.extend(lines)
    out.append("\n*Note: Use the above node inputs and states context to guide your logic and typing.*")
    return "\n".join(out)


def build_system_prompt(
    base_prompt: str,
    *,
    document_language: str,
    assist_context: EditorAssistContext | None,
    graph_context_snapshot: GraphContextSnapshot | None,
    graph_tools_enabled: bool = False,
    graph_tool_names: tuple[str, ...] = (),
    agent_surface: str = "graph",
) -> str:
    language = current_document_language(document_language=document_language, assist_context=assist_context)
    language_guidance = ""
    if language == "json":
        language_guidance = (
            "You are editing a JSON document. "
            "When generating or rewriting document content, return valid JSON for this document unless the user explicitly asks for another language. "
            "Do not default to Python code for JSON-authoring requests."
        )
    elif language not in {"", "plaintext"}:
        language_guidance = (
            f"You are editing a {language} document. "
            f"Prefer {language} syntax when writing or rewriting the document unless the user explicitly asks for another format."
        )

    blocks = [base_prompt]
    if language_guidance:
        blocks.append(language_guidance)
    assist_block = format_assist_context(assist_context)
    if assist_block:
        blocks.append(assist_block)
    graph_block = format_graph_context_snapshot(graph_context_snapshot)
    if graph_block:
        blocks.append(graph_block)
    if graph_tools_enabled:
        blocks.append(_graph_tool_guidance(agent_surface=agent_surface, tool_names=graph_tool_names))
    return "\n\n".join(blocks)


def _graph_tool_guidance(*, agent_surface: str, tool_names: tuple[str, ...]) -> str:
    normalized_surface = str(agent_surface or "graph").strip().lower()
    resolved_tool_names = tuple(str(name or "").strip() for name in tool_names if str(name or "").strip())
    available_line = _available_tool_names_line(resolved_tool_names)
    if normalized_surface == "node_editor":
        return "\n".join(
            [
                "## PyStudio Node Editor Agent",
                "- You are assisting with one editable field on a node inside a larger PyStudio graph.",
                "- Use graph tools to inspect the focused node, upstream inputs, downstream consumers, schemas, compile diagnostics, runtime samples, monitor data, and logs when that evidence matters.",
                "- The editor may provide a focused snapshot, but it is not the whole graph. Use `graph_ui_context`, `graph_node_detail`, and `graph_connections` instead of asking the user to describe wiring manually.",
                "- Keep generated code explicit, type-safe, and refactor-friendly. Avoid `getattr`, `setattr`, `hasattr`, `__dict__` mutation, and string-dispatched methods unless the schema is truly dynamic.",
                "- Avoid silent broad exception handling. Catch narrow exceptions, log/report actionable context at UI/runtime boundaries, and let core logic fail clearly.",
                "- High-frequency runtime telemetry such as latency, FPS, frame counters, and per-frame output counts belongs on monitor/data channels, not service state fields.",
                "- This editor profile is inspection and preview oriented. Do not claim you can directly apply graph patches, deploy services, write remote state, or invoke runtime commands unless those tools are explicitly available.",
                "- For non-trivial graph edits, prepare or preview a typed plan/patch and explain the change; leave final graph mutation to the graph-level agent workflow or an explicit approved action.",
                "- When the user explicitly asks for a complete document rewrite, return the complete rewritten document with no markdown fences.",
                available_line,
            ]
        )
    return "\n".join(
        [
            "## PyStudio Graph Tools",
            "- You already have graph tools available in this chat session; do not ask the user to manually expose MCP tools.",
            available_line,
            "- Use `graph_diagnostics` first when debugging graph structure, compile failures, or container/service binding issues.",
            "- Use `graph_ui_context` first when the user says current node, selected nodes, this node, or the node shown in properties.",
            "- Use `graph_find_nodes` and `graph_node_detail` to choose relevant nodes yourself; do not require the user to manually add graph context before you inspect the graph.",
            "- Use `graph_snapshot` for current nodes/edges/selection and `graph_connections` for wiring around a node.",
            "- Use `node_catalog` for valid canvas node types and ports.",
            "- Use `service_library`, `operator_library`, and `operator_detail` to choose valid node/service/operator schemas before constructing patches.",
            "- For graph construction from a user goal, use `graph_build_from_goal` or `graph_match_library` to gather candidates, inspect exact schemas, create a typed GraphBuildPlan, call `graph_preview_build_plan`, then call `graph_apply_build_plan` only after approval or a clear user build instruction.",
            "- Do not rely on hardcoded goal workflows; choose nodes from the live catalog/library and construct the typed plan from the user's intent.",
            "- Use `graph_debug_service` for a bundled diagnostics/compile/monitor/log pass around one service.",
            "- Use `graph_fix_container_bindings` when diagnostics report operators outside a service container or bound to a missing/mismatched service.",
            "- Use `graph_auto_layout` to preview or apply a tidy node layout after creating or repairing graph structure.",
            "- Use `runtime_services`, `monitor_service`, `runtime_read_state`, `runtime_watch_state`, `runtime_debug_data`, `runtime_sample_port`, `logs_read`, and `notifications_read` when debugging a running graph.",
            "- Use `graph_preview_patch` before applying a non-trivial graph edit, then use `graph_apply_patch` when the user asks you to make the graph change.",
            "- Destructive or runtime-affecting actions may show a GUI approval card; preview first, then request/apply only when the user's instruction is clear.",
            "- For game modding requests, call `modding_detect_target` first. If Unity is detected, call `modding_preview_install`, present blocking issues and exact game-directory writes, then call `modding_apply_install` only after approval or a clear user instruction.",
            "- After modding install, build or patch the PyStudio graph only after the stream target and UDP port are known. Verify with `modding_verify_stream` and a `UDP In -> Skeleton Decoder -> Viz 3D` graph before proposing TCode output.",
            "- Store failed attempts, verification notes, plugin versions, profile/config payloads, and graph/component references in `modding_create_recipe` drafts when the user wants to save or share the process.",
            "- Never guess-install a loader, overwrite custom profiles/configs, or write into a game directory without a typed preview and approval.",
            "- Do not model high-frequency skeleton stream counters as service state. Use monitor/data sampling for latency, sample counts, dropped frames, and decoder output counts.",
            "- GraphPatch operations use camelCase fields: `expectedRevision`, `ops`, `op`, `nodeType`, `nodeId`, `fromNodeId`, `fromPort`, `toNodeId`, and `toPort`.",
        ]
    )


def _available_tool_names_line(tool_names: tuple[str, ...]) -> str:
    if not tool_names:
        return "- Available tools are provided by the current PyStudio surface."
    names = ", ".join(f"`{name}`" for name in tool_names)
    return f"- Available tools in this surface: {names}."


def build_chat_messages(
    *,
    history: list[dict[str, Any]],
    code: str,
    selection: str,
    system_prompt: str,
    document_language: str,
    attachments: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    fence_language = "" if document_language in {"", "plaintext"} else document_language
    if code:
        context_content = f"Current editor content ({document_language}):\n```{fence_language}\n{code}\n```"
        if selection:
            context_content += f"\n\nSelected text:\n```{fence_language}\n{selection}\n```"
        messages.append({"role": "user", "content": context_content})
        messages.append({"role": "assistant", "content": "I can see the current document. How can I help?"})

    for item in history:
        role = str(item.get("role") or "")
        if role == "system":
            continue
        messages.append(_history_message_to_prompt_message(item))

    return _attach_images_to_last_user_message(messages, attachments)


def _attach_images_to_last_user_message(
    messages: list[dict[str, Any]],
    attachments: list[dict[str, str]] | None,
) -> list[dict[str, Any]]:
    if not attachments or not messages:
        return messages
    last_msg = messages[-1]
    if last_msg["role"] != "user":
        return messages
    text_content = last_msg["content"]
    if not isinstance(text_content, str):
        return messages
    parts: list[dict[str, str]] = [{"type": "text", "text": text_content}]
    for attachment in attachments:
        parts.append(
            {
                "type": "image",
                "image": attachment["content"],
                "mime_type": attachment["mime"],
            }
        )
    last_msg["content"] = parts
    return messages


def _history_message_to_prompt_message(item: dict[str, Any]) -> dict[str, Any]:
    role = str(item.get("role") or "")
    content = item.get("content", "")
    message = {"role": role, "content": content}
    attachments = item.get("attachments", [])
    if role != "user" or not isinstance(content, str) or not isinstance(attachments, list):
        return message
    image_attachments: list[dict[str, str]] = []
    for attachment in attachments:
        if isinstance(attachment, dict):
            image_attachments.append(
                {
                    "content": str(attachment.get("content", "")),
                    "mime": str(attachment.get("mime", "image/png")),
                }
            )
    if not image_attachments:
        return message
    parts: list[dict[str, str]] = [{"type": "text", "text": content}]
    for attachment in image_attachments:
        parts.append(
            {
                "type": "image",
                "image": attachment["content"],
                "mime_type": attachment["mime"],
            }
        )
    return {"role": role, "content": parts}
