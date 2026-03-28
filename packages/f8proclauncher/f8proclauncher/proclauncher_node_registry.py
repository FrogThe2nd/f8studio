from __future__ import annotations

from typing import Any

from f8pysdk import F8ServiceSchemaVersion, F8ServiceSpec, F8StateAccess, F8StateSpec, boolean_schema, string_schema
from f8pysdk.generated import F8RuntimeNode
from f8pysdk.runtime_node import RuntimeNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from .constants import SERVICE_CLASS
from .proclauncher_service_node import ProcLauncherServiceNode


def register_proclauncher_specs(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    reg.register_service_spec(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=SERVICE_CLASS,
            paletteCategory="svc",
            version="0.0.1",
            label="Proc Launcher",
            description="Launches an external OS process (optionally detached).",
            tags=["utility", "process", "launcher"],
            rendererClass="default_svc",
            stateFields=[
                F8StateSpec(
                    name="programPath",
                    label="Program Path",
                    description="Executable path or command line (quoted if it contains spaces). Cleared when exporting publish JSON.",
                    valueSchema=string_schema(default=""),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=True,
                    redactOnPublish=True,
                ),
                F8StateSpec(
                    name="singleton",
                    label="Singleton",
                    description="If true, do not start if a previous launch for the same command is still running.",
                    valueSchema=boolean_schema(default=True),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="detached",
                    label="Detached",
                    description="If true, do not stop the launched process when this service stops.",
                    valueSchema=boolean_schema(default=True),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
            ],
        ),
        overwrite=True,
    )

    def _service_factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
        return ProcLauncherServiceNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register_service(SERVICE_CLASS, _service_factory, overwrite=True)
    return reg
