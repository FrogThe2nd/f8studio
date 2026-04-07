from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.bus` module.

Prefer importing these public types from `f8pysdk.service_bus`.
"""

from .compat import warn_compat_import

warn_compat_import(
    module_path="f8pysdk.service_bus.bus",
    replacement="f8pysdk.service_bus",
)

from .api.bus import ServiceBus
from .api.config import ServiceBusConfig
from .api.types import (
    CommandExecutionErrorKind,
    CommandExecutionResult,
    CommandOutputPolicy,
    CrossPublishPolicy,
    DataDeliveryMode,
)

__all__ = [
    "CommandExecutionErrorKind",
    "CommandExecutionResult",
    "CommandOutputPolicy",
    "CrossPublishPolicy",
    "DataDeliveryMode",
    "ServiceBus",
    "ServiceBusConfig",
]
