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

SYSTEM_PROMPT_EDIT = (
    "You are a document editing assistant. "
    "The user will provide the current document and an instruction. "
    "Return ONLY the complete rewritten document; no explanation, no markdown fences, no comments about changes."
)

SYSTEM_PROMPT_PLAN = (
    "You are a thoughtful coding assistant. First ask any clarifying questions "
    "needed to understand the task. Then create a numbered plan. "
    "When the user approves the plan, you may proceed step by step. "
    "Be concise."
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
        blocks.append(
            "\n".join(
                [
                    "## PyStudio Graph Tools",
                    "- You already have graph tools available in this chat session; do not ask the user to manually expose MCP tools.",
                    "- Available inspection tools: `studio_status`, `graph_snapshot`, `graph_find_nodes`, `graph_node_detail`, `graph_connections`, `graph_diagnostics`, `node_catalog`, `service_library`, `operator_library`, `operator_detail`, `graph_session`, `graph_compile`, `runtime_services`, `runtime_service_status`, `monitor_report`, `monitor_service`, `logs_read`.",
                    "- Available action tools: `graph_preview_patch`, `graph_apply_patch`, `runtime_deploy`, `runtime_service_deploy`, `runtime_set_service_active`, `runtime_set_managed_active`, `runtime_service_process`, `runtime_write_state`, `runtime_sample_port`, `runtime_invoke_command`.",
                    "- Use `graph_diagnostics` first when debugging graph structure, compile failures, or container/service binding issues.",
                    "- Use `graph_find_nodes` and `graph_node_detail` to choose relevant nodes yourself; do not require the user to select nodes before you inspect the graph.",
                    "- Use `graph_snapshot` for current nodes/edges/selection and `graph_connections` for wiring around a node.",
                    "- Use `node_catalog` for valid canvas node types and ports.",
                    "- Use `service_library`, `operator_library`, and `operator_detail` to choose valid node/service/operator schemas before constructing patches.",
                    "- Use `runtime_services`, `monitor_service`, `runtime_sample_port`, and `logs_read` when debugging a running graph.",
                    "- Use `graph_preview_patch` before applying a non-trivial graph edit, then use `graph_apply_patch` when the user asks you to make the graph change.",
                    "- Deploy or runtime command tools may require `confirm=true`; ask the user only when the requested action is destructive or ambiguous.",
                    "- GraphPatch operations use camelCase fields: `expectedRevision`, `ops`, `op`, `nodeType`, `nodeId`, `fromNodeId`, `fromPort`, `toNodeId`, and `toPort`.",
                ]
            )
        )
    return "\n\n".join(blocks)


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
        messages.append({"role": role, "content": item.get("content", "")})

    return _attach_images_to_last_user_message(messages, attachments)


def build_edit_messages(
    *,
    history: list[dict[str, Any]],
    code: str,
    instruction: str,
    system_prompt: str,
    document_language: str,
    attachments: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    fence_language = "" if document_language in {"", "plaintext"} else document_language
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        role = str(item.get("role") or "")
        if role != "system":
            messages.append({"role": role, "content": item.get("content", "")})

    user_content = (
        f"Instruction: {instruction}\n\n"
        f"Current {document_language} document:\n```{fence_language}\n{code}\n```"
    )
    messages.append({"role": "user", "content": user_content})
    return _attach_images_to_last_user_message(messages, attachments)


def build_plan_messages(
    *,
    history: list[dict[str, Any]],
    code: str,
    task_description: str,
    system_prompt: str,
    document_language: str,
    attachments: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    fence_language = "" if document_language in {"", "plaintext"} else document_language
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if code:
        messages.append(
            {
                "role": "user",
                "content": f"Current {document_language} document:\n```{fence_language}\n{code}\n```",
            }
        )
        messages.append({"role": "assistant", "content": "I can see the current document. What would you like me to do?"})
    for item in history:
        role = str(item.get("role") or "")
        if role != "system":
            messages.append({"role": role, "content": item.get("content", "")})
    if task_description:
        messages.append({"role": "user", "content": str(task_description)})
    return _attach_images_to_last_user_message(messages, attachments)


def strip_code_fence(text: str) -> str:
    stripped = str(text or "")
    think_end = stripped.rfind("</think>")
    if think_end != -1:
        stripped = stripped[think_end + len("</think>"):]
    stripped = stripped.strip()
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines)


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
