from __future__ import annotations

from typing import Any

from f8pysdk.nodes import ServiceNode
from f8pysdk.specs import F8RuntimeNode, F8ServiceSchemaVersion, F8ServiceSpec
from f8pysdk.specs import F8StateAccess, F8StateSpec, integer_schema
from f8pysdk.registry import Registry, RuntimeNodeRegistry, create_runtime_node_registry, shared_runtime_node_registry

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS, STUDIO_SERVICE_ID


class PyStudioServiceNode(ServiceNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=str(node_id),
            data_in_ports=[port.name for port in list(node.dataInPorts or [])],
            data_out_ports=[port.name for port in list(node.dataOutPorts or [])],
            state_fields=[field.name for field in list(node.stateFields or [])],
        )
        self._initial_state = dict(initial_state or {})


def register_pystudio_specs(registry: Registry) -> Registry:
    """
    Register f8.pystudio service/operator specs for discovery / `--describe`.
    """
    from f8pystudio.operators import register_operator

    registry.register_service(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=SERVICE_CLASS,
            version="0.0.1",
            label="PyStudio",
            description="Service Graph Editor in Python and Qt.",
            tags=["editor", "ui", "python", "py"],
            paletteCategory="svc",
            hiddenInPalette=True,
            rendererClass="default_svc",
            stateFields=[
                F8StateSpec(
                    name="tickMs",
                    label="Refresh Interval (ms)",
                    description="Interval in milliseconds for refreshing the UI nodes in the editor.",
                    valueSchema=integer_schema(default=100, minimum=16, maximum=5000),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=True,
                ),
            ],
        ),
        PyStudioServiceNode,
        overwrite=True,
    )

    register_operator(registry)

    return registry


def create_pystudio_registry() -> RuntimeNodeRegistry:
    runtime_registry = create_runtime_node_registry()
    register_pystudio_specs(Registry.wrap(runtime_registry))
    return runtime_registry


def shared_pystudio_registry() -> RuntimeNodeRegistry:
    runtime_registry = shared_runtime_node_registry()
    register_pystudio_specs(Registry.wrap(runtime_registry))
    return runtime_registry
