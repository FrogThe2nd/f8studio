from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.command_runtime` module.

Repo-internal callers should prefer `f8pysdk.service_bus.internal.command`.
Public callers should stay on `f8pysdk.command` or `f8pysdk.service_bus`.
"""

from .compat import warn_compat_import
from .internal.command import (
    CommandBinding,
    CommandExecutionErrorKind,
    CommandExecutionResult,
    CommandGateway,
    CommandInvocation,
    CommandInvokeOptions,
    CommandOutputPolicy,
    command_state_bindings_ready,
    dispatch_command_input,
    execute_command,
    write_command_output,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.command_runtime",
    replacement="f8pysdk.service_bus.internal.command or f8pysdk.command",
)

__all__ = [
    "CommandBinding",
    "CommandExecutionErrorKind",
    "CommandExecutionResult",
    "CommandGateway",
    "CommandInvocation",
    "CommandInvokeOptions",
    "CommandOutputPolicy",
    "command_state_bindings_ready",
    "dispatch_command_input",
    "execute_command",
    "write_command_output",
]
