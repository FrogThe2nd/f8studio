from __future__ import annotations

"""Stable monitor snapshot API surface."""

from .service_bus.monitor_collector import MonitorCollector, MonitorCollectorConfig

__all__ = ["MonitorCollector", "MonitorCollectorConfig"]
