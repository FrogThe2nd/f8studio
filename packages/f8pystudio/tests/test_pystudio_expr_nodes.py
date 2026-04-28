from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.specs import F8DataPortSpec, any_schema  # noqa: E402
from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode, F8StateAccess, F8StateSpec  # noqa: E402
from f8pysdk.specs import number_schema  # noqa: E402
from f8pysdk.testing import buffer_input  # noqa: E402
from f8pysdk.host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS  # noqa: E402
from f8pystudio.operators.data_expr import DataExprRuntimeNode  # noqa: E402
from f8pystudio.operators.state_expr import StateExprRuntimeNode  # noqa: E402
from f8pystudio.studio_specs.registry import create_pystudio_registry  # noqa: E402


class PyStudioExprNodeTests(unittest.IsolatedAsyncioTestCase):
    def test_registered_in_pystudio_specs(self) -> None:
        reg = create_pystudio_registry()
        desc = reg.describe(SERVICE_CLASS)
        operator_classes = {str(spec.operatorClass or "") for spec in list(desc.operators or [])}
        self.assertIn("f8.data_expr", operator_classes)
        self.assertIn("f8.state_expr", operator_classes)

    async def test_data_expr_evaluates_inside_pystudio_runtime(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("studio")
        reg = create_pystudio_registry()
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        op = F8RuntimeNode(
            nodeId="expr1",
            serviceId="studio",
            serviceClass=SERVICE_CLASS,
            operatorClass=DataExprRuntimeNode.SPEC.operatorClass,
            stateFields=list(DataExprRuntimeNode.SPEC.stateFields or []),
            stateValues={"code": "a + b"},
            dataInPorts=[
                F8DataPortSpec(name="a", description="", valueSchema=any_schema(), required=False),
                F8DataPortSpec(name="b", description="", valueSchema=any_schema(), required=False),
            ],
            dataOutPorts=[
                F8DataPortSpec(name="out", description="", valueSchema=any_schema(), required=False),
            ],
        )
        await bus.set_rungraph(F8RuntimeGraph(graphId="g1", revision="r1", nodes=[op], edges=[]))

        node = bus.get_node("expr1")
        self.assertIsInstance(node, DataExprRuntimeNode)
        assert isinstance(node, DataExprRuntimeNode)

        buffer_input(bus, "expr1", "a", 2, ts_ms=0, edge=None, ctx_id=None)
        buffer_input(bus, "expr1", "b", 5, ts_ms=0, edge=None, ctx_id=None)
        out = await node.compute_output("out", ctx_id=1)
        self.assertEqual(out, 7)

    async def test_state_expr_publishes_out_inside_pystudio_runtime(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("studio")
        reg = create_pystudio_registry()
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        op = F8RuntimeNode(
            nodeId="state1",
            serviceId="studio",
            serviceClass=SERVICE_CLASS,
            operatorClass=StateExprRuntimeNode.SPEC.operatorClass,
            stateFields=[
                *list(StateExprRuntimeNode.SPEC.stateFields or []),
                F8StateSpec(name="a", label="a", description="", valueSchema=number_schema(default=1.5), access=F8StateAccess.rw, required=False, showOnNode=True),
                F8StateSpec(name="b", label="b", description="", valueSchema=number_schema(default=2.5), access=F8StateAccess.rw, required=False, showOnNode=True),
            ],
            stateValues={"code": "a + b", "a": 1.5, "b": 2.5},
            dataInPorts=[],
            dataOutPorts=[],
        )
        await bus.set_rungraph(F8RuntimeGraph(graphId="g2", revision="r1", nodes=[op], edges=[]))

        runtime = bus.get_node("state1")
        self.assertIsInstance(runtime, StateExprRuntimeNode)
        assert isinstance(runtime, StateExprRuntimeNode)

        await runtime.on_state("a", 4.0, ts_ms=1)
        self.assertEqual(await runtime.get_state_value("out"), 6.5)
        self.assertEqual(await runtime.get_state_value("lastError"), "")
