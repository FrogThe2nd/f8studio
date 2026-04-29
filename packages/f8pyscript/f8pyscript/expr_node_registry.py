from __future__ import annotations

from typing import Any

from f8pysdk.specs import (
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
    string_schema,
)
from f8pysdk.nodes import RuntimeNode
from f8pysdk.registry import create_runtime_node_registry, RuntimeNodeRegistry, shared_runtime_node_registry

from .constants import EXPR_SERVICE_CLASS
from .expr_service_node import DEFAULT_CODE, PythonExprServiceNode


def register_expr_specs(registry: RuntimeNodeRegistry) -> RuntimeNodeRegistry:
    registry.register_service_spec(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=EXPR_SERVICE_CLASS,
            paletteCategory="svc",
            version="0.0.1",
            label="Python Expr Service",
            description="Standalone expression runtime service for simplified data-flow transforms.",
            tags=["python", "expr", "service"],
            rendererClass="default_svc",
            stateFields=[
                F8StateSpec(
                    name="code",
                    label="Expr",
                    description="Single-line expression. Available names: inputs + identifier-safe input ports.",
                    valueSchema=string_schema(default=DEFAULT_CODE),
                    access=F8StateAccess.rw,
                    required=True,
                    uiControl="wrapline[python]",
                    showOnNode=True,
                ),
                F8StateSpec(
                    name="allowNumpy",
                    label="Allow Numpy",
                    description="Enable numpy calls in expressions (np.*, numpy.*).",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.wo,
                    required=True,
                    uiControl="toggle",
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="unpackDictOutputs",
                    label="Unpack Dict Outputs",
                    description="When enabled, dict results are emitted per matching output port key.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.wo,
                    required=True,
                    uiControl="toggle",
                    showOnNode=False,
                ),
            ],
            dataInPorts=[F8DataPortSpec(name="msg", description="Default input value.", valueSchema=any_schema(), required=False)],
            dataOutPorts=[
                F8DataPortSpec(name="out", description="Default expression output value.", valueSchema=any_schema(), required=False)
            ],
            editPolicy=F8SpecEditPolicy(
                dataInPorts=editable_collection_edit_policy(),
                dataOutPorts=editable_collection_edit_policy(),
            ),
        ),
        overwrite=True,
    )

    def _service_factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
        return PythonExprServiceNode(node_id=node_id, node=node, initial_state=initial_state)

    registry.register_service_factory(EXPR_SERVICE_CLASS, _service_factory, overwrite=True)
    return registry


def create_pyexpr_registry() -> RuntimeNodeRegistry:
    return register_expr_specs(create_runtime_node_registry())


def shared_pyexpr_registry() -> RuntimeNodeRegistry:
    return register_expr_specs(shared_runtime_node_registry())
