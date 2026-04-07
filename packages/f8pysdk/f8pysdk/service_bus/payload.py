from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.payload` module.

Repo-internal callers should prefer `f8pysdk.service_bus.state.helpers`.
"""

from .compat import warn_compat_import
from .state.helpers import coerce_inbound_ts_ms, extract_ts_field

warn_compat_import(
    module_path="f8pysdk.service_bus.payload",
    replacement="f8pysdk.service_bus.state.helpers",
)

__all__ = ["coerce_inbound_ts_ms", "extract_ts_field"]
