from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.micro` module.

New repo-internal imports should use `f8pysdk.service_bus.internal.micro`.
"""

from .compat import warn_compat_import
from .internal.micro import ServiceBusMicroEndpoints

warn_compat_import(
    module_path="f8pysdk.service_bus.micro",
    replacement="f8pysdk.service_bus.internal.micro",
)

__all__ = ["ServiceBusMicroEndpoints"]
