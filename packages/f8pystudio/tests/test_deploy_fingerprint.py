from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.command import command_output_state_field  # noqa: E402
from f8pysdk.specs import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
    F8RuntimeService,
    F8StateAccess,
    F8StateSpec,
)
from f8pysdk.specs import string_schema  # noqa: E402

from f8pystudio.bridge.deploy_fingerprint import (  # noqa: E402
    build_compiled_deploy_fingerprint,
    build_compiled_deploy_snapshot,
)


def _compiled(*, graph_id: str, revision: str, edge_id: str, state_value: str, edge_to_port: str = "in") -> object:
    hidden_output_name = command_output_state_field("run")
    graph = F8RuntimeGraph(
        graphId=graph_id,
        revision=revision,
        services=[
            F8RuntimeService(serviceId="svcA", serviceClass="svc.alpha"),
            F8RuntimeService(serviceId="svcB", serviceClass="svc.beta"),
        ],
        nodes=[
            F8RuntimeNode(
                nodeId="svcA",
                serviceId="svcA",
                serviceClass="svc.alpha",
                stateFields=[
                    F8StateSpec(name="value", valueSchema=string_schema(), access=F8StateAccess.rw),
                    F8StateSpec(name=hidden_output_name, valueSchema=string_schema(), access=F8StateAccess.ro),
                ],
                stateValues={"value": state_value, hidden_output_name: {"result": state_value}},
            ),
            F8RuntimeNode(
                nodeId="opB",
                serviceId="svcB",
                serviceClass="svc.beta",
                operatorClass="svc.beta.op",
                execInPorts=["tick"],
                execOutPorts=["next"],
                stateFields=[
                    F8StateSpec(name="in", valueSchema=string_schema(), access=F8StateAccess.rw),
                ],
            ),
        ],
        edges=[
            F8Edge(
                edgeId=edge_id,
                fromServiceId="svcA",
                fromOperatorId=None,
                fromPort=hidden_output_name,
                toServiceId="svcB",
                toOperatorId="opB",
                toPort=edge_to_port,
                kind=F8EdgeKindEnum.state,
                strategy=F8EdgeStrategyEnum.latest,
            )
        ],
    )
    return SimpleNamespace(global_graph=graph, per_service={}, warnings=())


def test_deploy_fingerprint_ignores_runtime_only_values_and_random_ids() -> None:
    compiled_a = _compiled(graph_id="g1", revision="r1", edge_id="edge-a", state_value="pause")
    compiled_b = _compiled(graph_id="g2", revision="r2", edge_id="edge-b", state_value="play")

    assert build_compiled_deploy_fingerprint(compiled_a) == build_compiled_deploy_fingerprint(compiled_b)

    snapshot = build_compiled_deploy_snapshot(compiled_a)
    nodes = snapshot["nodes"]
    assert isinstance(nodes, list)
    assert all("stateValues" not in node for node in nodes)
    edges = snapshot["edges"]
    assert isinstance(edges, list)
    assert all("edgeId" not in edge for edge in edges)


def test_deploy_fingerprint_changes_when_runtime_wiring_changes() -> None:
    compiled_a = _compiled(graph_id="g1", revision="r1", edge_id="edge-a", state_value="pause", edge_to_port="in")
    compiled_b = _compiled(graph_id="g1", revision="r1", edge_id="edge-b", state_value="pause", edge_to_port="other")

    assert build_compiled_deploy_fingerprint(compiled_a) != build_compiled_deploy_fingerprint(compiled_b)


def test_deploy_fingerprint_is_stable_for_unordered_runtime_collections() -> None:
    hidden_output_name = command_output_state_field("run")
    service_a = F8RuntimeService(serviceId="svcA", serviceClass="svc.alpha")
    service_b = F8RuntimeService(serviceId="svcB", serviceClass="svc.beta")
    state_field_a = F8StateSpec(name="alpha", valueSchema=string_schema(), access=F8StateAccess.rw)
    state_field_b = F8StateSpec(name="beta", valueSchema=string_schema(), access=F8StateAccess.rw)
    node_a = F8RuntimeNode(
        nodeId="svcA",
        serviceId="svcA",
        serviceClass="svc.alpha",
        stateFields=[state_field_a, state_field_b],
    )
    node_b = F8RuntimeNode(
        nodeId="opB",
        serviceId="svcB",
        serviceClass="svc.beta",
        operatorClass="svc.beta.op",
        execInPorts=["z", "a"],
        execOutPorts=["done", "next"],
        stateFields=[state_field_b, state_field_a],
    )
    edge_a = F8Edge(
        edgeId="edge-a",
        fromServiceId="svcA",
        fromOperatorId=None,
        fromPort=hidden_output_name,
        toServiceId="svcB",
        toOperatorId="opB",
        toPort="in",
        kind=F8EdgeKindEnum.state,
        strategy=F8EdgeStrategyEnum.latest,
    )
    edge_b = F8Edge(
        edgeId="edge-b",
        fromServiceId="svcB",
        fromOperatorId="opB",
        fromPort="done",
        toServiceId="svcA",
        toOperatorId=None,
        toPort="tick",
        kind=F8EdgeKindEnum.exec,
        strategy=F8EdgeStrategyEnum.latest,
    )
    graph_a = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        services=[service_a, service_b],
        nodes=[node_a, node_b],
        edges=[edge_a, edge_b],
    )
    graph_b = F8RuntimeGraph(
        graphId="g2",
        revision="r2",
        services=[service_b, service_a],
        nodes=[node_b, node_a],
        edges=[edge_b, edge_a],
    )

    compiled_a = SimpleNamespace(global_graph=graph_a, per_service={}, warnings=())
    compiled_b = SimpleNamespace(global_graph=graph_b, per_service={}, warnings=())

    assert build_compiled_deploy_fingerprint(compiled_a) == build_compiled_deploy_fingerprint(compiled_b)
