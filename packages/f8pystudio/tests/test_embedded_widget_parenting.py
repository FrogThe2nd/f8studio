from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.render_nodes.note import _NoteWidget
from f8pystudio.render_nodes.viz_audio import _AudioShmPane
from f8pystudio.render_nodes.viz_text import _PrintPreviewPane
from f8pystudio.render_nodes.viz_three_d import _Skeleton3DControlPane
from f8pystudio.render_nodes.viz_track import _TrackVizPane
from f8pystudio.render_nodes.viz_video import _VideoShmPane
from f8pystudio.render_nodes.viz_wave import _TimeSeriesPane


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_render_preview_pane_children_are_parented_at_construction() -> None:
    _ensure_app()
    panes = [
        _PrintPreviewPane(),
        _AudioShmPane(),
        _VideoShmPane(),
        _TrackVizPane(),
        _TimeSeriesPane(),
        _Skeleton3DControlPane(on_open_clicked=lambda: None),
    ]
    try:
        text_pane = panes[0]
        assert text_pane._copy.parent() is text_pane
        assert text_pane._update.parent() is text_pane
        assert text_pane._wrap.parent() is text_pane
        assert text_pane._text.parent() is text_pane

        audio_pane = panes[1]
        assert audio_pane._update.parent() is audio_pane
        assert audio_pane._plot.parent() is audio_pane

        video_pane = panes[2]
        assert video_pane._update.parent() is video_pane
        assert video_pane._image.parent() is video_pane

        track_pane = panes[3]
        assert track_pane._update.parent() is track_pane
        if track_pane._plot is not None:
            assert track_pane._plot.parent() is track_pane

        wave_pane = panes[4]
        assert wave_pane._clear.parent() is wave_pane
        assert wave_pane._update.parent() is wave_pane
        if wave_pane._plot is not None:
            assert wave_pane._plot.parent() is wave_pane

        three_d_pane = panes[5]
        assert three_d_pane._open_button.parent() is three_d_pane
    finally:
        for pane in panes:
            pane.close()
            pane.deleteLater()


def test_note_widget_embedded_children_are_parented_to_proxy_root() -> None:
    _ensure_app()
    widget = _NoteWidget()
    try:
        assert widget._editor.parent() is widget._pane
        assert widget._preview_switch.parent() is widget._pane
    finally:
        widget.close()
        widget.deleteLater()
