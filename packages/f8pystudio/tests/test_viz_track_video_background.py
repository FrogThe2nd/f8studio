from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.render_nodes.video_preview import EMBEDDED_VIDEO_MIN_INTERVAL_MS
from f8pystudio.render_nodes.viz_track import _TrackVizPane


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_trackviz_accepts_video_background_stream_with_safe_timer_floor() -> None:
    _ensure_app()
    pane = _TrackVizPane()
    try:
        pane._video_timer.stop()

        assert pane._video_timer.interval() == EMBEDDED_VIDEO_MIN_INTERVAL_MS

        pane.set_scene(
            {
                "videoStreamKey": "f8/test/video/trackviz",
                "throttleMs": 33,
                "width": 1920,
                "height": 1080,
                "tracks": [],
            }
        )

        assert pane._video_stream_key == "f8/test/video/trackviz"
        assert pane._video_frame_throttle_ms == EMBEDDED_VIDEO_MIN_INTERVAL_MS
        assert pane._video_timer.interval() == EMBEDDED_VIDEO_MIN_INTERVAL_MS
        assert pane._scene_payload is not None
        assert pane._scene_payload["videoStreamKey"] == "f8/test/video/trackviz"
        assert pane._video_timer.isActive()
    finally:
        pane.detach()
        pane.close()
        pane.deleteLater()
