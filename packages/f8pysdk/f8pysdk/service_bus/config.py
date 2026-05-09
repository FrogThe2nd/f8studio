from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Literal

from ..data import CrossPublishPolicy, DataDeliveryMode


BusBackend = Literal["zenoh", "mem"]
DEFAULT_ZENOH_SHM_POOL_BYTES = 256 * 1024 * 1024


def _debug_state_enabled() -> bool:
    return str(os.getenv("F8_STATE_DEBUG", "")).lower() in ("1", "true", "yes", "on")


def _normalize_backend(value: str) -> BusBackend:
    text = str(value or "").strip().lower()
    if text == "zenoh":
        return "zenoh"
    if text == "mem":
        return "mem"
    raise ValueError(f"Invalid bus_backend={value!r}; expected 'zenoh' or 'mem'.")


@dataclass(frozen=True)
class ServiceBusConfig:
    service_id: str = ""
    service_name: str | None = None
    service_class: str | None = None
    bus_backend: BusBackend = "zenoh"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = DEFAULT_ZENOH_SHM_POOL_BYTES
    cross_publish_policy: CrossPublishPolicy = "routed"
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
        zenoh_config_path = str(self.zenoh_config_path or "").strip() or None
        return ServiceBusConfig(
            service_id=str(self.service_id or "").strip(),
            service_name=service_name,
            service_class=service_class,
            bus_backend=_normalize_backend(self.bus_backend),
            zenoh_config_path=zenoh_config_path,
            zenoh_connect=tuple(str(item).strip() for item in self.zenoh_connect if str(item).strip()),
            zenoh_listen=tuple(str(item).strip() for item in self.zenoh_listen if str(item).strip()),
            zenoh_shm_pool_bytes=max(0, int(self.zenoh_shm_pool_bytes)),
            cross_publish_policy=self.cross_publish_policy,
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
        bus_backend: BusBackend | str | None = None,
        zenoh_config_path: str | None = None,
        zenoh_connect: tuple[str, ...] | None = None,
        zenoh_listen: tuple[str, ...] | None = None,
        zenoh_shm_pool_bytes: int | None = None,
    ) -> "ServiceBusConfig":
        return replace(
            self,
            service_id=str(service_id),
            service_class=str(service_class),
            bus_backend=self.bus_backend if bus_backend is None else _normalize_backend(str(bus_backend)),
            zenoh_config_path=self.zenoh_config_path if zenoh_config_path is None else str(zenoh_config_path),
            zenoh_connect=self.zenoh_connect if zenoh_connect is None else tuple(zenoh_connect),
            zenoh_listen=self.zenoh_listen if zenoh_listen is None else tuple(zenoh_listen),
            zenoh_shm_pool_bytes=(
                self.zenoh_shm_pool_bytes if zenoh_shm_pool_bytes is None else int(zenoh_shm_pool_bytes)
            ),
        ).normalized()


__all__ = ["BusBackend", "DEFAULT_ZENOH_SHM_POOL_BYTES", "ServiceBusConfig", "_debug_state_enabled"]
