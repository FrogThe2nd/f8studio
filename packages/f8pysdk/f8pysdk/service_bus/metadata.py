from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.metadata` module.

Repo-internal callers should prefer owner modules such as
`f8pysdk.service_bus.internal.command`,
`f8pysdk.service_bus.state.helpers`, and
`f8pysdk.service_bus.workflow.metadata`.
"""

from .compat import warn_compat_import
from .internal.command import build_command_output_meta, build_hidden_command_call_meta
from .state.helpers import build_cross_state_meta, build_intra_state_route_meta, build_state_validation_meta
from .workflow.metadata import (
    build_builtin_identity_state_meta,
    build_lifecycle_event_meta,
    build_lifecycle_state_meta,
    build_rungraph_reconcile_meta,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.metadata",
    replacement=(
        "f8pysdk.service_bus.internal.command, "
        "f8pysdk.service_bus.state.helpers, "
        "or f8pysdk.service_bus.workflow.metadata"
    ),
)

__all__ = [
    "build_builtin_identity_state_meta",
    "build_command_output_meta",
    "build_cross_state_meta",
    "build_hidden_command_call_meta",
    "build_intra_state_route_meta",
    "build_lifecycle_event_meta",
    "build_lifecycle_state_meta",
    "build_rungraph_reconcile_meta",
    "build_state_validation_meta",
]
