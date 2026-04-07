from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.routing.data_router` module.

Repo-internal callers should prefer `f8pysdk.service_bus.data.router`.
"""

from ..compat import warn_compat_import
from ..data.router import (
    DataCrossInRoutes,
    DataCrossOutRoutes,
    DataOutRoutes,
    DataRouteTarget,
    DataRouter,
    InputBuffer,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.routing.data_router",
    replacement="f8pysdk.service_bus.data.router",
)

__all__ = [
    "DataCrossInRoutes",
    "DataCrossOutRoutes",
    "DataOutRoutes",
    "DataRouteTarget",
    "DataRouter",
    "InputBuffer",
]
