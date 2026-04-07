from __future__ import annotations

import os
from dataclasses import dataclass

from nats.js.api import StorageType  # type: ignore[import-not-found]

from ..data import CrossPublishPolicy, DataDeliveryMode


def _debug_state_enabled() -> bool:
    return str(os.getenv("F8_STATE_DEBUG", "")).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ServiceBusConfig:
    service_id: str
    service_name: str | None = None
    service_class: str | None = None
    nats_url: str = "nats://127.0.0.1:4222"
    cross_publish_policy: CrossPublishPolicy = "routed"
    # Compatibility alias for pre-Slice-C callers.
    # - True  -> "all"
    # - False -> "routed"
    publish_all_data: bool | None = None
    kv_storage: StorageType = StorageType.MEMORY
    delete_bucket_on_start: bool = False
    delete_bucket_on_stop: bool = False
    # Canonical local delivery semantics:
    # - "callback": invoke `on_data(...)` only
    # - "buffered": satisfy `pull_data(...)` only
    # - "both": explicit dual local delivery for compatibility/migration only
    data_delivery: DataDeliveryMode = "callback"
    state_sync_concurrency: int = 8
    state_cache_max_entries: int = 8192
    data_input_max_buffers: int = 4096
    data_input_default_queue_size: int = 256
    monitor_enabled: bool = True
    monitor_interval_ms: int = 1000
    monitor_window_ms: int = 30000
    monitor_gpu_enabled: bool = True


__all__ = ["ServiceBusConfig", "_debug_state_enabled"]
