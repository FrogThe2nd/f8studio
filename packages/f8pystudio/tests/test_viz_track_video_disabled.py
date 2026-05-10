from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.render_nodes.viz_track import _TrackVizPane


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_trackviz_ignores_video_background_stream_for_gui_responsiveness() -> None:
    _ensure_app()
    pane = _TrackVizPane()
    try:
        pane._video_timer.stop()

        pane.set_scene(
            {
                "videoStreamKey": "f8/test/video/trackviz",
                "throttleMs": 33,
                "width": 1920,
                "height": 1080,
                "tracks": [],
            }
        )

        assert pane._video_stream_key == ""
        assert pane._video_reader is None
        assert pane._video_size is None
        assert pane._scene_payload is not None
        assert pane._scene_payload["videoStreamKey"] == ""
        assert not pane._video_timer.isActive()
    finally:
        pane.detach()
        pane.close()
        pane.deleteLater()
