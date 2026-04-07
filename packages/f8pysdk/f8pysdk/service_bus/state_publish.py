from __future__ import annotations

"""
Compatibility re-export for state publish helpers.

New repo-internal imports should use `f8pysdk.service_bus.internal` for these
pipeline helpers. Public SDK callers should stay on `ServiceBus` and
`f8pysdk.state`.
"""

from .compat import warn_compat_import
from .internal.state import (
    coerce_state_value,
    origin_allows_access,
    publish_state,
    validate_state_update,
)
from .internal.state import StatePublishOptions

warn_compat_import(
    module_path="f8pysdk.service_bus.state_publish",
    replacement="f8pysdk.service_bus.internal.state or f8pysdk.state",
)

__all__ = [
    "StatePublishOptions",
    "coerce_state_value",
    "origin_allows_access",
    "publish_state",
    "validate_state_update",
]
