from __future__ import annotations

from typing import Any


def graph_build_plan_schema_hint() -> dict[str, Any]:
    return {
        "type": "GraphBuildPlan",
        "description": "Typed graph construction plan. Use library/catalog tools to choose real nodeType and port names.",
        "requiredFields": ["summary", "requirement", "nodes"],
        "fields": {
            "summary": "Short human-readable description of the graph being built.",
            "requirement": {
                "goal": "Original user goal.",
                "serviceHints": ["Relevant service classes or labels."],
                "operatorHints": ["Relevant operator classes or labels."],
                "dataFlowHints": ["Expected signal/control flow."],
                "validationHints": ["How the agent should validate the graph after deploy."],
                "visualizationHints": ["Expected visual output nodes, if any."],
            },
            "candidates": [
                {
                    "kind": "service | operator",
                    "nodeType": "Valid canvas node type from node_catalog.",
                    "serviceClass": "Service class, if known.",
                    "operatorClass": "Operator class, if known.",
                    "label": "Catalog label.",
                    "description": "Catalog description.",
                    "score": 0.0,
                    "matchedTerms": ["goal terms matched by this candidate"],
                }
            ],
            "nodes": [
                {
                    "nodeType": "Valid canvas node type from node_catalog.",
                    "nodeId": "Stable unique node id to create.",
                    "name": "Readable node label.",
                    "role": "Why this node exists in the graph.",
                    "stateValues": {"fieldName": "value"},
                    "position": [0.0, 0.0],
                }
            ],
            "connections": [
                {
                    "fromNodeId": "Source node id.",
                    "fromPort": "Source output port name.",
                    "toNodeId": "Target node id.",
                    "toPort": "Target input port name.",
                    "reason": "Why this edge is required.",
                }
            ],
            "validationTargets": [
                {
                    "serviceId": "Runtime service id to sample.",
                    "nodeId": "Runtime node id to sample.",
                    "port": "Output port to sample.",
                    "description": "Expected runtime behavior.",
                    "expectedMin": 0.0,
                    "expectedMax": 100.0,
                }
            ],
        },
        "workflow": [
            "Call graph_match_library or graph_build_from_goal for candidates.",
            "Inspect node_catalog/operator_detail for exact ports and state fields.",
            "Create a GraphBuildPlan with real nodeType, nodeId, stateValues, and connections.",
            "Call graph_preview_build_plan with the plan.",
            "Call graph_apply_build_plan only after the user approves or clearly asks to build.",
            "Deploy/sample/debug with runtime and monitor tools, then summarize the runnable graph.",
        ],
    }
