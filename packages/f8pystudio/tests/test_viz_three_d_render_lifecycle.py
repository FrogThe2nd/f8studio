from __future__ import annotations

import logging
from typing import Any

from qtpy import QtWidgets

from f8pystudio.render_nodes.viz_three_d import _Skeleton3DViewerWindow, VizThreeDRenderNode
from f8pystudio.ui_bus import UiCommand


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeSignal:
    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, callback: Any) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: Any) -> None:
        kept: list[Any] = []
        for registered in self._callbacks:
            if registered is callback:
                continue
            kept.append(registered)
        self._callbacks = kept

    def emit(self, *args: Any) -> None:
        for callback in list(self._callbacks):
            callback(*args)


class _FakeSettings:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    def setAttribute(self, key: Any, value: Any) -> None:
        self.calls.append((key, value))


class _FakePage:
    def __init__(self) -> None:
        self.renderProcessTerminated = _FakeSignal()
        self.scripts: list[str] = []

    def runJavaScript(self, script: str) -> None:
        self.scripts.append(str(script or ""))


class _FakeWebView(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loadFinished = _FakeSignal()
        self._settings = _FakeSettings()
        self._page = _FakePage()
        self.stopped = False
        self.urls: list[Any] = []
        self.deleted = False

    def settings(self) -> _FakeSettings:
        return self._settings

    def setContextMenuPolicy(self, policy: Any) -> None:
        _ = policy

    def page(self) -> _FakePage:
        return self._page

    def stop(self) -> None:
        self.stopped = True

    def setUrl(self, url: Any) -> None:
        self.urls.append(url)

    def deleteLater(self) -> None:
        self.deleted = True
        super().deleteLater()


def _new_window() -> tuple[_Skeleton3DViewerWindow, list[bool], list[str]]:
    open_states: list[bool] = []
    statuses: list[str] = []
    window = _Skeleton3DViewerWindow(
        on_open_state_changed=lambda is_open: open_states.append(bool(is_open)),
        on_viewer_status_changed=lambda text: statuses.append(str(text or "")),
    )
    window.show = lambda: None  # type: ignore[method-assign]
    window.raise_ = lambda: None  # type: ignore[method-assign]
    window.activateWindow = lambda: None  # type: ignore[method-assign]
    return window, open_states, statuses


def test_viewer_close_keeps_web_view_and_next_open_reuses_it() -> None:
    _ensure_app()
    window, open_states, _statuses = _new_window()
    first = _FakeWebView(window)
    second = _FakeWebView(window)
    pool = [first, second]

    window._create_web_view = lambda: pool.pop(0)  # type: ignore[method-assign]
    window.open_viewer()
    assert window._view is first

    window.close()
    assert window._view is first
    assert first.stopped is False
    assert first.deleted is False

    window.open_viewer()
    assert window._view is first
    assert pool == [second]
    assert open_states == [True, False, True]


def test_render_process_termination_reports_status_and_releases_view(caplog) -> None:
    _ensure_app()
    window, open_states, statuses = _new_window()
    view = _FakeWebView(window)
    window._create_web_view = lambda: view  # type: ignore[method-assign]
    window.open_viewer()

    with caplog.at_level(logging.ERROR):
        view.page().renderProcessTerminated.emit(2, 137)

    assert window._view is None
    assert any("render process terminated" in message.lower() for message in statuses)
    assert any("render process terminated" in str(record.message).lower() for record in caplog.records)
    assert open_states == [True, False]


class _FakePresenter:
    def __init__(self) -> None:
        self.detach_calls = 0
        self.detach_viewer_calls = 0
        self.world_up_values: list[str] = []

    def on_detach(self) -> None:
        self.detach_calls += 1

    def detach_viewer(self) -> None:
        self.detach_viewer_calls += 1

    def on_set_world_up(self, world_up: str) -> None:
        self.world_up_values.append(str(world_up))


class _FakeViewerWindow:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def force_shutdown(self) -> None:
        self.shutdown_calls += 1


def test_render_node_graph_teardown_is_idempotent() -> None:
    node = VizThreeDRenderNode.__new__(VizThreeDRenderNode)
    node._presenter = _FakePresenter()
    node._viewer_window = _FakeViewerWindow()
    unbind_calls: list[str] = []
    node._unbind_app_quit_hook = lambda: unbind_calls.append("unbind")  # type: ignore[method-assign]

    VizThreeDRenderNode.on_graph_teardown(node)
    VizThreeDRenderNode.on_graph_teardown(node)

    assert node._presenter.detach_calls == 2
    assert node._presenter.detach_viewer_calls == 2
    assert unbind_calls == ["unbind", "unbind"]
    assert node._viewer_window is None


def test_render_node_world_up_command_applies_without_reopen() -> None:
    node = VizThreeDRenderNode.__new__(VizThreeDRenderNode)
    node._presenter = _FakePresenter()
    node._viewer_window = None
    node._get_widget = lambda: None  # type: ignore[method-assign]

    node.apply_ui_command(
        UiCommand(
            node_id="viz3d1",
            command="viz.three_d.world_up",
            payload={"worldUp": "+z"},
            ts_ms=123,
        )
    )

    assert node._presenter.world_up_values == ["+z"]
