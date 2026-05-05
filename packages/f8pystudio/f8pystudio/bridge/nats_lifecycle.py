from __future__ import annotations

"""Compatibility exports for the explicit NATS fallback lifecycle helpers."""

from .runtime_lifecycle import (
    NatsConnectionManager,
    NatsSingletonGuardResult,
    RuntimeConnectionManager,
    RuntimeSingletonGuardResult,
    SINGLETON_GUARD_DIALOG_MESSAGE,
    SINGLETON_GUARD_DIALOG_TITLE,
    SINGLETON_GUARD_LOG_MESSAGE,
    ensure_nats_server_owned_pid,
    stop_owned_nats_server,
)

__all__ = [
    "NatsConnectionManager",
    "NatsSingletonGuardResult",
    "RuntimeConnectionManager",
    "RuntimeSingletonGuardResult",
    "SINGLETON_GUARD_DIALOG_MESSAGE",
    "SINGLETON_GUARD_DIALOG_TITLE",
    "SINGLETON_GUARD_LOG_MESSAGE",
    "ensure_nats_server_owned_pid",
    "stop_owned_nats_server",
]
