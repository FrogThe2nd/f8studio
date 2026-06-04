from __future__ import annotations

import asyncio

from qtpy import QtWidgets

from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.specs import F8Edge, F8EdgeKindEnum, F8RuntimeGraph, F8RuntimeNode
from f8pysdk.host import ServiceHost, ServiceHostConfig
from f8pysdk.nodes import ComputableNode
from f8pysdk.testing import ServiceBusHarness
from f8pyengine.pyengine_node_registry import create_pyengine_registry
from f8pyengine.constants import SERVICE_CLASS as PYENGINE_SERVICE_CLASS
from f8pystudio.agents.workflows.pyengine_sine_graph import (
    COSINE_NODE_TYPE,
    PHASE_NODE_TYPE,
    PYENGINE_SERVICE_NODE_TYPE,
    RANGE_MAP_NODE_TYPE,
    PyEngineSineGraphNodeIds,
    build_pyengine_sine_0_100_patch,
    pyengine_sine_expected_range,
    pyengine_sine_sample_port,
)
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.automation.domain import graph_patch_to_dict
from f8pystudio.automation.graph_adapter import StudioGraphAutomationAdapter
from f8pystudio.nodegraph.node_graph import F8StudioGraph


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def _graph_with_pyengine_nodes() -> F8StudioGraph:
    _ensure_app()
    catalog = ServiceCatalog.instance()
    catalog.clear()
    registry = Registry.wrap(create_pyengine_registry())
    service_spec = registry.service_spec("f8.pyengine")
    assert service_spec is not None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs("f8.pyengine"):
        catalog.register_operator(operator_spec)
    graph = F8StudioGraph(asset_cache_auto_refresh=False)
    graph.node_factory.clear_registered_nodes()
    for node_cls in PyStudioProgram.build_node_classes():
        graph.node_factory.register_node(node_cls)
    return graph


def test_typed_pyengine_sine_patch_matches_catalog_and_payload() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    node_ids = PyEngineSineGraphNodeIds()
    patch = build_pyengine_sine_0_100_patch(expected_revision=adapter.revision(), node_ids=node_ids)

    catalog_node_types = {item["nodeType"] for item in adapter.node_catalog()["nodes"]}
    assert PYENGINE_SERVICE_NODE_TYPE in catalog_node_types
    assert PHASE_NODE_TYPE in catalog_node_types
    assert COSINE_NODE_TYPE in catalog_node_types
    assert RANGE_MAP_NODE_TYPE in catalog_node_types

    patch_payload = graph_patch_to_dict(patch)
    assert patch_payload["label"] == "typed pyengine 1hz sine 0-100 validation graph"
    assert patch_payload["ops"][0]["nodeType"] == PYENGINE_SERVICE_NODE_TYPE
    assert patch_payload["ops"][1]["nodeType"] == PHASE_NODE_TYPE
    assert patch_payload["ops"][2]["nodeType"] == COSINE_NODE_TYPE
    assert patch_payload["ops"][3]["nodeType"] == RANGE_MAP_NODE_TYPE
    assert {"op": "setNodeState", "nodeId": node_ids.phase, "field": "hz", "value": 1.0} in patch_payload["ops"]
    assert {"op": "setNodeState", "nodeId": node_ids.sine, "field": "phaseOffset", "value": 0.75} in patch_payload["ops"]
    assert pyengine_sine_sample_port(node_ids) == (node_ids.range_map, "value")
    assert pyengine_sine_expected_range() == (0.0, 100.0)

    preview = adapter.apply_patch(patch)

    assert preview.valid is True
    service_node = graph.get_node_by_id(node_ids.service)
    phase_node = graph.get_node_by_id(node_ids.phase)
    sine_node = graph.get_node_by_id(node_ids.sine)
    range_map_node = graph.get_node_by_id(node_ids.range_map)
    assert service_node is not None
    assert phase_node is not None
    assert sine_node is not None
    assert range_map_node is not None
    assert phase_node.svcId == node_ids.service
    assert sine_node.svcId == node_ids.service
    assert range_map_node.svcId == node_ids.service


def test_pyengine_rungraph_outputs_1hz_sine_transformed_to_0_100() -> None:
    async def _run() -> list[float]:
        node_ids = PyEngineSineGraphNodeIds()
        runtime_registry = create_pyengine_registry()
        spec_registry = Registry.wrap(runtime_registry)
        specs_by_class = {
            str(spec.operatorClass or ""): spec
            for spec in spec_registry.operator_specs(PYENGINE_SERVICE_CLASS)
        }
        phase_spec = specs_by_class["f8.phase"]
        cosine_spec = specs_by_class["f8.cosine"]
        range_map_spec = specs_by_class["f8.range_map"]
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=PYENGINE_SERVICE_CLASS), registry=runtime_registry)

        runtime_graph = F8RuntimeGraph(
            graphId="g_sine",
            revision="r1",
            nodes=[
                F8RuntimeNode(
                    nodeId=node_ids.phase,
                    serviceId="svcA",
                    serviceClass=PYENGINE_SERVICE_CLASS,
                    operatorClass="f8.phase",
                    stateFields=list(phase_spec.stateFields or []),
                    stateValues={"hz": 1.0},
                    dataInPorts=list(phase_spec.dataInPorts or []),
                    dataOutPorts=list(phase_spec.dataOutPorts or []),
                ),
                F8RuntimeNode(
                    nodeId=node_ids.sine,
                    serviceId="svcA",
                    serviceClass=PYENGINE_SERVICE_CLASS,
                    operatorClass="f8.cosine",
                    stateFields=list(cosine_spec.stateFields or []),
                    stateValues={"amp": 1.0, "dc": 0.0, "phaseOffset": 0.75},
                    dataInPorts=list(cosine_spec.dataInPorts or []),
                    dataOutPorts=list(cosine_spec.dataOutPorts or []),
                ),
                F8RuntimeNode(
                    nodeId=node_ids.range_map,
                    serviceId="svcA",
                    serviceClass=PYENGINE_SERVICE_CLASS,
                    operatorClass="f8.range_map",
                    stateFields=list(range_map_spec.stateFields or []),
                    stateValues={
                        "inMin": -1.0,
                        "inMax": 1.0,
                        "outMin": 0.0,
                        "outMax": 100.0,
                        "curve": "LINEAR",
                    },
                    dataInPorts=list(range_map_spec.dataInPorts or []),
                    dataOutPorts=list(range_map_spec.dataOutPorts or []),
                ),
            ],
            edges=[
                F8Edge(
                    edgeId="phase_to_sine",
                    fromServiceId="svcA",
                    fromOperatorId=node_ids.phase,
                    fromPort="phase",
                    toServiceId="svcA",
                    toOperatorId=node_ids.sine,
                    toPort="phase",
                    kind=F8EdgeKindEnum.data,
                ),
                F8Edge(
                    edgeId="sine_to_range",
                    fromServiceId="svcA",
                    fromOperatorId=node_ids.sine,
                    fromPort="value",
                    toServiceId="svcA",
                    toOperatorId=node_ids.range_map,
                    toPort="value",
                    kind=F8EdgeKindEnum.data,
                ),
            ],
        )
        await bus.set_rungraph(runtime_graph)
        range_node, range_port = pyengine_sine_sample_port(node_ids)
        runtime_node = bus.get_node(range_node)
        assert isinstance(runtime_node, ComputableNode)
        samples: list[float] = []
        for index in range(5):
            value = await runtime_node.compute_output(range_port, ctx_id=index)
            samples.append(float(value))
            await asyncio.sleep(0.05)
        return samples

    values = asyncio.run(_run())

    assert len(values) == 5
    assert all(0.0 <= value <= 100.0 for value in values)
    assert len({round(value, 3) for value in values}) >= 2
