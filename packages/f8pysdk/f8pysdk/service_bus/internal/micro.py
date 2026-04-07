from __future__ import annotations

"""Internal-only micro endpoint adapter alias owned by `service_bus`."""

from ..adapters.micro import _ServiceBusMicroEndpoints

ServiceBusMicroEndpoints = _ServiceBusMicroEndpoints

__all__ = ["ServiceBusMicroEndpoints"]
