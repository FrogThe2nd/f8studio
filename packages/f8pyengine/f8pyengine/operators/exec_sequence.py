from __future__ import annotations

from typing import Any

from f8pysdk.specs import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8SpecEditPolicy,
    editable_collection_edit_policy,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.nodes import OperatorNode
from f8pysdk.registry import RuntimeNodeRegistry

from ..constants import SERVICE_CLASS
from ._ports import exec_out_ports

OPERATOR_CLASS = "f8.exec_sequence"


class ExecSequenceRuntimeNode(OperatorNode):
    """
    UE-style Sequence:
    - one exec input
    - N exec outputs, executed in order (0,1,2...) under DFS scheduling
    """

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
        )
        self._exec_out_ports = exec_out_ports(node)

    async def on_exec(self, _exec_id: str | int, _in_port: str | None = None) -> list[str]:
        return list(self._exec_out_ports)


ExecSequenceRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    paletteCategory=f"{SERVICE_CLASS}.execution",
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="Sequence",
    description="Exec flow splitter: triggers its exec outputs in order (requires DFS scheduling).",
    tags=["execution", "flow", "sequence", "branch"],
    execInPorts=["exec"],
    execOutPorts=["0", "1", "2"],
    editPolicy=F8SpecEditPolicy(execOutPorts=editable_collection_edit_policy()),
)


def register_operator(registry: RuntimeNodeRegistry) -> RuntimeNodeRegistry:

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return ExecSequenceRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    registry.register_operator_factory(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    registry.register_operator_spec(ExecSequenceRuntimeNode.SPEC, overwrite=True)
    return registry

