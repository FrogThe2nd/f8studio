from __future__ import annotations

import logging

from f8pysdk.app import ServiceApp
from f8pysdk.capabilities import ClosableNode
from f8pysdk.registry import Registry
from f8pysdk.runtime import ServiceRuntime

from .constants import SERVICE_CLASS
from .proclauncher_node_registry import register_proclauncher_specs

logger = logging.getLogger(__name__)


async def _teardown(runtime: ServiceRuntime) -> None:
    try:
        node = runtime.bus.get_node(runtime.bus.service_id)
        if node is not None and isinstance(node, ClosableNode):
            await node.close()
    except Exception:
        logger.exception("service teardown: close failed service_id=%s", runtime.bus.service_id)


def build_app() -> ServiceApp:
    registry = Registry()
    register_proclauncher_specs(registry.runtime_registry)
    return ServiceApp(
        service_class=SERVICE_CLASS,
        registry=registry,
        teardown=_teardown,
    )
