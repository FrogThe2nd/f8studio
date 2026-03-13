from __future__ import annotations

import logging

from f8pysdk.capabilities import ClosableNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.service_cli import ServiceCliTemplate
from f8pysdk.service_runtime import ServiceRuntime

from .constants import SERVICE_CLASS
from .proclauncher_node_registry import register_proclauncher_specs

logger = logging.getLogger(__name__)


class ProcLauncherService(ServiceCliTemplate):
    def __init__(self) -> None:
        self._runtime: ServiceRuntime | None = None

    @property
    def service_class(self) -> str:
        return SERVICE_CLASS

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        register_proclauncher_specs(registry)

    async def setup(self, runtime: ServiceRuntime) -> None:
        self._runtime = runtime

    async def teardown(self, runtime: ServiceRuntime) -> None:
        try:
            node = runtime.bus.get_node(runtime.bus.service_id)
            if node is not None and isinstance(node, ClosableNode):
                await node.close()
        except Exception:
            logger.exception("service teardown: close failed service_id=%s", runtime.bus.service_id)
        finally:
            self._runtime = None

