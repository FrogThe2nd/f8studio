from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import sys
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk.generated import F8RuntimeGraph, F8RuntimeNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.service_host import ServiceHost, ServiceHostConfig
from f8pysdk.testing import ServiceBusHarness

from f8pyscript.constants import SERVICE_CLASS
from f8pyscript.script_node_registry import register_specs
from f8pyscript.script_service_node import PythonScriptServiceNode


@dataclass(frozen=True)
class BenchResult:
    mode: str
    name: str
    iterations: int
    elapsed_s: float

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


class _LegacyInvokePatch:
    def __init__(self) -> None:
        self._orig_build_invoke_ctx: Callable[..., Any] | None = None
        self._orig_invoke_sync: Callable[..., Any] | None = None
        self._orig_invoke_async: Callable[..., Any] | None = None

    def __enter__(self) -> "_LegacyInvokePatch":
        cls = PythonScriptServiceNode
        self._orig_build_invoke_ctx = cls._build_invoke_ctx
        self._orig_invoke_sync = cls._invoke_sync
        self._orig_invoke_async = cls._invoke_async

        def _legacy_build_invoke_ctx(self: PythonScriptServiceNode):
            return self._ctx.with_permission(self._permission_context())

        def _legacy_invoke_sync(
            self: PythonScriptServiceNode,
            hook: Callable[..., Any] | None,
            hook_is_async: bool,
            stage: str,
            *args: Any,
        ) -> Any:
            del hook_is_async
            if hook is None:
                return None
            try:
                invoke_ctx = self._build_invoke_ctx()
                result = hook(invoke_ctx, *args)
                if inspect.isawaitable(result):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError as exc:
                        self._set_error(stage, exc)
                        return None
                    loop.create_task(result, name=f"pyscript:{stage}:{self.node_id}")
                    return None
                return result
            except Exception as exc:
                self._set_error(stage, exc)
                return None

        async def _legacy_invoke_async(
            self: PythonScriptServiceNode,
            hook: Callable[..., Any] | None,
            hook_is_async: bool,
            stage: str,
            *args: Any,
        ) -> Any:
            del hook_is_async
            if hook is None:
                return None
            try:
                invoke_ctx = self._build_invoke_ctx()
                result = hook(invoke_ctx, *args)
                if inspect.isawaitable(result):
                    return await result
                return result
            except Exception as exc:
                self._set_error(stage, exc)
                raise

        cls._build_invoke_ctx = _legacy_build_invoke_ctx  # type: ignore[assignment]
        cls._invoke_sync = _legacy_invoke_sync  # type: ignore[assignment]
        cls._invoke_async = _legacy_invoke_async  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        cls = PythonScriptServiceNode
        if self._orig_build_invoke_ctx is not None:
            cls._build_invoke_ctx = self._orig_build_invoke_ctx  # type: ignore[assignment]
        if self._orig_invoke_sync is not None:
            cls._invoke_sync = self._orig_invoke_sync  # type: ignore[assignment]
        if self._orig_invoke_async is not None:
            cls._invoke_async = self._orig_invoke_async  # type: ignore[assignment]
        return False


def _service_node(code: str) -> F8RuntimeNode:
    desc = RuntimeNodeRegistry.instance().describe(SERVICE_CLASS)
    spec = desc.service
    return F8RuntimeNode(
        nodeId="svcA",
        serviceId="svcA",
        serviceClass=SERVICE_CLASS,
        operatorClass=None,
        dataInPorts=list(spec.dataInPorts or []),
        dataOutPorts=list(spec.dataOutPorts or []),
        stateFields=list(spec.stateFields or []),
        stateValues={"code": code, "tickEnabled": False, "tickMs": 100},
    )


async def _build_node(code: str) -> PythonScriptServiceNode:
    harness = ServiceBusHarness()
    bus = harness.create_bus("svcA")
    reg = RuntimeNodeRegistry.instance()
    register_specs(reg)
    _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)
    graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[_service_node(code)], edges=[])
    await bus.set_rungraph(graph)
    node = bus.get_node("svcA")
    if not isinstance(node, PythonScriptServiceNode):
        raise TypeError("runtime node is not PythonScriptServiceNode")
    return node


async def _bench_on_data(mode: str, iterations: int, warmup: int) -> BenchResult:
    code = (
        "def onData(ctx, port, value, ts_ms=None):\n"
        "    return {'outputs': {'out': value}}\n"
    )
    node = await _build_node(code)
    await node.on_state("code", code, ts_ms=1)

    for i in range(warmup):
        await node.on_data("in", i, ts_ms=i)

    t0 = perf_counter()
    for i in range(iterations):
        await node.on_data("in", i, ts_ms=i)
    elapsed = perf_counter() - t0
    await node.close()
    return BenchResult(mode=mode, name="on_data", iterations=iterations, elapsed_s=elapsed)


async def _bench_on_command(mode: str, iterations: int, warmup: int) -> BenchResult:
    code = (
        "async def onCommand(ctx, name, args, meta=None):\n"
        "    if name != 'inc':\n"
        "        return {'ok': False}\n"
        "    return {'value': int(args.get('x') or 0) + 1}\n"
    )
    node = await _build_node(code)
    await node.on_state("code", code, ts_ms=1)
    if node._hook_on_command is None:
        last_error = str(await node.get_state_value("lastError") or node._last_error or "")
        raise RuntimeError(f"onCommand hook not compiled, mode={mode}, lastError={last_error}")

    for i in range(warmup):
        reply = await node.on_command("inc", {"x": i})
        out = (reply or {}).get("result") if isinstance(reply, dict) else None
        if not isinstance(out, dict) or int(out.get("value") or 0) != (i + 1):
            raise AssertionError("warmup command output mismatch")

    t0 = perf_counter()
    for i in range(iterations):
        reply = await node.on_command("inc", {"x": i})
        out = (reply or {}).get("result") if isinstance(reply, dict) else None
        if not isinstance(out, dict) or int(out.get("value") or 0) != (i + 1):
            raise AssertionError("command output mismatch")
    elapsed = perf_counter() - t0
    await node.close()
    return BenchResult(mode=mode, name="on_command", iterations=iterations, elapsed_s=elapsed)


def _print_result(result: BenchResult) -> None:
    print(
        f"{result.mode}:{result.name} "
        f"n={result.iterations} "
        f"elapsed={result.elapsed_s:.4f}s "
        f"throughput={result.ops_per_sec:,.0f} ops/s "
        f"latency={result.us_per_op:.2f} us/op"
    )


def _print_speedup(legacy: BenchResult, optimized: BenchResult) -> None:
    if legacy.elapsed_s <= 0:
        return
    ratio = optimized.elapsed_s / legacy.elapsed_s
    speedup = legacy.elapsed_s / optimized.elapsed_s if optimized.elapsed_s > 0 else 0.0
    print(f"{optimized.name}: optimized_vs_legacy={ratio:.3f}x, speedup={speedup:.3f}x")


async def _run_mode(mode: str, iterations: int, warmup: int) -> list[BenchResult]:
    on_data_res = await _bench_on_data(mode, iterations=iterations, warmup=warmup)
    on_cmd_res = await _bench_on_command(mode, iterations=iterations, warmup=warmup)
    return [on_data_res, on_cmd_res]


async def main_async(args: argparse.Namespace) -> None:
    print(f"iterations={args.iterations} warmup={args.warmup}")

    with _LegacyInvokePatch():
        legacy_results = await _run_mode("legacy", iterations=args.iterations, warmup=args.warmup)

    optimized_results = await _run_mode("optimized", iterations=args.iterations, warmup=args.warmup)

    for item in legacy_results:
        _print_result(item)
    for item in optimized_results:
        _print_result(item)

    by_name_legacy = {item.name: item for item in legacy_results}
    for item in optimized_results:
        base = by_name_legacy.get(item.name)
        if base is not None:
            _print_speedup(base, item)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark f8.pyscript invoke-path optimizations")
    parser.add_argument("--iterations", type=int, default=100_000, help="iterations per benchmark")
    parser.add_argument("--warmup", type=int, default=2_000, help="warmup iterations")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
