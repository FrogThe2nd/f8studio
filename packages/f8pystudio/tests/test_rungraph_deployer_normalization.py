from __future__ import annotations

import msgspec

from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode
from f8pysdk.msgspec_codec import dump_json

from f8pystudio.bridge.rungraph_deployer import NatsRungraphGateway, RungraphDeployConfig


def test_normalize_graph_for_request_omits_null_operator_class_for_service_nodes() -> None:
    graph = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        nodes=[
            F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None),
            F8RuntimeNode(nodeId="op1", serviceId="svc1", serviceClass="svc.a", operatorClass="svc.a.op"),
        ],
        edges=[],
    )
    gateway = NatsRungraphGateway(RungraphDeployConfig(nats_url="nats://127.0.0.1:4222"))
    normalized = gateway._normalize_graph_for_request(graph)

    assert isinstance(normalized.nodes[0].operatorClass, msgspec.UnsetType)
    payload = dump_json(normalized, mode="json", by_alias=True)
    assert isinstance(payload, dict)
    nodes_payload = payload.get("nodes")
    assert isinstance(nodes_payload, list)
    assert "operatorClass" not in nodes_payload[0]
    assert nodes_payload[1].get("operatorClass") == "svc.a.op"

