from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from f8pystudio.nodegraph import operator_basenode as operator_module
from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorNodeItem


def test_operator_drag_start_position_failure_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message))

    monkeypatch.setattr(operator_module.logger, "debug", _debug)
    item = SimpleNamespace(id="op1", xy_pos=[object(), 2.0])

    assert F8StudioOperatorNodeItem._current_xy_pos(item) is None
    assert any("Failed to record operator drag start position" in message for message in debug_messages)


def test_operator_drop_rebind_known_failure_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    exception_messages: list[str] = []

    def _exception(message: str, *args: object, **kwargs: object) -> None:
        _ = kwargs
        exception_messages.append(message % args)

    class _Graph:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[float, float], str]] = []

        def on_operator_drop(self, *, node_id: str, start_pos: tuple[float, float], start_container_id: str) -> None:
            self.calls.append((node_id, start_pos, start_container_id))
            raise RuntimeError("drop failed")

    monkeypatch.setattr(operator_module.logger, "exception", _exception)
    item = SimpleNamespace(id="op1")
    graph = _Graph()

    F8StudioOperatorNodeItem._notify_operator_drop(
        item,
        graph=graph,
        start_xy=(10.0, 12.0),
        start_container_id="svc_old",
    )

    assert graph.calls == [("op1", (10.0, 12.0), "svc_old")]
    assert exception_messages == ["Operator drop rebind failed for node id=op1"]


def test_operator_drop_rebind_programmer_error_is_not_swallowed() -> None:
    class _Graph:
        def on_operator_drop(self, *, node_id: str, start_pos: tuple[float, float], start_container_id: str) -> None:
            _ = node_id
            _ = start_pos
            _ = start_container_id
            raise AssertionError("programmer error")

    item = SimpleNamespace(id="op1")

    with pytest.raises(AssertionError, match="programmer error"):
        F8StudioOperatorNodeItem._notify_operator_drop(
            item,
            graph=_Graph(),
            start_xy=(10.0, 12.0),
            start_container_id="svc_old",
        )


def test_operator_service_running_failure_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message) % args)

    class _Bridge:
        def is_service_running(self, service_id: str) -> bool:
            assert service_id == "svc1"
            raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(operator_module.logger, "debug", _debug)
    item = SimpleNamespace(
        _bridge=lambda: _Bridge(),
        _service_id=lambda: "svc1",
    )

    assert F8StudioOperatorNodeItem._is_service_running(item) is False
    assert debug_messages == ["Failed to query operator service process state for service id=svc1."]


def test_operator_service_state_refresh_failures_are_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message) % args)

    def _raise_refresh() -> None:
        raise RuntimeError("refresh failed")

    def _raise_single_shot(delay_ms: int, callback: Any) -> None:
        _ = delay_ms
        _ = callback
        raise RuntimeError("timer failed")

    monkeypatch.setattr(operator_module.logger, "debug", _debug)
    monkeypatch.setattr(operator_module.QtCore.QTimer, "singleShot", _raise_single_shot)
    item = SimpleNamespace(
        _service_id=lambda: "svc1",
        _refresh_inline_command_rows=_raise_refresh,
        draw_node=lambda: None,
    )

    F8StudioOperatorNodeItem._on_bridge_service_process_state(item, service_id="svc1", running=True)

    assert debug_messages == [
        "Failed to refresh operator command rows after service state change: service_id=svc1.",
        "Failed to schedule operator redraw after service state change: service_id=svc1.",
    ]
