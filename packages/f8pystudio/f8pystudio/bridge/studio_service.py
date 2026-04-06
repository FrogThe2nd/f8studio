from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.service_runtime import ServiceRuntime, ServiceRuntimeConfig

from f8pystudio.plugins.loader import load_entrypoint_plugins
from f8pystudio.contracts.ui_commands import set_ui_command_sink, UiCommand
from f8pystudio.operators import register_operator
from f8pystudio.studio_specs.registry import SERVICE_CLASS, STUDIO_SERVICE_ID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PyStudioServiceConfig:
    nats_url: str = "nats://127.0.0.1:4222"
    studio_service_id: str = STUDIO_SERVICE_ID


class PyStudioService:
    """
    PyStudio in-process "service" wiring.

    Mirrors the clarity of `pyengine_service.py`, but for the Qt app:
    - builds the runtime registry
    - registers studio runtime nodes/operators
    - constructs and starts a `ServiceRuntime` (ServiceBus + ServiceHost)
    - wires preview + state updates to UI callbacks
    """

    def __init__(
        self,
        config: PyStudioServiceConfig,
        *,
        registry: RuntimeNodeRegistry | None = None,
    ) -> None:
        self._cfg = config
        self._registry = registry or RuntimeNodeRegistry.instance()
        self.runtime: ServiceRuntime | None = None

    @property
    def studio_service_id(self) -> str:
        return str(self._cfg.studio_service_id)

    @property
    def bus(self):
        if self.runtime is None:
            return None
        return self.runtime.bus

    async def start(
        self,
        *,
        on_ui_command: Callable[[UiCommand], None] | None,
    ) -> None:
        # Register studio operators into the shared registry.
        register_operator(self._registry)
        for manifest in load_entrypoint_plugins():
            for op_reg in manifest.operators:
                try:
                    out_registry = op_reg.register(self._registry)
                except Exception:
                    logger.exception("Plugin operator registration failed plugin_id=%s", manifest.plugin_id)
                    continue
                if out_registry is not self._registry:
                    logger.warning(
                        "Plugin '%s' returned a different RuntimeNodeRegistry; keeping current instance.",
                        manifest.plugin_id,
                    )

        cfg = ServiceRuntimeConfig.from_values(
            service_id=str(self._cfg.studio_service_id),
            service_class=SERVICE_CLASS,
            nats_url=str(self._cfg.nats_url),
            cross_publish_policy="routed",
            data_delivery="callback",
        )
        self.runtime = ServiceRuntime(cfg, registry=self._registry)

        if on_ui_command is not None:
            set_ui_command_sink(on_ui_command)
        else:
            set_ui_command_sink(None)

        await self.runtime.start()

    async def stop(self) -> None:
        set_ui_command_sink(None)
        rt = self.runtime
        self.runtime = None
        if rt is None:
            return
        await rt.stop()
