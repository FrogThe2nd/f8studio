from __future__ import annotations

import pytest

from f8pysdk.specs import F8DataPortSpec, number_schema
from f8pystudio.agents.graph_builder import (
    decode_graph_build_plan,
    graph_build_plan_schema_hint,
    graph_patch_from_build_plan,
    match_graph_library_candidates,
)
from f8pystudio.assets.components.component_compatibility import SemanticSignal
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentSourceKind
from f8pystudio.automation.domain import graph_patch_to_dict
from f8pysdk.specs import F8ComponentRecord


def test_match_graph_library_candidates_ranks_goal_terms() -> None:
    catalog = {
        "nodes": [
            {
                "nodeType": "svc.f8.test",
                "label": "Test Engine",
                "kind": "service",
                "serviceClass": "f8.test",
                "operatorClass": "",
                "outputs": [],
                "stateFields": [],
            },
            {
                "nodeType": "f8.test.range_map",
                "label": "Range Map",
                "kind": "operator",
                "serviceClass": "f8.test",
                "operatorClass": "range_map",
                "inputs": [{"name": "value", "kind": "data"}],
                "outputs": [{"name": "value", "kind": "data"}],
                "stateFields": [{"name": "outMax", "description": "0 to 100 output maximum"}],
            },
            {
                "nodeType": "f8.test.text",
                "label": "Text",
                "kind": "operator",
                "serviceClass": "f8.test",
                "operatorClass": "text",
                "inputs": [{"name": "text", "kind": "data"}],
                "outputs": [],
                "stateFields": [],
            },
        ]
    }

    result = match_graph_library_candidates(goal="map signal into 0-100 range", node_catalog=catalog, limit=4)

    assert result.query_terms == ("map", "signal", "100", "range")
    assert [candidate.node_type for candidate in result.candidates] == ["f8.test.range_map"]
    assert result.candidates[0].matched_terms == ("map", "100", "range")


def test_match_graph_library_candidates_returns_component_taxonomy_and_compatibility() -> None:
    component = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="lovense-output",
            name="Lovense Output",
            description="Drive Lovense from a normalized signal.",
            tags=[
                "role:output",
                "workflow:video",
                "signal:vibrate",
                "protocol:lovense",
            ],
            content={},
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )

    result = match_graph_library_candidates(
        goal="video vibrate lovense output",
        node_catalog={"nodes": []},
        component_entries=[component],
        source_port=F8DataPortSpec(
            name="intensity",
            valueSchema=number_schema(minimum=0.0, maximum=1.0),
        ),
        signal=SemanticSignal.VIBRATE,
    )
    payload = result.to_dict()["components"][0]

    assert payload["componentId"] == "lovense-output"
    assert payload["role"] == "output"
    assert payload["workflows"] == ["video"]
    assert payload["signals"] == ["vibrate"]
    assert payload["protocols"] == ["lovense"]
    assert payload["compatibility"] == {
        "evaluated": True,
        "compatible": True,
        "signal": "vibrate",
        "sourcePort": "intensity",
        "reasons": [],
        "warnings": [],
    }


def test_component_library_match_marks_compatibility_as_not_evaluated_without_port_context() -> None:
    component = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="tcode-output",
            name="TCode Output",
            tags=["role:output", "signal:tcode", "protocol:serial"],
            content={},
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )

    result = match_graph_library_candidates(
        goal="tcode serial output",
        node_catalog={"nodes": []},
        component_entries=[component],
    )

    assert result.components[0].compatibility.evaluated is False
    assert result.components[0].compatibility.compatible is None


def test_decode_graph_build_plan_and_patch_are_typed_camel_case_payloads() -> None:
    payload = {
        "summary": "Build a tiny dataflow graph.",
        "requirement": {
            "goal": "generate and display a mapped wave",
            "serviceHints": ["f8.test"],
            "operatorHints": ["source", "display"],
            "dataFlowHints": ["source.value -> display.y"],
            "validationHints": ["sample source.value"],
            "visualizationHints": ["display y"],
        },
        "nodes": [
            {
                "nodeType": "svc.f8.test",
                "nodeId": "service",
                "name": "Service",
                "role": "Container",
                "position": [0, 0],
            },
            {
                "nodeType": "f8.test.source",
                "nodeId": "source",
                "name": "Source",
                "role": "Generate value",
                "stateValues": {"svcId": "service", "hz": 1},
                "position": [200, 0],
            },
            {
                "nodeType": "f8.test.display",
                "nodeId": "display",
                "name": "Display",
                "role": "Visualize value",
                "stateValues": {"svcId": "service"},
                "position": [400, 0],
            },
        ],
        "connections": [
            {
                "fromNodeId": "source",
                "fromPort": "value",
                "toNodeId": "display",
                "toPort": "y",
                "reason": "Show the generated value.",
            }
        ],
        "validationTargets": [
            {
                "serviceId": "service",
                "nodeId": "source",
                "port": "value",
                "description": "Source emits samples.",
                "expectedMin": 0,
                "expectedMax": 100,
            }
        ],
    }

    plan = decode_graph_build_plan(payload)
    plan_payload = plan.to_dict()
    patch_payload = graph_patch_to_dict(graph_patch_from_build_plan(plan, expected_revision=12))

    assert plan_payload["requirement"] == {
        "goal": "generate and display a mapped wave",
        "serviceHints": ["f8.test"],
        "operatorHints": ["source", "display"],
        "dataFlowHints": ["source.value -> display.y"],
        "validationHints": ["sample source.value"],
        "visualizationHints": ["display y"],
    }
    assert patch_payload["expectedRevision"] == 12
    assert [op["op"] for op in patch_payload["ops"]] == [
        "createNode",
        "createNode",
        "setNodeState",
        "setNodeState",
        "createNode",
        "setNodeState",
        "connectPorts",
    ]
    assert patch_payload["ops"][2] == {"op": "setNodeState", "nodeId": "source", "field": "svcId", "value": "service"}
    assert patch_payload["ops"][-1] == {
        "op": "connectPorts",
        "fromNodeId": "source",
        "fromPort": "value",
        "toNodeId": "display",
        "toPort": "y",
    }


def test_decode_graph_build_plan_rejects_missing_nodes() -> None:
    with pytest.raises(ValueError, match="non-empty nodes"):
        decode_graph_build_plan({"summary": "bad", "requirement": {"goal": "x"}, "nodes": []})


def test_graph_build_plan_schema_hint_documents_generic_workflow() -> None:
    schema = graph_build_plan_schema_hint()

    assert schema["type"] == "GraphBuildPlan"
    assert "nodes" in schema["fields"]
    assert "workflow" in schema
    assert any("graph_match_library" in step for step in schema["workflow"])
