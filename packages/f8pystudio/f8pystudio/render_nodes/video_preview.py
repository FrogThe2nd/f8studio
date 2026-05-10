from __future__ import annotations

from qtpy import QtCore, QtGui

EMBEDDED_VIDEO_MIN_INTERVAL_MS = 100
EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH = 640
EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT = 360


def embedded_video_timer_interval_ms(throttle_ms: int) -> int:
    requested_ms = max(1, int(throttle_ms))
    return max(requested_ms, EMBEDDED_VIDEO_MIN_INTERVAL_MS)


def bounded_preview_size(
    *,
    width: int,
    height: int,
    max_width: int = EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH,
    max_height: int = EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT,
) -> tuple[int, int]:
    source_width = max(1, int(width))
    source_height = max(1, int(height))
    preview_max_width = max(1, int(max_width))
    preview_max_height = max(1, int(max_height))
    if source_width <= preview_max_width and source_height <= preview_max_height:
        return source_width, source_height
    scale = min(preview_max_width / float(source_width), preview_max_height / float(source_height))
    return max(1, int(source_width * scale)), max(1, int(source_height * scale))


def preview_sample_steps(*, width: int, height: int) -> tuple[int, int, int, int]:
    source_width = max(1, int(width))
    source_height = max(1, int(height))
    preview_width, preview_height = bounded_preview_size(width=source_width, height=source_height)
    step_x = max(1, (source_width + preview_width - 1) // preview_width)
    step_y = max(1, (source_height + preview_height - 1) // preview_height)
    sampled_width = max(1, (source_width + step_x - 1) // step_x)
    sampled_height = max(1, (source_height + step_y - 1) // step_y)
    return step_x, step_y, sampled_width, sampled_height


def copy_bgra_preview_image(
    payload: memoryview,
    *,
    width: int,
    height: int,
    pitch: int,
    max_width: int = EMBEDDED_VIDEO_MAX_PREVIEW_WIDTH,
    max_height: int = EMBEDDED_VIDEO_MAX_PREVIEW_HEIGHT,
) -> QtGui.QImage | None:
    source_width = int(width)
    source_height = int(height)
    source_pitch = int(pitch)
    if source_width <= 0 or source_height <= 0 or source_pitch < source_width * 4:
        return None
    required_bytes = source_pitch * source_height
    if len(payload) < required_bytes:
        return None

    source = QtGui.QImage(payload, source_width, source_height, source_pitch, QtGui.QImage.Format_ARGB32)
    if source.isNull():
        return None

    preview_width, preview_height = bounded_preview_size(
        width=source_width,
        height=source_height,
        max_width=max_width,
        max_height=max_height,
    )
    if preview_width == source_width and preview_height == source_height:
        return source.copy()
    return source.scaled(
        QtCore.QSize(preview_width, preview_height),
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.FastTransformation,
    ).copy()
