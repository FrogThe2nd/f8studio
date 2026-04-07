from f8pysdk.msgspec_codec import copy_model
import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.capabilities import EntrypointNode  # noqa: E402
from f8pysdk.executors.exec_flow import EntrypointContext, ExecFlowExecutor  # noqa: E402
from f8pysdk.generated import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
)
from f8pysdk.nodes import OperatorNode  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402


def _runtime_node(*, node_id: str, service_id: str, exec_in: list[str], exec_out: list[str]) -> F8RuntimeNode:
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId=service_id,
        serviceClass=service_id,
        operatorClass=f"op.{node_id}",
        execInPorts=list(exec_in),
        execOutPorts=list(exec_out),
    )


def _exec_edge(*, edge_id: str, service_id: str, from_node: str, from_port: str, to_node: str, to_port: str) -> F8Edge:
    return F8Edge(
        edgeId=edge_id,
        fromServiceId=service_id,
        fromOperatorId=from_node,
        fromPort=from_port,
        toServiceId=service_id,
        toOperatorId=to_node,
        toPort=to_port,
        kind=F8EdgeKindEnum.exec,
        strategy=F8EdgeStrategyEnum.latest,
    )


class _ManualEntrypointNode(OperatorNode, EntrypointNode):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id=node_id, exec_in_ports=[], exec_out_ports=["exec"])
        self.start_calls = 0
        self.stop_calls = 0
        self.last_ctx: EntrypointContext | None = None

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id
        _ = in_port
        return []

    async def start_entrypoint(self, ctx: EntrypointContext) -> None:
        self.start_calls += 1
        self.last_ctx = ctx

    async def stop_entrypoint(self) -> None:
        self.stop_calls += 1


class _NonEntrypointExecNode(OperatorNode):
    def __init__(self, node_id: str, *, exec_in_ports: list[str], exec_out_ports: list[str]) -> None:
        super().__init__(node_id=node_id, exec_in_ports=list(exec_in_ports), exec_out_ports=list(exec_out_ports))

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id
        _ = in_port
        return []


class _SerialSinkNode(OperatorNode):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id=node_id, exec_in_ports=["left", "right"], exec_out_ports=[])
        self.trace: list[str] = []

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = in_port
        self.trace.append(f"start:{exec_id}")
        await asyncio.sleep(0.03)
        self.trace.append(f"end:{exec_id}")
        return []


class _BlockingSinkNode(OperatorNode):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id=node_id, exec_in_ports=["exec"], exec_out_ports=[])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = in_port
        self.calls.append(str(exec_id))
        self.entered.set()
        await self.release.wait()
        return []


class ExecFlowMultiEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_and_stops_multiple_entrypoints(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        executor = ExecFlowExecutor(bus)

        ep1 = _ManualEntrypointNode("ep1")
        ep2 = _ManualEntrypointNode("ep2")
        executor.register_node(ep1)
        executor.register_node(ep2)

        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[
                _runtime_node(node_id="ep1", service_id="svcA", exec_in=[], exec_out=["exec"]),
                _runtime_node(node_id="ep2", service_id="svcA", exec_in=[], exec_out=["exec"]),
            ],
            edges=[],
        )

        await executor.apply_rungraph(graph)
        self.assertEqual(executor.current_entrypoint_node_ids(), ("ep1", "ep2"))
        self.assertEqual(ep1.start_calls, 1)
        self.assertEqual(ep2.start_calls, 1)

        await executor.stop_all_entrypoints()
        self.assertEqual(executor.current_entrypoint_node_ids(), ())
        self.assertEqual(ep1.stop_calls, 1)
        self.assertEqual(ep2.stop_calls, 1)

        await executor.set_active(False)

    async def test_concurrent_triggers_are_processed_in_fifo_order(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        executor = ExecFlowExecutor(bus)
        sink = _SerialSinkNode("sink")
        executor.register_node(sink)

        graph = F8RuntimeGraph(
            graphId="g2",
            revision="r1",
            nodes=[
                _runtime_node(node_id="src1", service_id="svcA", exec_in=["in"], exec_out=["exec"]),
                _runtime_node(node_id="src2", service_id="svcA", exec_in=["in"], exec_out=["exec"]),
                _runtime_node(node_id="sink", service_id="svcA", exec_in=["left", "right"], exec_out=[]),
            ],
            edges=[
                _exec_edge(
                    edge_id="e1",
                    service_id="svcA",
                    from_node="src1",
                    from_port="exec",
                    to_node="sink",
                    to_port="left",
                ),
                _exec_edge(
                    edge_id="e2",
                    service_id="svcA",
                    from_node="src2",
                    from_port="exec",
                    to_node="sink",
                    to_port="right",
                ),
            ],
        )

        await executor.apply_rungraph(graph)

        t1 = asyncio.create_task(executor.trigger_exec("src1", "exec", exec_id=1))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(executor.trigger_exec("src2", "exec", exec_id=2))
        await asyncio.gather(t1, t2)

        self.assertEqual(sink.trace, ["start:1", "end:1", "start:2", "end:2"])
        await executor.set_active(False)

    async def test_rungraph_redeploy_drops_pending_queue(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        executor = ExecFlowExecutor(bus)
        sink = _BlockingSinkNode("sink")
        executor.register_node(sink)

        graph_v1 = F8RuntimeGraph(
            graphId="g3",
            revision="r1",
            nodes=[
                _runtime_node(node_id="src", service_id="svcA", exec_in=["in"], exec_out=["exec"]),
                _runtime_node(node_id="sink", service_id="svcA", exec_in=["exec"], exec_out=[]),
            ],
            edges=[
                _exec_edge(
                    edge_id="e1",
                    service_id="svcA",
                    from_node="src",
                    from_port="exec",
                    to_node="sink",
                    to_port="exec",
                )
            ],
        )

        await executor.apply_rungraph(graph_v1)

        t1 = asyncio.create_task(executor.trigger_exec("src", "exec", exec_id="e1"))
        await asyncio.wait_for(sink.entered.wait(), timeout=1.0)
        t2 = asyncio.create_task(executor.trigger_exec("src", "exec", exec_id="e2"))
        await asyncio.sleep(0)

        graph_v2 = copy_model(graph_v1, update={"revision": "r2"})
        await executor.apply_rungraph(graph_v2)
        sink.release.set()

        await asyncio.gather(t1, t2)
        self.assertEqual(sink.calls, ["e1"])
        await executor.set_active(False)

    async def test_rejects_structural_entrypoint_without_entrypoint_capability(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        executor = ExecFlowExecutor(bus)
        bad = _NonEntrypointExecNode("bad", exec_in_ports=[], exec_out_ports=["exec"])
        executor.register_node(bad)

        graph = F8RuntimeGraph(
            graphId="g4",
            revision="r1",
            nodes=[_runtime_node(node_id="bad", service_id="svcA", exec_in=[], exec_out=["exec"])],
            edges=[],
        )

        with self.assertRaisesRegex(ValueError, "EntrypointNode: bad"):
            await executor.apply_rungraph(graph)
        await executor.set_active(False)


if __name__ == "__main__":
    unittest.main()
