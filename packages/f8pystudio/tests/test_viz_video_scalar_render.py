from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from qtpy import QtGui, QtWidgets

from f8pysdk.video_transport import LatestVideoFrame
from f8pystudio.render_nodes.viz_video import (
    _LatestVideoPane,
    _apply_colormap,
    _normalize_scalar_values,
    _to_bool,
)
from f8pystudio.render_nodes.video_preview import EMBEDDED_VIDEO_MIN_INTERVAL_MS


class _IntervalReader:
    def __init__(self) -> None:
        self.intervals: list[int] = []

    def close(self) -> None:
        return

    def poll_latest(self) -> None:
        return None

    def wait_latest(self, timeout_ms: int) -> None:
        _ = timeout_ms
        return None

    def set_min_sample_interval_ms(self, min_sample_interval_ms: int) -> None:
        self.intervals.append(int(min_sample_interval_ms))


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_normalize_scalar_values_manual_degenerate_range() -> None:
    values = np.array([[5.0, 5.0], [np.nan, np.inf]], dtype=np.float32)
    normalized, alpha = _normalize_scalar_values(
        values,
        range_mode="manual",
        manual_min=1.0,
        manual_max=1.0,
        auto_percentile_lo=2.0,
        auto_percentile_hi=98.0,
        invert=False,
        nan_mode="transparent",
    )
    assert normalized.shape == values.shape
    assert float(normalized[0, 0]) == 0.0
    assert float(normalized[0, 1]) == 0.0
    assert int(alpha[1, 0]) == 0
    assert int(alpha[1, 1]) == 0


def test_normalize_scalar_values_auto_percentile_and_invert() -> None:
    values = np.array([[0.0, 1.0], [2.0, 100.0]], dtype=np.float32)
    normalized, alpha = _normalize_scalar_values(
        values,
        range_mode="auto",
        manual_min=0.0,
        manual_max=1.0,
        auto_percentile_lo=0.0,
        auto_percentile_hi=50.0,
        invert=True,
        nan_mode="zero",
    )
    assert normalized.shape == values.shape
    assert alpha.min() == 255
    assert float(normalized[0, 0]) >= float(normalized[0, 1])
    assert float(normalized[0, 1]) >= float(normalized[1, 0])


def test_normalize_scalar_values_nan_modes() -> None:
    values = np.array([[np.nan, np.inf], [0.5, 1.0]], dtype=np.float32)

    normalized_transparent, alpha_transparent = _normalize_scalar_values(
        values,
        range_mode="manual",
        manual_min=0.0,
        manual_max=1.0,
        auto_percentile_lo=2.0,
        auto_percentile_hi=98.0,
        invert=False,
        nan_mode="transparent",
    )
    assert int(alpha_transparent[0, 0]) == 0
    assert int(alpha_transparent[0, 1]) == 0
    assert float(normalized_transparent[0, 0]) == 0.0

    normalized_max, alpha_max = _normalize_scalar_values(
        values,
        range_mode="manual",
        manual_min=0.0,
        manual_max=1.0,
        auto_percentile_lo=2.0,
        auto_percentile_hi=98.0,
        invert=False,
        nan_mode="max",
    )
    assert int(alpha_max[0, 0]) == 255
    assert int(alpha_max[0, 1]) == 255
    assert float(normalized_max[0, 0]) == 1.0
    assert float(normalized_max[0, 1]) == 1.0


def test_apply_colormap_produces_variable_pixels() -> None:
    values = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)
    rgb = _apply_colormap(values, colormap="turbo")
    assert rgb.shape == (1, 3, 3)
    assert tuple(int(v) for v in rgb[0, 0]) != tuple(int(v) for v in rgb[0, 2])


def test_to_bool_accepts_string_forms() -> None:
    assert _to_bool("true", default=False) is True
    assert _to_bool("false", default=True) is False
    assert _to_bool("1", default=False) is True
    assert _to_bool("0", default=True) is False


def test_video_tick_render_priority_flow_over_scalar_over_video() -> None:
    _ensure_app()
    pane = _LatestVideoPane()
    pane._timer.stop()
    pane.set_update_enabled(True)

    image = QtGui.QImage(2, 2, QtGui.QImage.Format_ARGB32)
    image.fill(QtGui.QColor(10, 20, 30))
    pane._latest_video = image

    calls: list[str] = []

    pane._update_video_cache = lambda: calls.append("update_cache")  # type: ignore[method-assign]
    pane._present = lambda _img: calls.append("present")  # type: ignore[method-assign]

    pane._try_render_flow = lambda: image  # type: ignore[method-assign]
    pane._try_render_scalar = lambda: calls.append("scalar") or image  # type: ignore[method-assign]
    pane._tick()
    assert calls == ["update_cache", "present"]

    calls.clear()
    pane._try_render_flow = lambda: None  # type: ignore[method-assign]
    pane._try_render_scalar = lambda: calls.append("scalar") or image  # type: ignore[method-assign]
    pane._tick()
    assert calls == ["update_cache", "scalar", "present"]

    calls.clear()
    pane._try_render_flow = lambda: None  # type: ignore[method-assign]
    pane._try_render_scalar = lambda: None  # type: ignore[method-assign]
    pane._tick()
    assert calls == ["update_cache", "present"]


def test_try_render_scalar_skips_non_scalar_format() -> None:
    _ensure_app()
    pane = _LatestVideoPane()
    pane._timer.stop()
    pane._scalar_display_mode = "colormap"
    pane._ensure_scalar_reader = lambda: True  # type: ignore[method-assign]
    pane._scalar_reader = SimpleNamespace(
        poll_latest=lambda: LatestVideoFrame(
            width=2,
            height=2,
            pitch=8,
            fmt=1,
            frame_id=1,
            ts_ms=0,
            payload=memoryview(bytes(16)),
        )
    )
    assert pane._try_render_scalar() is None


def test_video_pane_accepts_zenoh_video_source() -> None:
    _ensure_app()
    pane = _LatestVideoPane()
    pane._timer.stop()
    pane.set_config(
        video_stream_key="f8/test/video/source",
        throttle_ms=33,
        flow_stream_key="",
        flow_display_mode="off",
        flow_mag_scale=20.0,
        flow_stride=12,
        scalar_stream_key="",
        scalar_display_mode="off",
        scalar_colormap="turbo",
        scalar_range_mode="auto",
        scalar_min=-1.0,
        scalar_max=1.0,
        scalar_auto_percentile_lo=2.0,
        scalar_auto_percentile_hi=98.0,
        scalar_invert=False,
        scalar_nan_mode="transparent",
        scale_mode="fit",
    )
    try:
        assert pane._video_stream_key == "f8/test/video/source"
        assert pane._timer.isActive()
        assert pane._timer.interval() == EMBEDDED_VIDEO_MIN_INTERVAL_MS
    finally:
        pane.detach()


def test_video_pane_updates_existing_reader_sample_interval() -> None:
    _ensure_app()
    pane = _LatestVideoPane()
    pane._timer.stop()
    video_reader = _IntervalReader()
    flow_reader = _IntervalReader()
    scalar_reader = _IntervalReader()
    pane._video_reader = video_reader
    pane._flow_reader = flow_reader
    pane._scalar_reader = scalar_reader

    pane.set_config(
        video_stream_key="",
        throttle_ms=250,
        flow_stream_key="",
        flow_display_mode="off",
        flow_mag_scale=20.0,
        flow_stride=12,
        scalar_stream_key="",
        scalar_display_mode="off",
        scalar_colormap="turbo",
        scalar_range_mode="auto",
        scalar_min=-1.0,
        scalar_max=1.0,
        scalar_auto_percentile_lo=2.0,
        scalar_auto_percentile_hi=98.0,
        scalar_invert=False,
        scalar_nan_mode="transparent",
        scale_mode="fit",
    )
    try:
        assert pane._timer.interval() == 250
        assert video_reader.intervals == [250]
        assert flow_reader.intervals == [250]
        assert scalar_reader.intervals == [250]
    finally:
        pane.detach()
