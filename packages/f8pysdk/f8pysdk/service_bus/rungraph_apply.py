from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.rungraph_apply` module.

Repo-internal callers should prefer `f8pysdk.service_bus.workflow.rungraph`.
"""

from .compat import warn_compat_import

warn_compat_import(
    module_path="f8pysdk.service_bus.rungraph_apply",
    replacement="f8pysdk.service_bus.workflow.rungraph",
)

from .workflow.rungraph import (
    apply_rungraph,
    apply_rungraph_state_values,
    initial_sync_intra_state_edges,
    rebuild_routes,
    seed_builtin_identity_state,
    set_rungraph,
    validate_rungraph_or_raise,
)

__all__ = [
    "apply_rungraph",
    "apply_rungraph_state_values",
    "initial_sync_intra_state_edges",
    "rebuild_routes",
    "seed_builtin_identity_state",
    "set_rungraph",
    "validate_rungraph_or_raise",
]
