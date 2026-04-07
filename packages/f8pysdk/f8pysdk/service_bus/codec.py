from __future__ import annotations

"""
Compatibility re-export for the legacy `service_bus.codec` module.

Public callers should prefer `f8pysdk.codec`.
"""

from .compat import warn_compat_import
from ..codec import decode_as, decode_obj, encode_obj

warn_compat_import(
    module_path="f8pysdk.service_bus.codec",
    replacement="f8pysdk.codec",
)

__all__ = ["decode_as", "decode_obj", "encode_obj"]
