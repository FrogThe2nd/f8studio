from __future__ import annotations

"""
Stable public owner for the service bus runtime facade and config.

Prefer `f8pysdk.bus` over `f8pysdk.service_bus`, which remains as a
compatibility barrel for legacy imports.
"""

from .service_bus.config import ServiceBusConfig
from .service_bus.runtime import ServiceBus

__all__ = ["ServiceBus", "ServiceBusConfig"]
