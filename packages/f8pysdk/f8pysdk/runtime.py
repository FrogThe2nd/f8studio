from __future__ import annotations

from dataclasses import dataclass

from nats.js.api import StorageType  # type: ignore[import-not-found]

from .bus import ServiceBus, ServiceBusConfig
from .data import CrossPublishPolicy
from .host import ServiceHost
from .registry import RuntimeNodeRegistry, create_runtime_node_registry


@dataclass(frozen=True)
class ServiceRuntimeConfig:
    """
    Runtime facade for a service process.

    This bundles:
    - `ServiceBus`: NATS+KV transport, routing, state cache
    - `ServiceHost`: rungraph-driven node creation and registration
    - `RuntimeNodeRegistry`: node factory registry (optionally loaded from modules)
    """

    bus: ServiceBusConfig
    registry_modules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        modules = tuple(str(module).strip() for module in self.registry_modules if str(module).strip())
        object.__setattr__(self, "bus", self.bus.normalized())
        object.__setattr__(self, "registry_modules", modules)

    @property
    def service_id(self) -> str:
        return str(self.bus.service_id)

    @property
    def service_class(self) -> str:
        return str(self.bus.service_class or "")

    @property
    def nats_url(self) -> str:
        return str(self.bus.nats_url)

    @property
    def cross_publish_policy(self) -> CrossPublishPolicy:
        return self.bus.cross_publish_policy

    @property
    def kv_storage(self) -> StorageType:
        return self.bus.kv_storage

    @property
    def delete_bucket_on_start(self) -> bool:
        return bool(self.bus.delete_bucket_on_start)

    @property
    def delete_bucket_on_stop(self) -> bool:
        return bool(self.bus.delete_bucket_on_stop)


class ServiceRuntime:
    """
    Process-level runtime facade that wires together `ServiceBus` and `ServiceHost`.
    """

    def __init__(
        self,
        config: ServiceRuntimeConfig,
        *,
        registry: RuntimeNodeRegistry | None = None,
    ) -> None:
        self._config = config
        self._registry = registry if registry is not None else create_runtime_node_registry()
        self._closed = False

        for module in config.registry_modules:
            self._registry.load_modules([str(module)])

        self.bus = ServiceBus(config.bus)
        self.host = ServiceHost(self.bus, registry=self._registry)

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("ServiceRuntime is not restartable after stop(); create a new instance")
        await self.host.start()
        await self.bus.start()

    async def stop(self) -> None:
        await self.bus.stop()
        self._closed = True

__all__ = ["ServiceRuntime", "ServiceRuntimeConfig"]
