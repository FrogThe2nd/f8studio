from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pysdk.specs import (  # noqa: E402
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
    F8RuntimeService,
    F8StateAccess,
    F8StateSpec,
)
from f8pysdk.specs import string_schema  # noqa: E402

from f8pystudio.ui.mainwin.main_window import F8StudioMainWin  # noqa: E402


def _compiled(*, state_value: str) -> object:
    graph = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        services=[
            F8RuntimeService(serviceId="svcA", serviceClass="svc.alpha"),
        ],
        nodes=[
            F8RuntimeNode(
                nodeId="svcA",
                serviceId="svcA",
                serviceClass="svc.alpha",
                stateFields=[
                    F8StateSpec(name="value", valueSchema=string_schema(), access=F8StateAccess.rw),
                ],
                stateValues={"value": state_value},
            ),
        ],
        edges=[
            F8Edge(
                edgeId="edge-1",
                fromServiceId="svcA",
                fromOperatorId=None,
                fromPort="value",
                toServiceId="svcA",
                toOperatorId=None,
                toPort="value",
                kind=F8EdgeKindEnum.state,
                strategy=F8EdgeStrategyEnum.latest,
            )
        ],
    )
    return SimpleNamespace(global_graph=graph, per_service={"svcA": graph}, warnings=())


class _FakeLogDock:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.exceptions: list[tuple[str, str, str]] = []

    def append(self, channel: str, line: str) -> None:
        self.lines.append((str(channel), str(line)))

    def report_exception(self, channel: str, context: str, exc: Exception) -> None:
        self.exceptions.append((str(channel), str(context), str(exc)))


class _FakeBridge:
    def __init__(self, *, running: bool) -> None:
        self._running = bool(running)
        self.deploy_calls: list[str] = []
        self.sync_calls: list[object] = []

    def is_service_running(self, service_id: str) -> bool:
        _ = service_id
        return self._running

    def deploy_service_rungraph(self, service_id: str, *, compiled: object | None = None) -> None:
        _ = compiled
        self.deploy_calls.append(str(service_id))

    def sync_studio_runtime(self, compiled: object) -> None:
        self.sync_calls.append(compiled)


class _FakeMain:
    _on_auto_deploy_timeout = F8StudioMainWin._on_auto_deploy_timeout
    _on_studio_runtime_sync_timeout = F8StudioMainWin._on_studio_runtime_sync_timeout
    _mark_auto_deploy_observed = F8StudioMainWin._mark_auto_deploy_observed
    _deploy_fingerprint_from_compiled = F8StudioMainWin._deploy_fingerprint_from_compiled
    _refresh_auto_deploy_fingerprint = F8StudioMainWin._refresh_auto_deploy_fingerprint
    _mark_auto_deploy_synced = F8StudioMainWin._mark_auto_deploy_synced

    def __init__(self, *, compiled: object, running: bool, current_undo_index: int, last_fingerprint: str = "") -> None:
        self._compiled = compiled
        self._auto_deploy_enabled = True
        self._undo_index = int(current_undo_index)
        self._last_auto_deploy_observed_undo_index = int(current_undo_index) - 1
        self._last_auto_deploy_fingerprint = str(last_fingerprint)
        self.studio_graph = object()
        self._log_dock = _FakeLogDock()
        self._bridge = _FakeBridge(running=running)

    def _current_undo_index(self) -> int:
        return self._undo_index

    def _declared_graph_services(self) -> dict[str, str]:
        return {"svcA": "svc.alpha"}


def test_auto_deploy_skips_when_only_runtime_state_values_change(monkeypatch) -> None:
    compiled = _compiled(state_value="pause")
    fake_main = _FakeMain(compiled=compiled, running=True, current_undo_index=5)
    fake_main._last_auto_deploy_fingerprint = fake_main._deploy_fingerprint_from_compiled(compiled)  # type: ignore[arg-type]

    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window.compile_runtime_graphs_from_studio", lambda _graph: compiled)

    F8StudioMainWin._on_auto_deploy_timeout(fake_main)

    assert fake_main._bridge.deploy_calls == []
    assert fake_main._last_auto_deploy_observed_undo_index == 5
    assert any("deploy fingerprint unchanged" in line for _channel, line in fake_main._log_dock.lines)


def test_auto_deploy_updates_baseline_when_no_services_are_running(monkeypatch) -> None:
    compiled = _compiled(state_value="pause")
    fake_main = _FakeMain(compiled=compiled, running=False, current_undo_index=7)

    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window.compile_runtime_graphs_from_studio", lambda _graph: compiled)

    F8StudioMainWin._on_auto_deploy_timeout(fake_main)

    assert fake_main._bridge.deploy_calls == []
    assert fake_main._last_auto_deploy_observed_undo_index == 7
    assert fake_main._last_auto_deploy_fingerprint == fake_main._deploy_fingerprint_from_compiled(compiled)  # type: ignore[arg-type]
    assert any("no running services" in line for _channel, line in fake_main._log_dock.lines)


def test_auto_deploy_redeploys_when_deploy_fingerprint_changes(monkeypatch) -> None:
    compiled = _compiled(state_value="pause")
    fake_main = _FakeMain(compiled=compiled, running=True, current_undo_index=9, last_fingerprint="older")

    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window.compile_runtime_graphs_from_studio", lambda _graph: compiled)

    F8StudioMainWin._on_auto_deploy_timeout(fake_main)

    assert fake_main._bridge.deploy_calls == ["svcA"]
    assert fake_main._last_auto_deploy_observed_undo_index == 9
    assert fake_main._last_auto_deploy_fingerprint == fake_main._deploy_fingerprint_from_compiled(compiled)  # type: ignore[arg-type]
    assert any("applying rungraph" in line for _channel, line in fake_main._log_dock.lines)


def test_studio_runtime_sync_updates_local_runtime_without_remote_deploy(monkeypatch) -> None:
    compiled = _compiled(state_value="pause")
    fake_main = _FakeMain(compiled=compiled, running=True, current_undo_index=3)

    monkeypatch.setattr("f8pystudio.ui.mainwin.main_window.compile_runtime_graphs_from_studio", lambda _graph: compiled)

    F8StudioMainWin._on_studio_runtime_sync_timeout(fake_main)

    assert fake_main._bridge.sync_calls == [compiled]
    assert fake_main._bridge.deploy_calls == []
