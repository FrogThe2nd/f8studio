from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.routing_data` module.

Public callers should prefer `ServiceBus` methods or `f8pysdk.testing`.
Repo-internal callers should prefer `DataRouter` or
`f8pysdk.service_bus.data.flow`.
"""

from .compat import warn_compat_import

warn_compat_import(
    module_path="f8pysdk.service_bus.routing_data",
    replacement="ServiceBus methods, f8pysdk.testing, or f8pysdk.service_bus.data.flow",
)

from .data.flow import (
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
