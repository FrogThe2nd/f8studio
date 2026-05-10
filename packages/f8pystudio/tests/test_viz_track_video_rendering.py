from __future__ import annotations

from qtpy import QtGui, QtWidgets

from f8pystudio.render_nodes.viz_track import _TRACKVIZ_VIDEO_MIN_INTERVAL_MS, _TrackVizPane


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_trackviz_video_uses_slow_background_timer() -> None:
    _ensure_app()
    pane = _TrackVizPane()
    try:
        pane._video_timer.stop()

        assert pane._video_timer.interval() == _TRACKVIZ_VIDEO_MIN_INTERVAL_MS
        assert pane._track_video_interval_ms(33) == _TRACKVIZ_VIDEO_MIN_INTERVAL_MS
        assert pane._track_video_interval_ms(500) == 500

        pane.set_scene(
            {
                "videoStreamKey": "f8/test/video/trackviz",
                "throttleMs": 33,
                "width": 1920,
                "height": 1080,
                "tracks": [],
            }
        )

        assert pane._video_frame_throttle_ms == _TRACKVIZ_VIDEO_MIN_INTERVAL_MS
        assert pane._video_timer.interval() == _TRACKVIZ_VIDEO_MIN_INTERVAL_MS
    finally:
        pane.detach()
        pane.close()
        pane.deleteLater()


def test_trackviz_video_frame_is_separate_background_item() -> None:
    _ensure_app()
    pane = _TrackVizPane()
    try:
        pane._video_timer.stop()
        pane._video_size = (1920, 1080)
        image = QtGui.QImage(640, 360, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor(10, 20, 30))

        pane._set_video_frame(image)
        assert pane._video_item is not None
        assert pane._video_item.isVisible()
        assert pane._video_item.pixmap().width() == 640
        assert pane._video_item.pixmap().height() == 360
        assert pane._video_item.zValue() < pane._canvas.zValue()
        assert pane._video_item.cacheMode() == QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache

        transform = pane._video_item.transform()
        assert round(transform.m11(), 3) == 3.0
        assert round(transform.m22(), 3) == 3.0
    finally:
        pane.detach()
        pane.close()
        pane.deleteLater()
