from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.state_router` module.

Repo-internal callers should prefer `f8pysdk.service_bus.state.router`.
"""

from .compat import warn_compat_import
from .state.router import (
    CrossStateBindingKey,
    CrossStateBindingTable,
    StateRouteTable,
    StateRouteTarget,
    StateRouter,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.state_router",
    replacement="f8pysdk.service_bus.state.router",
)

__all__ = [
    "CrossStateBindingKey",
    "CrossStateBindingTable",
    "StateRouteTable",
    "StateRouteTarget",
    "StateRouter",
]
