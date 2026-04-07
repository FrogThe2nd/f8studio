from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.error_utils` module.

Repo-internal callers should prefer `f8pysdk.service_bus.internal.logging`.
"""

from .compat import warn_compat_import
from .internal.logging import log_error_once

warn_compat_import(
    module_path="f8pysdk.service_bus.error_utils",
    replacement="f8pysdk.service_bus.internal.logging",
)

__all__ = ["log_error_once"]
