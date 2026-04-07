from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.routing.data_flow` module.

Repo-internal callers should prefer `f8pysdk.service_bus.data.flow`.
"""

from ..compat import warn_compat_import
from ..data.flow import (
    _InputBuffer,
    buffer_input,
    compute_and_buffer_for_input,
    emit_data,
    ensure_input_available,
    is_stale,
    on_cross_data_msg,
    precreate_input_buffers_for_cross_in,
    pull_data,
    push_input,
    subscribe_subject,
    sync_subscriptions,
    unsubscribe_subject,
)

warn_compat_import(
    module_path="f8pysdk.service_bus.routing.data_flow",
    replacement="f8pysdk.service_bus.data.flow",
)

__all__ = [
    "_InputBuffer",
    "buffer_input",
    "compute_and_buffer_for_input",
    "emit_data",
    "ensure_input_available",
    "is_stale",
    "on_cross_data_msg",
    "precreate_input_buffers_for_cross_in",
    "pull_data",
    "push_input",
    "subscribe_subject",
    "sync_subscriptions",
    "unsubscribe_subject",
]
