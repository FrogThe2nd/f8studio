from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.domain.state_pipeline` module.

Repo-internal callers should prefer `f8pysdk.service_bus.state.pipeline`.
"""

from ..compat import warn_compat_import
from ..state.pipeline import (
    coerce_state_value,
    origin_allows_access,
    publish_state,
    validate_state_update,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.domain.state_pipeline",
    replacement="f8pysdk.service_bus.state.pipeline",
)

__all__ = [
    "coerce_state_value",
    "origin_allows_access",
    "publish_state",
    "validate_state_update",
]
