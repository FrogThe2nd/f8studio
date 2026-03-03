from __future__ import annotations

from typing import Any

from f8pysdk import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from ..constants import SERVICE_CLASS

OPERATOR_CLASS = "f8.note"
RENDERER_CLASS = "note_markdown"
_DEFAULT_NOTE_CONTENT = "# Note\n\n- Write tips here.\n- Supports **Markdown**.\n"


class NoteRuntimeNode(OperatorNode):
    """
    Studio-only note node.

    This runtime node intentionally has no data/exec behavior and only serves
    as a persistent state carrier for UI note content.
    """

    SPEC = F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        version="0.0.1",
        label="Note",
        description="A markdown note node for tips and reminders inside the canvas.",
        tags=["note", "markdown", "ui", "tips"],
        dataInPorts=[],
        dataOutPorts=[],
        execInPorts=[],
        execOutPorts=[],
        rendererClass=RENDERER_CLASS,
        editableStateFields=False,
        stateFields=[
            F8StateSpec(
                name="content",
                label="Content",
                description="Markdown note content shown in the node.",
                valueSchema=string_schema(default=_DEFAULT_NOTE_CONTENT),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
        ],
    )

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        del initial_state
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
            exec_in_ports=list(node.execInPorts or []),
            exec_out_ports=list(node.execOutPorts or []),
        )


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return NoteRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(NoteRuntimeNode.SPEC, overwrite=True)
    return reg
