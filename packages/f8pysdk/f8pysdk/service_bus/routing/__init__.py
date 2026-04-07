from __future__ import annotations

"""
Legacy compatibility namespace for data-side routing helpers.

New repo-internal imports should prefer `f8pysdk.service_bus.data.*`.
"""

from ..data.emit import DataEmitOptions
from ..data.flow import (
    _InputBuffer,
    buffer_input,
    emit_data,
    pull_data,
    subscribe_subject,
    unsubscribe_subject,
)

__all__ = [
    "DataEmitOptions",
    "_InputBuffer",
    "buffer_input",
    "emit_data",
    "pull_data",
    "subscribe_subject",
    "unsubscribe_subject",
]
