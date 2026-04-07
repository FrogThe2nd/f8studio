import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk import F8DataPortSpec, F8RuntimeGraph, F8RuntimeNode, F8StateAccess, F8StateSpec, any_schema, integer_schema  # noqa: E402
from f8pysdk.generated import F8Edge, F8EdgeKindEnum, F8EdgeStrategyEnum  # noqa: E402
from f8pysdk.registry import RuntimeNodeRegistry  # noqa: E402
from f8pysdk.app import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import buffer_input  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.pull import PullRuntimeNode  # noqa: E402
from f8pyengine.operators.recorder import RecorderRuntimeNode  # noqa: E402
from f8pyengine.operators.replayer import ReplayerRuntimeNode  # noqa: E402
from f8pyengine.pyengine_node_registry import register_pyengine_specs  # noqa: E402
from f8pyengine.recording import (  # noqa: E402
    FORMAT_VERSION,
    RecordingHeader,
    RecordingReader,
    RecordingWriter,
    TIME_MODE_OFFSET_FROM_PLAY,
    TIME_MODE_RECORDED_EPOCH,
)


def _data_edge(*, edge_id: str, from_node: str, from_port: str, to_node: str, to_port: str) -> F8Edge:
    return F8Edge(
        edgeId=edge_id,
        fromServiceId="svcA",
        fromOperatorId=from_node,
        fromPort=from_port,
        toServiceId="svcA",
        toOperatorId=to_node,
        toPort=to_port,
        kind=F8EdgeKindEnum.data,
        strategy=F8EdgeStrategyEnum.latest,
    )


class RecorderReplayerTests(unittest.IsolatedAsyncioTestCase):
    async def _build_runtime(
        self,
        *,
        nodes: list[F8RuntimeNode],
        edges: list[F8Edge] | None = None,
    ) -> tuple[ServiceBusHarness, object]:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_pyengine_specs(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)
        graph = F8RuntimeGraph(graphId="g_recording", revision="r1", nodes=list(nodes), edges=list(edges or []))
        await bus.set_rungraph(graph)
        return harness, bus

    def _recorder_node(self, *, path: str, node_id: str = "rec1", data_ports: list[str] | None = None) -> F8RuntimeNode:
        state_fields = list(RecorderRuntimeNode.SPEC.stateFields or [])
        state_fields.append(
            F8StateSpec(
                name="alpha",
                label="Alpha",
                description="Custom sparse state.",
                valueSchema=integer_schema(),
                access=F8StateAccess.rw,
                required=False,
                showOnNode=True,
            )
        )
        return F8RuntimeNode(
            nodeId=node_id,
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=RecorderRuntimeNode.SPEC.operatorClass,
            stateFields=state_fields,
            stateValues={"path": path, "enabled": True, "append": True},
            execInPorts=["record"],
            execOutPorts=[],
            dataInPorts=[F8DataPortSpec(name=name, description="", valueSchema=any_schema(), required=False) for name in list(data_ports or ["a", "b"])],
            dataOutPorts=[],
        )

    def _replayer_node(self, *, path: str, node_id: str = "rep1", time_mode: str = TIME_MODE_OFFSET_FROM_PLAY) -> F8RuntimeNode:
        state_fields = list(ReplayerRuntimeNode.SPEC.stateFields or [])
        state_fields.append(
            F8StateSpec(
                name="alpha",
                label="Alpha",
                description="Replayed sparse state.",
                valueSchema=integer_schema(),
                access=F8StateAccess.rw,
                required=False,
                showOnNode=True,
            )
        )
        data_out_ports = list(ReplayerRuntimeNode.SPEC.dataOutPorts or [])
        data_out_ports.append(F8DataPortSpec(name="outA", description="", valueSchema=any_schema(), required=False))
        return F8RuntimeNode(
            nodeId=node_id,
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=ReplayerRuntimeNode.SPEC.operatorClass,
            stateFields=state_fields,
            stateValues={"path": path, "loop": False, "timeMode": time_mode, "playing": False},
            execInPorts=["play", "pause", "stop"],
            execOutPorts=["started", "stopped", "looped", "done"],
            dataInPorts=[],
            dataOutPorts=data_out_ports,
        )

    def _pull_sink_node(self, *, node_id: str = "sink1") -> F8RuntimeNode:
        return F8RuntimeNode(
            nodeId=node_id,
            serviceId="svcA",
            serviceClass=SERVICE_CLASS,
            operatorClass=PullRuntimeNode.SPEC.operatorClass,
            stateFields=list(PullRuntimeNode.SPEC.stateFields or []),
            stateValues={"autoTriggerEnabled": False},
            dataInPorts=[
                F8DataPortSpec(name="outA", description="", valueSchema=any_schema(), required=False),
                F8DataPortSpec(name="positionMs", description="", valueSchema=any_schema(), required=False),
            ],
            dataOutPorts=[],
            execInPorts=[],
            execOutPorts=[],
        )

    async def test_recorder_records_data_samples_and_sparse_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "capture.f8rec")
            _, bus = await self._build_runtime(nodes=[self._recorder_node(path=path)])
            node = bus.get_node("rec1")
            self.assertIsInstance(node, RecorderRuntimeNode)

            buffer_input(bus, "rec1", "a", 11, ts_ms=1000, edge=None, ctx_id=None)
            buffer_input(bus, "rec1", "b", {"x": 2}, ts_ms=1000, edge=None, ctx_id=None)
            await node.on_exec(1000, "record")
            await bus.publish_state_runtime("rec1", "alpha", 7, ts_ms=1010)

            reader = RecordingReader(path)
            events = list(reader.iter_events())
            self.assertEqual(events[0].type, "header")
            self.assertEqual(events[1].type, "data_sample")
            self.assertEqual(events[2].type, "state_change")
            self.assertEqual(events[1].data["a"], 11)
            self.assertEqual(events[1].data["b"], {"x": 2})
            self.assertEqual(events[2].field, "alpha")
            self.assertEqual(events[2].value, 7)

    async def test_recorder_append_mode_allows_compatible_and_rejects_incompatible_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "append.f8rec")
            _, bus = await self._build_runtime(nodes=[self._recorder_node(path=path, node_id="rec1", data_ports=["a"])])
            node = bus.get_node("rec1")
            self.assertIsInstance(node, RecorderRuntimeNode)
            buffer_input(bus, "rec1", "a", 1, ts_ms=1000, edge=None, ctx_id=None)
            await node.on_exec(1000, "record")

            _, bus2 = await self._build_runtime(nodes=[self._recorder_node(path=path, node_id="rec2", data_ports=["a"])])
            node2 = bus2.get_node("rec2")
            self.assertIsInstance(node2, RecorderRuntimeNode)
            buffer_input(bus2, "rec2", "a", 2, ts_ms=1010, edge=None, ctx_id=None)
            await node2.on_exec(1010, "record")

            info = RecordingReader(path).read_info()
            self.assertEqual(info.header.data_ports, ("a",))
            self.assertEqual(info.event_count, 3)

            _, bus3 = await self._build_runtime(nodes=[self._recorder_node(path=path, node_id="rec3", data_ports=["a", "b"])])
            node3 = bus3.get_node("rec3")
            self.assertIsInstance(node3, RecorderRuntimeNode)
            buffer_input(bus3, "rec3", "a", 3, ts_ms=1020, edge=None, ctx_id=None)
            buffer_input(bus3, "rec3", "b", 4, ts_ms=1020, edge=None, ctx_id=None)
            await node3.on_exec(1020, "record")
            last_error = (await bus3.get_state("rec3", "lastError")).value
            self.assertIn("header mismatch", str(last_error))

    async def test_replayer_replays_data_state_and_position_offset_mode(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".f8rec")
        os.close(fd)
        try:
            writer = RecordingWriter(
                path,
                header=RecordingHeader(
                    format_version=FORMAT_VERSION,
                    created_ts_ms=1000,
                    data_ports=("outA",),
                    state_fields=("alpha",),
                ),
                append=False,
            )
            writer.open()
            writer.write_data_sample(tick_ts_ms=1000, relative_offset_ms=0, data={"outA": 1})
            writer.write_state_change(state_ts_ms=1010, relative_offset_ms=10, field="alpha", value=9)
            writer.write_data_sample(tick_ts_ms=1020, relative_offset_ms=20, data={"outA": 2})
            writer.close()

            replayer = self._replayer_node(path=path, time_mode=TIME_MODE_OFFSET_FROM_PLAY)
            sink = self._pull_sink_node()
            _, bus = await self._build_runtime(
                nodes=[replayer, sink],
                edges=[
                    _data_edge(edge_id="d1", from_node="rep1", from_port="outA", to_node="sink1", to_port="outA"),
                    _data_edge(edge_id="d2", from_node="rep1", from_port="positionMs", to_node="sink1", to_port="positionMs"),
                ],
            )
            node = bus.get_node("rep1")
            self.assertIsInstance(node, ReplayerRuntimeNode)
            await asyncio.sleep(0.02)
            self.assertEqual((await bus.get_state("rep1", "durationMs")).value, 20)
            await node.on_exec(1, "play")
            await asyncio.sleep(0.08)

            out_a = await bus.pull_data("sink1", "outA", ctx_id="final")
            pos = await bus.pull_data("sink1", "positionMs", ctx_id="final")
            alpha = (await bus.get_state("rep1", "alpha")).value
            self.assertEqual(out_a, 2)
            self.assertEqual(alpha, 9)
            self.assertGreaterEqual(int(pos), 20)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    async def test_replayer_supports_recorded_epoch_mode(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".f8rec")
        os.close(fd)
        try:
            now_ms = int(asyncio.get_running_loop().time() * 1000.0)
            wall_now_ms = int(__import__("time").time() * 1000.0)
            start_ts_ms = wall_now_ms + 20
            writer = RecordingWriter(
                path,
                header=RecordingHeader(
                    format_version=FORMAT_VERSION,
                    created_ts_ms=start_ts_ms,
                    data_ports=("outA",),
                    state_fields=(),
                ),
                append=False,
            )
            writer.open()
            writer.write_data_sample(tick_ts_ms=start_ts_ms, relative_offset_ms=0, data={"outA": 3})
            writer.write_data_sample(tick_ts_ms=start_ts_ms + 20, relative_offset_ms=20, data={"outA": 4})
            writer.close()

            replayer = self._replayer_node(path=path, time_mode=TIME_MODE_RECORDED_EPOCH)
            sink = self._pull_sink_node()
            _, bus = await self._build_runtime(
                nodes=[replayer, sink],
                edges=[_data_edge(edge_id="d1", from_node="rep1", from_port="outA", to_node="sink1", to_port="outA")],
            )
            node = bus.get_node("rep1")
            self.assertIsInstance(node, ReplayerRuntimeNode)
            await asyncio.sleep(0.02)
            await node.on_exec(1, "play")
            await asyncio.sleep(0.08)
            out_a = await bus.pull_data("sink1", "outA", ctx_id="epoch")
            self.assertEqual(out_a, 4)
            self.assertGreaterEqual(now_ms, 0)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    async def test_replayer_bad_file_sets_last_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "broken.f8rec")
            with open(path, "wb") as fh:
                fh.write(b"not-a-valid-recording")

            _, bus = await self._build_runtime(nodes=[self._replayer_node(path=path)])
            await bus.publish_state_runtime("rep1", "path", path, ts_ms=1)
            loaded = (await bus.get_state("rep1", "loaded")).value
            last_error = (await bus.get_state("rep1", "lastError")).value
            self.assertEqual(bool(loaded), False)
            self.assertTrue(str(last_error))

    def test_position_ms_is_not_a_state_field(self) -> None:
        state_names = [str(field.name) for field in list(ReplayerRuntimeNode.SPEC.stateFields or [])]
        data_names = [str(port.name) for port in list(ReplayerRuntimeNode.SPEC.dataOutPorts or [])]
        self.assertNotIn("positionMs", state_names)
        self.assertIn("positionMs", data_names)


if __name__ == "__main__":
    unittest.main()
