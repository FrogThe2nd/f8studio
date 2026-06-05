from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from f8pystudio.automation.domain import ConnectPortsOp, CreateNodeOp, GraphPatch, SetNodeStateOp, graph_patch_to_dict

GraphBuildStatus = Literal["planned", "unsupported_goal", "invalid_plan"]
GraphBuildCandidateKind = Literal["service", "operator"]


@dataclass(frozen=True)
class GraphBuildRequirement:
    goal: str
    service_hints: tuple[str, ...] = ()
    operator_hints: tuple[str, ...] = ()
    data_flow_hints: tuple[str, ...] = ()
    validation_hints: tuple[str, ...] = ()
    visualization_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "serviceHints": list(self.service_hints),
            "operatorHints": list(self.operator_hints),
            "dataFlowHints": list(self.data_flow_hints),
            "validationHints": list(self.validation_hints),
            "visualizationHints": list(self.visualization_hints),
        }


@dataclass(frozen=True)
class GraphBuildCandidate:
    kind: GraphBuildCandidateKind
    node_type: str
    service_class: str
    operator_class: str = ""
    label: str = ""
    description: str = ""
    score: float = 0.0
    matched_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "nodeType": self.node_type,
            "serviceClass": self.service_class,
            "operatorClass": self.operator_class,
            "label": self.label,
            "description": self.description,
            "score": self.score,
            "matchedTerms": list(self.matched_terms),
        }


@dataclass(frozen=True)
class GraphNodePlan:
    node_type: str
    node_id: str
    name: str
    role: str
    state_values: tuple[tuple[str, Any], ...] = ()
    position: tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeType": self.node_type,
            "nodeId": self.node_id,
            "name": self.name,
            "role": self.role,
            "stateValues": {key: value for key, value in self.state_values},
            "position": [float(self.position[0]), float(self.position[1])],
        }


@dataclass(frozen=True)
class GraphConnectionPlan:
    from_node_id: str
    from_port: str
    to_node_id: str
    to_port: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fromNodeId": self.from_node_id,
            "fromPort": self.from_port,
            "toNodeId": self.to_node_id,
            "toPort": self.to_port,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GraphValidationTarget:
    service_id: str
    node_id: str
    port: str
    description: str = ""
    expected_min: float | None = None
    expected_max: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "serviceId": self.service_id,
            "nodeId": self.node_id,
            "port": self.port,
            "description": self.description,
            "expectedMin": self.expected_min,
            "expectedMax": self.expected_max,
        }


@dataclass(frozen=True)
class GraphBuildPlan:
    status: GraphBuildStatus
    requirement: GraphBuildRequirement
    summary: str
    candidates: tuple[GraphBuildCandidate, ...] = ()
    nodes: tuple[GraphNodePlan, ...] = ()
    connections: tuple[GraphConnectionPlan, ...] = ()
    validation_targets: tuple[GraphValidationTarget, ...] = ()
    unsupported_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        patch = graph_patch_from_build_plan(self) if self.status == "planned" else None
        return {
            "status": self.status,
            "summary": self.summary,
            "requirement": self.requirement.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "nodes": [node.to_dict() for node in self.nodes],
            "connections": [connection.to_dict() for connection in self.connections],
            "validationTargets": [target.to_dict() for target in self.validation_targets],
            "patch": None if patch is None else graph_patch_to_dict(patch),
            "unsupportedReason": self.unsupported_reason,
        }


@dataclass(frozen=True)
class GraphBuildDeliveryReport:
    status: str
    summary: str
    plan: GraphBuildPlan
    preview: dict[str, Any]
    diagnostics: dict[str, Any] | None = None
    runtime_validation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "plan": self.plan.to_dict(),
            "preview": dict(self.preview),
            "diagnostics": None if self.diagnostics is None else dict(self.diagnostics),
            "runtimeValidation": None if self.runtime_validation is None else dict(self.runtime_validation),
        }


def graph_patch_from_build_plan(plan: GraphBuildPlan, *, expected_revision: int | None = None) -> GraphPatch:
    if plan.status != "planned":
        raise ValueError("cannot build GraphPatch from an unplanned graph build plan")
    ops: list[object] = []
    for node in plan.nodes:
        ops.append(
            CreateNodeOp(
                node_type=node.node_type,
                node_id=node.node_id,
                name=node.name,
                pos=node.position,
            )
        )
        for field, value in node.state_values:
            ops.append(SetNodeStateOp(node_id=node.node_id, field=field, value=value))
    for connection in plan.connections:
        ops.append(
            ConnectPortsOp(
                from_node_id=connection.from_node_id,
                from_port=connection.from_port,
                to_node_id=connection.to_node_id,
                to_port=connection.to_port,
            )
        )
    return GraphPatch(
        expected_revision=expected_revision,
        label=f"agent graph build: {plan.requirement.goal[:80]}",
        ops=tuple(ops),
    )


def delivery_report_for_plan(
    *,
    plan: GraphBuildPlan,
    preview: dict[str, Any],
    applied: bool,
    diagnostics: dict[str, Any] | None = None,
    runtime_validation: dict[str, Any] | None = None,
) -> GraphBuildDeliveryReport:
    return GraphBuildDeliveryReport(
        status="applied" if applied else "previewed",
        summary=(
            "Graph changes were applied; the graph is ready for runtime validation."
            if applied
            else "Graph build plan was previewed and is ready for approval."
        ),
        plan=plan,
        preview=preview,
        diagnostics=diagnostics,
        runtime_validation=runtime_validation,
    )
