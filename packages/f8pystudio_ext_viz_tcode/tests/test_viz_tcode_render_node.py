from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from qtpy import QtWidgets

from f8pystudio_ext_viz_tcode.render_nodes.viz_tcode import VizTCodeRenderNode


class _FakeSignal:
    def __init__(self) -> None:
        self.disconnected_callbacks: list[object] = []

    def disconnect(self, callback: object) -> None:
        self.disconnected_callbacks.append(callback)


def test_get_widget_logs_read_failure(caplog) -> None:
    class _BrokenNode(VizTCodeRenderNode):
        def get_widget(self, name: str) -> object:
            _ = name
            raise RuntimeError("widget map unavailable")

    node = _BrokenNode.__new__(_BrokenNode)

    with caplog.at_level("DEBUG", logger="f8pystudio_ext_viz_tcode.render_nodes.viz_tcode"):
        widget = node._get_widget()

    assert widget is None
    assert "failed to read TCode viewer widget" in caplog.text


def test_bind_app_quit_hook_logs_connect_failure(monkeypatch, caplog) -> None:
    node = VizTCodeRenderNode.__new__(VizTCodeRenderNode)
    node._app_quit_hook_bound = False

    class _BrokenSignal:
        def connect(self, callback: object) -> None:
            _ = callback
            raise RuntimeError("signal unavailable")

    fake_app = SimpleNamespace(aboutToQuit=_BrokenSignal())
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: fake_app))

    with caplog.at_level("ERROR", logger="f8pystudio_ext_viz_tcode.render_nodes.viz_tcode"):
        node._bind_app_quit_hook()

    assert node._app_quit_hook_bound is False
    assert "failed to bind TCode viewer app quit hook" in caplog.text


def test_app_quit_shutdown_failure_is_logged(caplog) -> None:
    node = VizTCodeRenderNode.__new__(VizTCodeRenderNode)

    class _BrokenWindow:
        def force_shutdown(self) -> None:
            raise RuntimeError("shutdown failed")

    node._viewer_window = _BrokenWindow()

    with caplog.at_level("ERROR", logger="f8pystudio_ext_viz_tcode.render_nodes.viz_tcode"):
        node._on_app_about_to_quit()

    assert "failed to shutdown TCode viewer during app quit" in caplog.text


class _FakePresenter:
    def __init__(self) -> None:
        self.detach_calls = 0
        self.detach_viewer_calls = 0

    def on_detach(self) -> None:
        self.detach_calls += 1

    def detach_viewer(self) -> None:
        self.detach_viewer_calls += 1


class _FakeWindow:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def force_shutdown(self) -> None:
        self.shutdown_calls += 1


def test_graph_teardown_releases_tcode_viewer(monkeypatch) -> None:
    node = VizTCodeRenderNode.__new__(VizTCodeRenderNode)
    node._presenter = _FakePresenter()
    node._viewer_window = _FakeWindow()
    node._app_quit_hook_bound = True

    fake_signal = _FakeSignal()
    fake_app = SimpleNamespace(aboutToQuit=fake_signal)
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: fake_app))

    VizTCodeRenderNode.on_graph_teardown(node)
    VizTCodeRenderNode.on_graph_teardown(node)

    assert node._presenter.detach_calls == 2
    assert node._presenter.detach_viewer_calls == 2
    assert node._viewer_window is None
    assert node._app_quit_hook_bound is False
    assert len(fake_signal.disconnected_callbacks) == 1


def test_tcode_web_asset_recovers_from_detach_without_reopen() -> None:
    viewer_js = (
        Path(__file__).resolve().parents[1]
        / "f8pystudio_ext_viz_tcode"
        / "render_nodes"
        / "web_assets"
        / "viz_tcode"
        / "viewer.js"
    ).read_text(encoding="utf-8")

    assert "let detached = false;" in viewer_js
    assert "function requestCreateEmulator(model)" in viewer_js
    assert "detached = false;" in viewer_js
    assert "requestCreateEmulator(desiredCreateModel());" in viewer_js
    assert "requestedCreateModel = null;" in viewer_js
    assert "setState(\"detached\");" in viewer_js
