from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.runtime_collections` module.

Repo-internal callers should prefer `f8pysdk.service_bus.internal.cache`.
"""

from .compat import warn_compat_import
from .internal.cache import CappedOrderedDict

warn_compat_import(
    module_path="f8pysdk.service_bus.runtime_collections",
    replacement="f8pysdk.service_bus.internal.cache",
)

__all__ = ["CappedOrderedDict"]
