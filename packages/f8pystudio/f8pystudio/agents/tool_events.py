from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


StudioAgentToolStatus = Literal["started", "completed", "failed"]


@dataclass(frozen=True)
class StudioAgentToolTrace:
    tool_call_id: str
    tool_name: str
    method: str
    status: StudioAgentToolStatus
    started_at_ms: int
    ended_at_ms: int | None = None
    duration_ms: int | None = None
    summary: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolCallId": self.tool_call_id,
            "toolName": self.tool_name,
            "method": self.method,
            "status": self.status,
            "startedAtMs": self.started_at_ms,
            "endedAtMs": self.ended_at_ms,
            "durationMs": self.duration_ms,
            "summary": self.summary,
            "error": self.error,
        }


@dataclass(frozen=True)
class StudioAgentApprovalRequest:
    approval_id: str
    tool_call_id: str
    tool_name: str
    method: str
    title: str
    description: str
    params_summary: str
    created_at_ms: int
    timeout_s: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "approvalId": payload["approval_id"],
            "toolCallId": payload["tool_call_id"],
            "toolName": payload["tool_name"],
            "method": payload["method"],
            "title": payload["title"],
            "description": payload["description"],
            "paramsSummary": payload["params_summary"],
            "createdAtMs": payload["created_at_ms"],
            "timeoutS": payload["timeout_s"],
            "metadata": dict(self.metadata),
        }


def new_tool_call_id() -> str:
    return "tool-" + uuid.uuid4().hex


def new_approval_id() -> str:
    return "approval-" + uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)


def tool_method_to_name(method: str) -> str:
    name = str(method or "").strip()
    return name.replace(".", "_") if name else "studio_tool"


def summarize_tool_params(params: dict[str, Any]) -> str:
    if not params:
        return "No parameters."
    parts: list[str] = []
    for key in sorted(params.keys()):
        value = params[key]
        if key == "patch" and isinstance(value, dict):
            ops = value.get("ops")
            op_count = len(ops) if isinstance(ops, list) else 0
            parts.append(f"patch.ops={op_count}")
        elif key == "value":
            parts.append("value=<provided>")
        elif key == "args":
            parts.append("args=<provided>")
        elif isinstance(value, (str, int, float, bool)) or value is None:
            parts.append(f"{key}={value!r}")
        elif isinstance(value, list):
            parts.append(f"{key}=list[{len(value)}]")
        elif isinstance(value, dict):
            parts.append(f"{key}=object[{len(value)}]")
        else:
            parts.append(f"{key}={type(value).__name__}")
    return ", ".join(parts)


def summarize_tool_result(result: dict[str, Any]) -> str:
    if not result:
        return "Completed."
    if "preview" in result:
        preview = result.get("preview")
        if isinstance(preview, dict):
            valid = bool(preview.get("valid", False))
            errors = preview.get("errors")
            error_count = len(errors) if isinstance(errors, list) else 0
            changed = preview.get("changed_node_ids") or preview.get("changedNodeIds")
            changed_count = len(changed) if isinstance(changed, list) else 0
            return f"Preview valid={valid}, changedNodes={changed_count}, errors={error_count}."
        return "Preview returned."
    if "diagnostics" in result:
        diagnostics = result.get("diagnostics")
        if isinstance(diagnostics, dict):
            summary = diagnostics.get("summary")
            issues = diagnostics.get("issues")
            issue_count = len(issues) if isinstance(issues, list) else 0
            if isinstance(summary, dict):
                return f"Diagnostics issues={issue_count}, nodes={summary.get('nodeCount', '?')}."
            return f"Diagnostics issues={issue_count}."
        return "Diagnostics returned."
    if "samples" in result:
        samples = result.get("samples")
        if isinstance(samples, dict):
            inner = samples.get("samples")
            sample_count = len(inner) if isinstance(inner, list) else len(samples)
            return f"Samples returned={sample_count}."
        if isinstance(samples, list):
            return f"Samples returned={len(samples)}."
        return "Samples returned."
    if "state" in result:
        return "State returned." if result.get("state") is not None else "No state observed."
    if "logs" in result:
        return "Logs returned."
    if "services" in result:
        services = result.get("services")
        service_count = len(services) if isinstance(services, list) else 0
        return f"Services returned={service_count}."
    if "workflow" in result:
        workflow = result.get("workflow")
        if isinstance(workflow, dict):
            return str(workflow.get("summary") or workflow.get("status") or "Workflow completed.")
        return "Workflow completed."
    keys = ", ".join(sorted(str(key) for key in result.keys())[:5])
    return f"Returned keys: {keys}."
