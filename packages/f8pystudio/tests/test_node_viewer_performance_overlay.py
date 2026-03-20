from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_performance_overlay_tracks_paint_samples_and_proxy_counts() -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()
    viewer.resize(640, 480)
    viewer.show()

    proxy_button = QtWidgets.QPushButton("inline")
    proxy_button.resize(120, 30)
    proxy_button.show()

    scene = viewer.scene()
    assert scene is not None

    proxy = QtWidgets.QGraphicsProxyWidget()
    proxy.setWidget(proxy_button)
    proxy.setPos(30.0, 40.0)
    scene.addItem(proxy)

    QtWidgets.QApplication.processEvents()

    initial_snapshot = viewer.performance_overlay_snapshot()
    assert viewer.performance_overlay_enabled() is False
    assert initial_snapshot["paint_samples"] == 0.0
    assert initial_snapshot["visible_proxy_widget_count"] >= 1.0

    viewer.set_performance_overlay_enabled(True)
    QtWidgets.QApplication.processEvents()
    viewer.viewport().update()
    viewer.grab()
    QtWidgets.QApplication.processEvents()

    snapshot = viewer.performance_overlay_snapshot()
    assert viewer.performance_overlay_enabled() is True
    assert viewer._perf_overlay_timer.isActive() is True
    assert viewer._perf_overlay_label.isVisible() is True
    assert snapshot["paint_samples"] >= 1.0
    assert snapshot["last_paint_ms"] >= 0.0
    assert snapshot["ema_paint_ms"] >= 0.0
    assert snapshot["visible_proxy_widget_count"] >= 1.0
    assert "paint " in viewer._perf_overlay_label.text()
    assert "proxy " in viewer._perf_overlay_label.text()

    viewer.set_performance_overlay_enabled(False)

    assert viewer.performance_overlay_enabled() is False
    assert viewer._perf_overlay_timer.isActive() is False
    assert viewer._perf_overlay_label.isVisible() is False
    assert viewer._perf_overlay_label.text() == ""
