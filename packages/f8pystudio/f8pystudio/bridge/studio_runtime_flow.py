from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from f8pysdk import F8RuntimeGraph

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from f8pystudio.bridge.remote_state_watcher import WatchTarget
from .remote_state_sync import ApplyWatchTargetsRequest, RemoteStateGateway


async def wait_for_studio_runtime_ready(
    *,
    get_service_bus: Callable[[], Any | None],
    emit_log: Callable[[str], None],
    timeout_s: float = 6.0,
    poll_interval_s: float = 0.08,
) -> bool:
    deadline = time.monotonic() + float(timeout_s or 0.0)
    while True:
        if get_service_bus() is not None:
            return True
        if time.monotonic() >= deadline:
            emit_log("studio runtime not ready (timeout)")
            return False
        await asyncio.sleep(float(poll_interval_s))


async def install_studio_runtime_graph(
    *,
    compiled: CompiledRuntimeGraphs,
    get_service_bus: Callable[[], Any | None],
    build_studio_runtime_graph: Callable[[CompiledRuntimeGraphs], F8RuntimeGraph],
    emit_log: Callable[[str], None],
) -> bool:
    bus = get_service_bus()
    if bus is None:
        return False
    try:
        studio_graph = build_studio_runtime_graph(compiled)
        await bus.set_rungraph(studio_graph)
        return True
    except Exception as exc:
        emit_log(f"install studio graph failed: {exc}")
        return False


async def apply_remote_state_watches_if_changed(
    *,
    compiled: CompiledRuntimeGraphs,
    remote_state_gateway: RemoteStateGateway | None,
    watch_targets_cache: tuple[WatchTarget, ...] | None,
    build_remote_watch_targets: Callable[[CompiledRuntimeGraphs], tuple[WatchTarget, ...]],
) -> tuple[tuple[WatchTarget, ...] | None, bool]:
    if remote_state_gateway is None:
        return watch_targets_cache, False
    targets_sorted = build_remote_watch_targets(compiled)
    if watch_targets_cache == targets_sorted:
        return watch_targets_cache, False
    await remote_state_gateway.apply_targets(ApplyWatchTargetsRequest(targets=targets_sorted))
    return targets_sorted, True
