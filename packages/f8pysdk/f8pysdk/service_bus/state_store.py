from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.state_store` module.

Repo-internal callers should prefer `f8pysdk.service_bus.state.store`.
"""

from .compat import warn_compat_import
from .state.store import StateStore

warn_compat_import(
    module_path="f8pysdk.service_bus.state_store",
    replacement="f8pysdk.service_bus.state.store",
)

__all__ = ["StateStore"]
