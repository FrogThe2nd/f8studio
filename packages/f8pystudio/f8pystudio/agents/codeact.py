from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal


CodeActAvailabilityStatus = Literal["available", "unavailable"]


@dataclass(frozen=True)
class CodeActAvailability:
    status: CodeActAvailabilityStatus
    reason: str
    package_name: str = "agent-framework-hyperlight"

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class StudioAgentSkillStatus:
    name: str
    display_name: str
    enabled: bool
    available: bool
    description: str
    reason: str = ""

    def label(self) -> str:
        suffix = "on" if self.enabled and self.available else "off"
        if not self.available:
            suffix = "unavailable"
        return f"{self.display_name}: {suffix}"


@dataclass(frozen=True)
class StudioCodeActConfig:
    enabled: bool = True
    workspace_root: str = ""


def codeact_availability() -> CodeActAvailability:
    try:
        importlib.import_module("agent_framework_hyperlight")
    except ModuleNotFoundError as exc:
        return CodeActAvailability(status="unavailable", reason=str(exc))
    if importlib.util.find_spec("python_guest") is None:
        return CodeActAvailability(
            status="unavailable",
            reason="agent-framework-hyperlight is installed, but the packaged Python guest is unavailable.",
        )
    if importlib.util.find_spec("hyperlight_sandbox_backend_wasm") is None:
        return CodeActAvailability(
            status="unavailable",
            reason="agent-framework-hyperlight is installed, but the Hyperlight Wasm backend package is unavailable.",
        )
    return CodeActAvailability(status="available", reason="")


def codeact_skill_status(config: StudioCodeActConfig | None = None) -> StudioAgentSkillStatus:
    resolved_config = config or StudioCodeActConfig()
    availability = codeact_availability()
    return StudioAgentSkillStatus(
        name="codeact_diagnostics",
        display_name="CodeAct diagnostics",
        enabled=bool(resolved_config.enabled and availability.available),
        available=availability.available,
        description="Runs read-only graph/runtime diagnostic plans through a Hyperlight execute_code sandbox.",
        reason=availability.reason,
    )


def build_codeact_context_provider(
    *,
    tools: Sequence[Callable[..., dict[str, Any]]],
    config: StudioCodeActConfig | None = None,
) -> object | None:
    resolved_config = config or StudioCodeActConfig()
    if not resolved_config.enabled:
        return None
    if not tools:
        return None
    availability = codeact_availability()
    if not availability.available:
        return None

    hyperlight_module = importlib.import_module("agent_framework_hyperlight")
    provider_class = hyperlight_module.HyperlightCodeActProvider
    approval_mode = _never_require_approval_mode()
    workspace_root = str(resolved_config.workspace_root or "").strip() or None
    return provider_class(
        tools=tuple(tools),
        approval_mode=approval_mode,
        workspace_root=workspace_root,
    )


def _never_require_approval_mode() -> object | None:
    return "never_require"
