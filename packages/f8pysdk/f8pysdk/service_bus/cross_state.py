from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.cross_state` module.

Repo-internal callers should prefer `f8pysdk.service_bus.workflow.cross_state`
or `StateRouter`.
"""

from .compat import warn_compat_import

warn_compat_import(
    module_path="f8pysdk.service_bus.cross_state",
    replacement="f8pysdk.service_bus.workflow.cross_state",
)

from .workflow.cross_state import (
    on_remote_state_kv,
    stop_unused_cross_state_watches,
    sync_cross_state_watches,
    update_cross_state_bindings,
)

__all__ = [
    "on_remote_state_kv",
    "stop_unused_cross_state_watches",
    "sync_cross_state_watches",
    "update_cross_state_bindings",
]
