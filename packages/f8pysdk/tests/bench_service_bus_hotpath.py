from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from time import perf_counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.executors.exec_flow import ExecFlowExecutor  # noqa: E402
from f8pysdk.generated import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
)
from f8pysdk.runtime_node import OperatorNode, RuntimeNode  # noqa: E402
from f8pysdk.service_bus.routing_data import emit_data as emit_data_internal  # noqa: E402
from f8pysdk.service_bus.routing_data import pull_data as pull_data_internal  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402


@dataclass(frozen=True)
class BenchResult:
    name: str
    iterations: int
    elapsed_s: float
    extra: str = ""

    @property
    def ops_per_sec(self) -> float:
        if self.elapsed_s <= 0:
            return 0.0
        return float(self.iterations) / self.elapsed_s

    @property
    def us_per_op(self) -> float:
        if self.iterations <= 0:
            return 0.0
        return (self.elapsed_s * 1_000_000.0) / float(self.iterations)


def _runtime_node(
    *,
    node_id: str,
    service_id: str,
    data_in: list[str] | None = None,
    data_out: list[str] | None = None,
    exec_in: list[str] | None = None,
    exec_out: list[str] | None = None,
) -> F8RuntimeNode:
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId=service_id,
        serviceClass=service_id,
        operatorClass=f"bench.{node_id}",
        dataInPorts=list(data_in or []),
        dataOutPorts=list(data_out or []),
        execInPorts=list(exec_in or []),
        execOutPorts=list(exec_out or []),
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


def _exec_edge(*, edge_id: str, from_node: str, from_port: str, to_node: str, to_port: str) -> F8Edge:
    return F8Edge(
        edgeId=edge_id,
        fromServiceId="svcA",
        fromOperatorId=from_node,
        fromPort=from_port,
        toServiceId="svcA",
        toOperatorId=to_node,
        toPort=to_port,
        kind=F8EdgeKindEnum.exec,
        strategy=F8EdgeStrategyEnum.latest,
    )


class _CountSinkExecNode(OperatorNode):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id=node_id, exec_in_ports=["in"], exec_out_ports=[])
        self.count = 0
        self._done_event: asyncio.Event | None = None
        self._target_count = 0

    def expect(self, count: int) -> None:
        self.count = 0
        self._target_count = max(0, int(count))
        self._done_event = asyncio.Event()
        if self._target_count == 0:
            self._done_event.set()

    async def wait_done(self, timeout_s: float) -> None:
        done_event = self._done_event
        if done_event is None:
            return
        await asyncio.wait_for(done_event.wait(), timeout=timeout_s)

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id
        _ = in_port
        self.count += 1
        done_event = self._done_event
        if done_event is not None and self.count >= self._target_count:
            done_event.set()
        return []


class _CountSinkDataNode(RuntimeNode):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id=node_id, data_in_ports=["in"], data_out_ports=[])
        self.count = 0
        self._done_event: asyncio.Event | None = None
        self._target_count = 0

    def expect(self, count: int) -> None:
        self.count = 0
        self._target_count = max(0, int(count))
        self._done_event = asyncio.Event()
        if self._target_count == 0:
            self._done_event.set()

    async def wait_done(self, timeout_s: float) -> None:
        done_event = self._done_event
        if done_event is None:
            return
        await asyncio.wait_for(done_event.wait(), timeout=timeout_s)

    async def on_data(self, port: str, value: object, *, ts_ms: int | None = None) -> None:
        _ = port
        _ = value
        _ = ts_ms
        self.count += 1
        done_event = self._done_event
        if done_event is not None and self.count >= self._target_count:
            done_event.set()


def _print_result(result: BenchResult) -> None:
    suffix = f" {result.extra}" if result.extra else ""
    print(
        f"{result.name}: n={result.iterations} elapsed={result.elapsed_s:.4f}s "
        f"throughput={result.ops_per_sec:,.0f} ops/s latency={result.us_per_op:.2f} us/op{suffix}"
    )


async def bench_data_emit_pull_local(*, iterations: int, warmup: int) -> BenchResult:
    harness = ServiceBusHarness()
    bus = harness.create_bus("svcA")
    graph = F8RuntimeGraph(
        graphId="g-data",
        revision="r1",
        nodes=[
            _runtime_node(node_id="src", service_id="svcA", data_out=["out"]),
            _runtime_node(node_id="dst", service_id="svcA", data_in=["in"]),
        ],
        edges=[_data_edge(edge_id="d1", from_node="src", from_port="out", to_node="dst", to_port="in")],
    )
    await bus.set_rungraph(graph)

    for i in range(max(0, int(warmup))):
        await bus.emit_data("src", "out", i, ts_ms=i + 1)
        _ = await bus.pull_data("dst", "in", ctx_id=i)

    t0 = perf_counter()
    for i in range(max(0, int(iterations))):
        await bus.emit_data("src", "out", i, ts_ms=i + 1)
        _ = await bus.pull_data("dst", "in", ctx_id=i)
    elapsed = perf_counter() - t0
    return BenchResult(name="data_emit_pull_local", iterations=iterations, elapsed_s=elapsed)


async def bench_data_emit_pull_internal(*, iterations: int, warmup: int) -> BenchResult:
    harness = ServiceBusHarness()
    bus = harness.create_bus("svcA")
    graph = F8RuntimeGraph(
        graphId="g-data-internal",
        revision="r1",
        nodes=[
            _runtime_node(node_id="src", service_id="svcA", data_out=["out"]),
            _runtime_node(node_id="dst", service_id="svcA", data_in=["in"]),
        ],
        edges=[_data_edge(edge_id="d1", from_node="src", from_port="out", to_node="dst", to_port="in")],
    )
    await bus.set_rungraph(graph)

    for i in range(max(0, int(warmup))):
        await emit_data_internal(bus, "src", "out", i, ts_ms=i + 1)
        _ = await pull_data_internal(bus, "dst", "in", ctx_id=i)

    t0 = perf_counter()
    for i in range(max(0, int(iterations))):
        await emit_data_internal(bus, "src", "out", i, ts_ms=i + 1)
        _ = await pull_data_internal(bus, "dst", "in", ctx_id=i)
    elapsed = perf_counter() - t0
    return BenchResult(name="data_emit_pull_internal", iterations=iterations, elapsed_s=elapsed)


async def _build_exec_bench() -> tuple[ExecFlowExecutor, _CountSinkExecNode]:
    harness = ServiceBusHarness()
    bus = harness.create_bus("svcA")
    executor = ExecFlowExecutor(bus)
    sink = _CountSinkExecNode("sink")
    executor.register_node(sink)

    graph = F8RuntimeGraph(
        graphId="g-exec",
        revision="r1",
        nodes=[
            _runtime_node(node_id="src", service_id="svcA", exec_in=["tick"], exec_out=["exec"]),
            _runtime_node(node_id="sink", service_id="svcA", exec_in=["in"]),
        ],
        edges=[_exec_edge(edge_id="e1", from_node="src", from_port="exec", to_node="sink", to_port="in")],
    )
    await executor.apply_rungraph(graph)
    return executor, sink


async def bench_exec_trigger_wait(*, iterations: int, warmup: int) -> BenchResult:
    executor, sink = await _build_exec_bench()

    try:
        sink.expect(warmup)
        for i in range(max(0, int(warmup))):
            await executor.trigger_exec("src", "exec", exec_id=i)
        await sink.wait_done(timeout_s=3.0)

        sink.expect(iterations)
        t0 = perf_counter()
        for i in range(max(0, int(iterations))):
            await executor.trigger_exec("src", "exec", exec_id=i)
        await sink.wait_done(timeout_s=3.0)
        elapsed = perf_counter() - t0
    finally:
        await executor.set_active(False)

    return BenchResult(name="exec_trigger_wait", iterations=iterations, elapsed_s=elapsed)


async def bench_exec_trigger_nowait(*, iterations: int, warmup: int) -> BenchResult:
    executor, sink = await _build_exec_bench()

    try:
        sink.expect(warmup)
        for i in range(max(0, int(warmup))):
            await executor.trigger_exec_nowait("src", "exec", exec_id=i)
        await sink.wait_done(timeout_s=3.0)

        sink.expect(iterations)
        t0 = perf_counter()
        for i in range(max(0, int(iterations))):
            await executor.trigger_exec_nowait("src", "exec", exec_id=i)
        await sink.wait_done(timeout_s=5.0)
        elapsed = perf_counter() - t0
    finally:
        await executor.set_active(False)

    return BenchResult(name="exec_trigger_nowait", iterations=iterations, elapsed_s=elapsed)


async def bench_push_on_data_batched(*, ticks: int, burst: int, warmup_ticks: int) -> BenchResult:
    harness = ServiceBusHarness()
    bus = harness.create_bus("svcA")
    bus.set_data_delivery("push", source="bench")
    sink = _CountSinkDataNode("dst")
    bus.register_node(sink)

    graph = F8RuntimeGraph(
        graphId="g-push",
        revision="r1",
        nodes=[
            _runtime_node(node_id="src", service_id="svcA", data_out=["out"]),
            _runtime_node(node_id="dst", service_id="svcA", data_in=["in"]),
        ],
        edges=[_data_edge(edge_id="d1", from_node="src", from_port="out", to_node="dst", to_port="in")],
    )
    await bus.set_rungraph(graph)

    for tick in range(max(0, int(warmup_ticks))):
        for i in range(max(0, int(burst))):
            await bus.emit_data("src", "out", (tick * burst) + i, ts_ms=(tick * burst) + i + 1)
        await asyncio.sleep(0)

    samples = max(0, int(ticks)) * max(0, int(burst))
    sink.expect(max(0, int(ticks)))

    t0 = perf_counter()
    for tick in range(max(0, int(ticks))):
        for i in range(max(0, int(burst))):
            await bus.emit_data("src", "out", (tick * burst) + i, ts_ms=(tick * burst) + i + 1)
        await asyncio.sleep(0)
    await sink.wait_done(timeout_s=5.0)
    elapsed = perf_counter() - t0

    return BenchResult(
        name="push_on_data_batched",
        iterations=samples,
        elapsed_s=elapsed,
        extra=f"(ticks={ticks}, burst={burst}, on_data_calls={sink.count})",
    )


async def main_async(args: argparse.Namespace) -> None:
    print("Benchmark: ServiceBus/ExecFlow hot paths")

    results = [
        await bench_data_emit_pull_local(iterations=args.iterations, warmup=args.warmup),
        await bench_data_emit_pull_internal(iterations=args.iterations, warmup=args.warmup),
        await bench_exec_trigger_wait(iterations=args.iterations, warmup=args.warmup),
        await bench_exec_trigger_nowait(iterations=args.iterations, warmup=args.warmup),
        await bench_push_on_data_batched(ticks=args.push_ticks, burst=args.push_burst, warmup_ticks=args.push_warmup),
    ]
    for result in results:
        _print_result(result)

    api_path = results[0]
    internal_path = results[1]
    if internal_path.elapsed_s > 0:
        ratio = api_path.elapsed_s / internal_path.elapsed_s
        delta_pct = ((api_path.elapsed_s - internal_path.elapsed_s) / internal_path.elapsed_s) * 100.0
        print(f"data(api vs internal): slowdown={ratio:.2f}x ({delta_pct:+.1f}%)")

    wait = results[2]
    nowait = results[3]
    if nowait.elapsed_s > 0:
        ratio = wait.elapsed_s / nowait.elapsed_s
        delta_pct = ((wait.elapsed_s - nowait.elapsed_s) / nowait.elapsed_s) * 100.0
        print(f"exec(wait vs nowait): slowdown={ratio:.2f}x ({delta_pct:+.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="ServiceBus/ExecFlow hot-path micro benchmark")
    parser.add_argument("--iterations", type=int, default=200_000, help="iterations for data/exec benchmarks")
    parser.add_argument("--warmup", type=int, default=20_000, help="warmup iterations for data/exec benchmarks")
    parser.add_argument("--push-ticks", type=int, default=20_000, help="push-mode tick count")
    parser.add_argument("--push-burst", type=int, default=8, help="samples emitted per push tick")
    parser.add_argument("--push-warmup", type=int, default=2_000, help="push-mode warmup ticks")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
