from __future__ import annotations

import asyncio
from types import SimpleNamespace

from f8pystudio.bridge.studio_runtime_flow import (
    apply_remote_state_watches_if_changed,
    install_studio_runtime_graph,
    wait_for_studio_runtime_ready,
)
from f8pystudio.bridge.remote_state_watcher import WatchTarget


class _FakeBus:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = bool(should_fail)
        self.applied_graphs: list[object] = []

    async def set_rungraph(self, graph: object) -> None:
        if self.should_fail:
            raise RuntimeError("set_rungraph failed")
        self.applied_graphs.append(graph)


class _FakeRemoteStateGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[WatchTarget, ...]] = []

    async def apply_targets(self, req: object) -> None:
        self.calls.append(tuple(req.targets))  # type: ignore[attr-defined]


def test_wait_for_studio_runtime_ready_times_out() -> None:
    logs: list[str] = []

    ok = asyncio.run(
        wait_for_studio_runtime_ready(
            get_service_bus=lambda: None,
            emit_log=lambda line: logs.append(str(line)),
            timeout_s=0.0,
            poll_interval_s=0.0,
        )
    )

    assert ok is False
    assert logs == ["studio runtime not ready (timeout)"]


def test_install_studio_runtime_graph_success_and_failure() -> None:
    compiled = SimpleNamespace(graph_id="g1")
    graph_obj = {"graph": "runtime"}
    logs: list[str] = []

    ok_bus = _FakeBus(should_fail=False)
    ok = asyncio.run(
        install_studio_runtime_graph(
            compiled=compiled,  # type: ignore[arg-type]
            get_service_bus=lambda: ok_bus,
            build_studio_runtime_graph=lambda _compiled: graph_obj,  # type: ignore[arg-type]
            emit_log=lambda line: logs.append(str(line)),
        )
    )
    assert ok is True
    assert ok_bus.applied_graphs == [graph_obj]

    bad_bus = _FakeBus(should_fail=True)
    ok_fail = asyncio.run(
        install_studio_runtime_graph(
            compiled=compiled,  # type: ignore[arg-type]
            get_service_bus=lambda: bad_bus,
            build_studio_runtime_graph=lambda _compiled: graph_obj,  # type: ignore[arg-type]
            emit_log=lambda line: logs.append(str(line)),
        )
    )
    assert ok_fail is False
    assert "install studio graph failed:" in logs[-1]


def test_apply_remote_state_watches_if_changed_dedupes() -> None:
    compiled = SimpleNamespace()
    gateway = _FakeRemoteStateGateway()
    targets = (
        WatchTarget(service_id="svc_a", node_id="node_a", fields=("x", "svcId")),
    )

    cache, applied = asyncio.run(
        apply_remote_state_watches_if_changed(
            compiled=compiled,  # type: ignore[arg-type]
            remote_state_gateway=gateway,  # type: ignore[arg-type]
            watch_targets_cache=None,
            build_remote_watch_targets=lambda _compiled: targets,  # type: ignore[arg-type]
        )
    )
    assert applied is True
    assert cache == targets
    assert gateway.calls == [targets]

    cache2, applied2 = asyncio.run(
        apply_remote_state_watches_if_changed(
            compiled=compiled,  # type: ignore[arg-type]
            remote_state_gateway=gateway,  # type: ignore[arg-type]
            watch_targets_cache=cache,
            build_remote_watch_targets=lambda _compiled: targets,  # type: ignore[arg-type]
        )
    )
    assert applied2 is False
    assert cache2 == targets
    assert gateway.calls == [targets]
