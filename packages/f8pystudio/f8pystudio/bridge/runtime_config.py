from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from f8pysdk.bus import BusBackend
from f8pysdk.service_bus.config import DEFAULT_ZENOH_SHM_POOL_BYTES
from f8pystudio.studio_specs.registry import STUDIO_SERVICE_ID


@dataclass(frozen=True)
class PyStudioServiceBridgeConfig:
    bus_backend: BusBackend = "zenoh"
    supervision_mode: Literal["studio_owned", "detached"] = "studio_owned"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = DEFAULT_ZENOH_SHM_POOL_BYTES
    studio_service_id: str = STUDIO_SERVICE_ID


__all__ = ["PyStudioServiceBridgeConfig"]
