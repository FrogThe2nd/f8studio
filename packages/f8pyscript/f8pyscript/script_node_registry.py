from __future__ import annotations

from typing import Any

from f8pysdk import (
    F8Command,
    F8CommandParam,
    F8DataPortSpec,
    F8RuntimeNode,
    F8SpecEditPolicy,
    F8ServiceSchemaVersion,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
    any_schema,
    boolean_schema,
    editable_collection_edit_policy,
    integer_schema,
    string_schema,
)
from f8pysdk.runtime_node import RuntimeNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from .constants import SERVICE_CLASS
from .editor_assist_payload import pyscript_code_field_editor_assist_payload
from .script_service_node import DEFAULT_CODE, PythonScriptServiceNode


def register_specs(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    reg.register_service_spec(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=SERVICE_CLASS,
            paletteCategory="svc",
            version="0.0.1",
            label="Python Script Service",
            description="Standalone python script runtime service with lifecycle/tick/command hooks.",
            tags=["python", "script", "service"],
            rendererClass="default_svc",
            stateFields=[
                F8StateSpec(
                    name="code",
                    label="Code",
                    description="Python source code.",
                    valueSchema=string_schema(default=DEFAULT_CODE),
                    access=F8StateAccess.rw,
                    uiControl="code[python]",
                    required=True,
                    showOnNode=False,
                    editorAssist=pyscript_code_field_editor_assist_payload(),
                ),
                F8StateSpec(
                    name="lastError",
                    label="Last Error",
                    description="Last script compile/runtime error.",
                    valueSchema=string_schema(default=""),
                    access=F8StateAccess.ro,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="tickEnabled",
                    label="Tick Enabled",
                    description="Enable onTick scheduler.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="tickMs",
                    label="Tick Interval (ms)",
                    description="onTick interval in milliseconds.",
                    valueSchema=integer_schema(default=100, minimum=1),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
            ],
            commands=[
                F8Command(
                    name="grant_local_exec",
                    description="Grant local execution for this script session.",
                    required=True,
                    showOnNode=True,
                    params=[
                        F8CommandParam(
                            name="ttlMs",
                            description="Optional grant TTL in milliseconds.",
                            valueSchema=integer_schema(default=60000, minimum=1),
                            required=False,
                        )
                    ],
                ),
                F8Command(
                    name="revoke_local_exec",
                    description="Revoke local execution grant.",
                    required=True,
                    showOnNode=True,
                    params=[],
                ),
            ],
            dataInPorts=[F8DataPortSpec(name="in", description="Default data input", valueSchema=any_schema(), required=False)],
            dataOutPorts=[F8DataPortSpec(name="out", description="Default data output", valueSchema=any_schema(), required=False)],
            editPolicy=F8SpecEditPolicy(
                stateFields=editable_collection_edit_policy(),
                commands=editable_collection_edit_policy(),
                dataInPorts=editable_collection_edit_policy(),
                dataOutPorts=editable_collection_edit_policy(),
            ),
        ),
        overwrite=True,
    )

    def _service_factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
        return PythonScriptServiceNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register_service(SERVICE_CLASS, _service_factory, overwrite=True)
    return reg
