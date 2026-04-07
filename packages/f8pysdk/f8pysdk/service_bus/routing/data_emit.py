from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.routing.data_emit` module.

Repo-internal callers should prefer `f8pysdk.service_bus.data.emit`.
"""

from ..compat import warn_compat_import
from ..data.emit import CrossPublishPlan, DataEmitOptions

warn_compat_import(
    module_path="f8pysdk.service_bus.routing.data_emit",
    replacement="f8pysdk.service_bus.data.emit",
)

__all__ = ["CrossPublishPlan", "DataEmitOptions"]
