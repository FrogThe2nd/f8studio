"""Model connectivity checks through Microsoft Agent Framework clients.

This module deliberately avoids provider-specific HTTP request construction.
It validates that Studio can build the configured MAF client and obtain a
minimal non-streaming response for the selected model.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .clients import AgentClientSelection, build_chat_client
from .registry import ProviderConfig


@dataclass(frozen=True)
class ProviderConnectivityResult:
    success: bool
    error: str = ""


def check_model_connectivity(
    provider: ProviderConfig,
    model_id: str,
    *,
    timeout_s: float = 20.0,
) -> ProviderConnectivityResult:
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        return ProviderConnectivityResult(success=False, error="Model ID is required.")

    try:
        asyncio.run(_check_model_connectivity_async(provider, normalized_model_id, timeout_s=timeout_s))
    except TimeoutError:
        return ProviderConnectivityResult(success=False, error="Timed out while testing model connectivity.")
    except (ModuleNotFoundError, ValueError, TypeError, RuntimeError, OSError) as exc:
        return ProviderConnectivityResult(success=False, error=f"{type(exc).__name__}: {exc}")
    return ProviderConnectivityResult(success=True)


async def _check_model_connectivity_async(provider: ProviderConfig, model_id: str, *, timeout_s: float) -> None:
    client = build_chat_client(
        AgentClientSelection(
            provider=provider,
            model_id=model_id,
            reasoning_level=str(provider.reasoning_level or ""),
        )
    )
    message = _connectivity_message()
    response = client.get_response(
        [message],
        options=_connectivity_options(provider),
    )
    await asyncio.wait_for(response, timeout=timeout_s)


def _connectivity_options(provider: ProviderConfig) -> dict[str, Any]:
    options: dict[str, Any] = {"max_tokens": 8}
    if provider.inference_service in ("azure_openai_responses", "openai_responses"):
        options["store"] = False
    return options


def _connectivity_message() -> Any:
    try:
        from agent_framework import Message
    except ModuleNotFoundError as exc:
        raise RuntimeError("agent-framework-core is not installed.") from exc
    return Message("user", ["Reply with only: ok"])
