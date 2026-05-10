from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.render_nodes.video_preview import (
    EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT,
    EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH,
    EMBEDDED_VIDEO_MIN_INTERVAL_MS,
    bounded_preview_size,
    copy_bgra_preview_image,
    embedded_video_timer_interval_ms,
    preview_sample_steps,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_embedded_video_timer_interval_has_gui_safe_floor() -> None:
    assert embedded_video_timer_interval_ms(1) == EMBEDDED_VIDEO_MIN_INTERVAL_MS
    assert embedded_video_timer_interval_ms(33) == EMBEDDED_VIDEO_MIN_INTERVAL_MS
    assert embedded_video_timer_interval_ms(250) == 250


def test_bounded_preview_size_preserves_aspect_within_budget() -> None:
    width, height = bounded_preview_size(width=1920, height=1080)

    assert width <= EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH
    assert height <= EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT
    assert (width, height) == (640, 360)


def test_preview_sample_steps_cover_large_frame_without_exceeding_budget() -> None:
    step_x, step_y, sampled_width, sampled_height = preview_sample_steps(width=1920, height=1080)

    assert step_x == 3
    assert step_y == 3
    assert sampled_width == 640
    assert sampled_height == 360


def test_preview_sample_steps_never_exceed_budget_after_rounding() -> None:
    step_x, step_y, sampled_width, sampled_height = preview_sample_steps(width=800, height=450)

    assert step_x == 2
    assert step_y == 2
    assert sampled_width <= EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH
    assert sampled_height <= EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT


def test_copy_bgra_preview_image_downscales_large_frames() -> None:
    _ensure_app()
    width = 1280
    height = 720
    pitch = width * 4
    payload = bytearray(pitch * height)
    for index in range(0, len(payload), 4):
        payload[index : index + 4] = bytes((10, 20, 30, 255))

    view = memoryview(payload)
    try:
        image = copy_bgra_preview_image(view, width=width, height=height, pitch=pitch)
    finally:
        view.release()

    assert image is not None
    assert image.width() == 640
    assert image.height() == 360
