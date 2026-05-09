from __future__ import annotations

import asyncio
import os
import sys
import unittest

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pysdk.bus import ServiceBus, ServiceBusConfig  # noqa: E402
from f8pysdk.codec import encode_obj  # noqa: E402
from f8pysdk.f8_naming import data_key  # noqa: E402
from f8pysdk.specs import (  # noqa: E402
    F8DataPortSpec,
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
    any_schema,
)
from f8pysdk.testing import InMemoryCluster, InMemoryTransport  # noqa: E402

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS  # noqa: E402


class VizTextInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_service_data_is_buffered_for_pull_driven_text_inputs(self) -> None:
        cluster = InMemoryCluster()
        transport = InMemoryTransport(cluster=cluster)
        bus = ServiceBus(
            ServiceBusConfig(
                service_id="studio",
                cross_publish_policy="routed",
                data_delivery="callback",
            ),
            transport=transport,
        )

        runtime_node = F8RuntimeNode(
            nodeId="viz_text_1",
            serviceId="studio",
            serviceClass=SERVICE_CLASS,
            operatorClass="f8.viz.text",
            dataInPorts=[F8DataPortSpec(name="inputData", valueSchema=any_schema())],
            dataOutPorts=[],
            stateFields=[],
            stateValues={},
        )

        graph = F8RuntimeGraph(
            graphId="g-viz-text-cross",
            revision="r1",
            nodes=[
                F8RuntimeNode(
                    nodeId="tracker",
                    serviceId="tracker",
                    serviceClass="f8.cvkit.tracking",
                    operatorClass="f8.remote.source",
                    dataInPorts=[],
                    dataOutPorts=[F8DataPortSpec(name="tracking", valueSchema=any_schema())],
                ),
                runtime_node,
            ],
            edges=[
                F8Edge(
                    edgeId="edge-tracker-to-viz",
                    fromServiceId="tracker",
                    fromOperatorId="tracker",
                    fromPort="tracking",
                    toServiceId="studio",
                    toOperatorId="viz_text_1",
                    toPort="inputData",
                    kind=F8EdgeKindEnum.data,
                    strategy=F8EdgeStrategyEnum.latest,
                )
            ],
        )

        await bus.set_rungraph(graph)
        sample = {"status": "tracking", "bbox": [11, 22, 33, 44], "tsMs": 123}
        key = data_key("tracker", from_node_id="tracker", port_id="tracking")
        payload = encode_obj({"value": sample, "tsMs": 123})

        await transport.publish(key, payload)
        await asyncio.sleep(0)

        pulled = await bus.pull_data("viz_text_1", "inputData")
        self.assertEqual(pulled, sample)


if __name__ == "__main__":
    unittest.main()
