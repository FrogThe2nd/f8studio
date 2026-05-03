from __future__ import annotations

import os
from dataclasses import dataclass, replace

from nats.js.api import StorageType  # type: ignore[import-not-found]

from ..data import CrossPublishPolicy, DataDeliveryMode


def _debug_state_enabled() -> bool:
    return str(os.getenv("F8_STATE_DEBUG", "")).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ServiceBusConfig:
    service_id: str = ""
    service_name: str | None = None
    service_class: str | None = None
    nats_url: str = "nats://127.0.0.1:4222"
    cross_publish_policy: CrossPublishPolicy = "routed"
    kv_storage: StorageType = StorageType.MEMORY
    delete_bucket_on_start: bool = False
    delete_bucket_on_stop: bool = False
    # Canonical local delivery semantics:
    # - "callback": buffer local inputs and invoke `on_data(...)`
    # - "buffered": buffer local inputs without invoking `on_data(...)`
    data_delivery: DataDeliveryMode = "callback"
    state_sync_concurrency: int = 8
    state_cache_max_entries: int = 8192
    data_input_max_buffers: int = 4096
    data_input_default_queue_size: int = 256
    monitor_enabled: bool = True
    monitor_interval_ms: int = 1000
    monitor_window_ms: int = 30000
    monitor_gpu_enabled: bool = True

    def normalized(self) -> "ServiceBusConfig":
        service_name = str(self.service_name or "").strip() or None
        service_class = str(self.service_class or "").strip() or None
        return ServiceBusConfig(
            service_id=str(self.service_id or "").strip(),
            service_name=service_name,
            service_class=service_class,
            nats_url=str(self.nats_url or "").strip() or "nats://127.0.0.1:4222",
            cross_publish_policy=self.cross_publish_policy,
            kv_storage=self.kv_storage,
            delete_bucket_on_start=bool(self.delete_bucket_on_start),
            delete_bucket_on_stop=bool(self.delete_bucket_on_stop),
            data_delivery=self.data_delivery,
            state_sync_concurrency=max(1, int(self.state_sync_concurrency)),
            state_cache_max_entries=max(0, int(self.state_cache_max_entries)),
            data_input_max_buffers=max(0, int(self.data_input_max_buffers)),
            data_input_default_queue_size=max(1, int(self.data_input_default_queue_size)),
            monitor_enabled=bool(self.monitor_enabled),
            monitor_interval_ms=max(200, int(self.monitor_interval_ms)),
            monitor_window_ms=max(1000, int(self.monitor_window_ms)),
            monitor_gpu_enabled=bool(self.monitor_gpu_enabled),
        )

    def for_service(
        self,
        *,
        service_id: str,
        service_class: str,
        nats_url: str | None = None,
    ) -> "ServiceBusConfig":
        resolved_nats_url = self.nats_url if nats_url is None else str(nats_url)
        return replace(
            self,
            service_id=str(service_id),
            service_class=str(service_class),
            nats_url=resolved_nats_url,
        ).normalized()


__all__ = ["ServiceBusConfig", "_debug_state_enabled"]
