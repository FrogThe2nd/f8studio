from __future__ import annotations

from types import SimpleNamespace

from qtpy import QtWidgets

from f8pystudio_ext_viz_tcode.render_nodes.viz_tcode import VizTCodeRenderNode


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
