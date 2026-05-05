from __future__ import annotations

from typing import Any, Callable

import numpy as np
from NodeGraphQt.nodes.base_node import NodeBaseWidget
from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk.shm import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16, VIDEO_FORMAT_SCALAR1_F32
from f8pysdk.video_transport import (
    LatestVideoFrameTransport,
    LegacyShmLatestVideoFrameTransport,
    ZenohLatestVideoFrameTransport,
)

from ..nodegraph.operator_basenode import F8StudioOperatorBaseNode
from ..nodegraph.viz_operator_nodeitem import F8StudioVizOperatorNodeItem
from f8pystudio.contracts.ui_commands import UiCommand

_STATE_UI_UPDATE = "uiUpdate"
_WIDGET_NAME = "__video_latest"
_TRANSPORT_LEGACY_SHM = "legacy_shm"
_TRANSPORT_ZENOH = "zenoh"


def _normalize_frame_transport(value: object, *, zenoh_key: str, shm_name: str) -> str:
    transport = str(value or "").strip().lower()
    if transport in (_TRANSPORT_LEGACY_SHM, _TRANSPORT_ZENOH):
        return transport
    if str(zenoh_key or "").strip():
        return _TRANSPORT_ZENOH
    if str(shm_name or "").strip():
        return _TRANSPORT_LEGACY_SHM
    return _TRANSPORT_ZENOH


def _hsv_to_rgb_u8(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    i = np.floor(h * 6.0).astype(np.int32)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    m = i % 6

    r = np.select([m == 0, m == 1, m == 2, m == 3, m == 4], [v, q, p, p, t], default=v)
    g = np.select([m == 0, m == 1, m == 2, m == 3, m == 4], [t, v, v, q, p], default=p)
    b = np.select([m == 0, m == 1, m == 2, m == 3, m == 4], [p, p, t, v, v], default=q)
    rgb = np.stack([r, g, b], axis=2)
    return np.clip(rgb * 255.0, 0.0, 255.0).astype(np.uint8)


def _colormap_gray(v: np.ndarray) -> np.ndarray:
    vv = np.clip(v, 0.0, 1.0)
    return np.stack([vv, vv, vv], axis=2)


def _colormap_turbo(v: np.ndarray) -> np.ndarray:
    vv = np.clip(v, 0.0, 1.0)
    r = np.clip(1.6 * vv - 0.2, 0.0, 1.0)
    g = np.clip(1.2 - np.abs(2.0 * vv - 1.0) * 1.6, 0.0, 1.0)
    b = np.clip(1.2 * (1.0 - vv) - 0.1, 0.0, 1.0)
    return np.stack([r, g, b], axis=2)


def _colormap_viridis(v: np.ndarray) -> np.ndarray:
    vv = np.clip(v, 0.0, 1.0)
    r = np.clip(0.28 + 0.65 * vv**1.15, 0.0, 1.0)
    g = np.clip(0.08 + 1.05 * vv - 0.25 * vv**2.0, 0.0, 1.0)
    b = np.clip(0.40 + 0.65 * (1.0 - vv) ** 1.1, 0.0, 1.0)
    return np.stack([r, g, b], axis=2)


def _colormap_magma(v: np.ndarray) -> np.ndarray:
    vv = np.clip(v, 0.0, 1.0)
    r = np.clip(0.05 + 1.20 * vv - 0.20 * vv**2.0, 0.0, 1.0)
    g = np.clip(0.00 + 0.90 * vv**1.7, 0.0, 1.0)
    b = np.clip(0.10 + 0.65 * (1.0 - vv) ** 1.6, 0.0, 1.0)
    return np.stack([r, g, b], axis=2)


def _apply_colormap(values_01: np.ndarray, *, colormap: str) -> np.ndarray:
    cmap = str(colormap or "").strip().lower()
    if cmap == "gray":
        rgb = _colormap_gray(values_01)
    elif cmap == "viridis":
        rgb = _colormap_viridis(values_01)
    elif cmap == "magma":
        rgb = _colormap_magma(values_01)
    else:
        rgb = _colormap_turbo(values_01)
    return np.clip(rgb * 255.0, 0.0, 255.0).astype(np.uint8)


def _normalize_scalar_values(
    values: np.ndarray,
    *,
    range_mode: str,
    manual_min: float,
    manual_max: float,
    auto_percentile_lo: float,
    auto_percentile_hi: float,
    invert: bool,
    nan_mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    finite_mask = np.isfinite(values)
    alpha = np.full(values.shape, 255, dtype=np.uint8)
    normalized = np.zeros(values.shape, dtype=np.float32)

    if finite_mask.any():
        finite_values = values[finite_mask].astype(np.float32, copy=False)
        if str(range_mode or "").strip().lower() == "manual":
            lo = float(manual_min)
            hi = float(manual_max)
        else:
            lo_pct = float(max(0.0, min(100.0, auto_percentile_lo)))
            hi_pct = float(max(0.0, min(100.0, auto_percentile_hi)))
            if hi_pct < lo_pct:
                lo_pct, hi_pct = hi_pct, lo_pct
            lo = float(np.percentile(finite_values, lo_pct))
            hi = float(np.percentile(finite_values, hi_pct))

        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = float(np.min(finite_values))
            hi = float(np.max(finite_values))

        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            normalized = ((values.astype(np.float32, copy=False) - lo) / (hi - lo)).astype(np.float32, copy=False)
            normalized = np.clip(normalized, 0.0, 1.0)
        else:
            normalized.fill(0.0)
    else:
        normalized.fill(0.0)

    nan_mode_normalized = str(nan_mode or "").strip().lower()
    invalid_mask = ~finite_mask
    if invalid_mask.any():
        if nan_mode_normalized == "transparent":
            alpha[invalid_mask] = 0
            normalized[invalid_mask] = 0.0
        elif nan_mode_normalized in ("zero", "min"):
            normalized[invalid_mask] = 0.0
        elif nan_mode_normalized == "max":
            normalized[invalid_mask] = 1.0
        else:
            alpha[invalid_mask] = 0
            normalized[invalid_mask] = 0.0

    if invert:
        normalized = 1.0 - normalized
    return normalized, alpha


def _to_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return bool(default)


class _LatestVideoPane(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._update = QtWidgets.QCheckBox("Update", self)
        self._update.setChecked(True)
        self._update.setStyleSheet(
            """
            QCheckBox { color: rgb(225, 225, 225); }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
                border: 1px solid rgba(255, 255, 255, 90);
                background: rgba(0, 0, 0, 35);
                border-radius: 2px;
            }
            QCheckBox::indicator:checked {
                image: none;
                background: rgba(120, 200, 255, 90);
            }
            """
        )
        top.addStretch()
        top.addWidget(self._update)

        self._image = QtWidgets.QLabel(self)
        self._image.setAlignment(QtCore.Qt.AlignCenter)
        self._image.setMinimumSize(320, 180)
        self._image.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._image.setStyleSheet("background: rgba(0,0,0,60); border: 1px solid rgba(255,255,255,25);")

        layout.addLayout(top)
        layout.addWidget(self._image, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)  # type: ignore[attr-defined]
        self._timer.setInterval(33)

        self._video_reader: LatestVideoFrameTransport | None = None
        self._flow_reader: LatestVideoFrameTransport | None = None
        self._scalar_reader: LatestVideoFrameTransport | None = None
        self._video_shm_name = ""
        self._video_transport = _TRANSPORT_ZENOH
        self._video_key = ""
        self._flow_transport = _TRANSPORT_ZENOH
        self._flow_key = ""
        self._flow_shm_name = ""
        self._scalar_transport = _TRANSPORT_ZENOH
        self._scalar_key = ""
        self._scalar_shm_name = ""
        self._flow_display_mode = "off"
        self._flow_mag_scale = 20.0
        self._flow_stride = 12
        self._scalar_display_mode = "off"
        self._scalar_colormap = "turbo"
        self._scalar_range_mode = "auto"
        self._scalar_min = -1.0
        self._scalar_max = 1.0
        self._scalar_auto_percentile_lo = 2.0
        self._scalar_auto_percentile_hi = 98.0
        self._scalar_invert = False
        self._scalar_nan_mode = "transparent"
        self._scale_mode = "fit"

        self._last_video_frame_id = 0
        self._last_flow_frame_id = 0
        self._latest_video: QtGui.QImage | None = None
        self._last_pixmap_size: QtCore.QSize | None = None
        self.setMinimumWidth(360)
        self.setMinimumHeight(240)

    def update_checkbox(self) -> QtWidgets.QCheckBox:
        return self._update

    def update_enabled(self) -> bool:
        return bool(self._update.isChecked())

    def set_update_enabled(self, enabled: bool) -> None:
        self._update.setChecked(bool(enabled))
        self._sync_timer_with_update_state()

    def set_config(
        self,
        *,
        shm_name: str,
        video_transport: object,
        video_key: str,
        throttle_ms: int,
        flow_transport: object,
        flow_key: str,
        flow_shm_name: str,
        flow_display_mode: str,
        flow_mag_scale: float,
        flow_stride: int,
        scalar_transport: object,
        scalar_key: str,
        scalar_shm_name: str,
        scalar_display_mode: str,
        scalar_colormap: str,
        scalar_range_mode: str,
        scalar_min: float,
        scalar_max: float,
        scalar_auto_percentile_lo: float,
        scalar_auto_percentile_hi: float,
        scalar_invert: bool,
        scalar_nan_mode: str,
        scale_mode: str,
    ) -> None:
        next_video = str(shm_name or "").strip()
        next_video_key = str(video_key or "").strip()
        next_video_transport = _normalize_frame_transport(
            video_transport,
            zenoh_key=next_video_key,
            shm_name=next_video,
        )
        next_flow_key = str(flow_key or "").strip()
        next_flow = str(flow_shm_name or "").strip()
        next_flow_transport = _normalize_frame_transport(
            flow_transport,
            zenoh_key=next_flow_key,
            shm_name=next_flow,
        )
        next_scalar_key = str(scalar_key or "").strip()
        next_scalar = str(scalar_shm_name or "").strip()
        next_scalar_transport = _normalize_frame_transport(
            scalar_transport,
            zenoh_key=next_scalar_key,
            shm_name=next_scalar,
        )
        next_mode = str(flow_display_mode or "off").strip().lower()
        if next_mode not in ("off", "hsv", "arrows"):
            next_mode = "off"
        next_scalar_mode = str(scalar_display_mode or "off").strip().lower()
        if next_scalar_mode not in ("off", "colormap"):
            next_scalar_mode = "off"
        next_scalar_colormap = str(scalar_colormap or "turbo").strip().lower()
        if next_scalar_colormap not in ("gray", "turbo", "viridis", "magma"):
            next_scalar_colormap = "turbo"
        next_scalar_range = str(scalar_range_mode or "auto").strip().lower()
        if next_scalar_range not in ("auto", "manual"):
            next_scalar_range = "auto"
        next_scalar_nan_mode = str(scalar_nan_mode or "transparent").strip().lower()
        if next_scalar_nan_mode not in ("transparent", "zero", "min", "max"):
            next_scalar_nan_mode = "transparent"

        if (
            next_video != self._video_shm_name
            or next_video_transport != self._video_transport
            or next_video_key != self._video_key
        ):
            self._video_shm_name = next_video
            self._video_transport = next_video_transport
            self._video_key = next_video_key
            self._reset_video_reader()
        if (
            next_flow != self._flow_shm_name
            or next_flow_transport != self._flow_transport
            or next_flow_key != self._flow_key
        ):
            self._flow_shm_name = next_flow
            self._flow_transport = next_flow_transport
            self._flow_key = next_flow_key
            self._reset_flow_reader()
        if (
            next_scalar != self._scalar_shm_name
            or next_scalar_transport != self._scalar_transport
            or next_scalar_key != self._scalar_key
        ):
            self._scalar_shm_name = next_scalar
            self._scalar_transport = next_scalar_transport
            self._scalar_key = next_scalar_key
            self._reset_scalar_reader()
        self._flow_display_mode = next_mode
        self._flow_mag_scale = max(0.1, float(flow_mag_scale))
        self._flow_stride = max(2, int(flow_stride))
        self._scalar_display_mode = next_scalar_mode
        self._scalar_colormap = next_scalar_colormap
        self._scalar_range_mode = next_scalar_range
        self._scalar_min = float(scalar_min)
        self._scalar_max = float(scalar_max)
        self._scalar_auto_percentile_lo = float(max(0.0, min(100.0, float(scalar_auto_percentile_lo))))
        self._scalar_auto_percentile_hi = float(max(0.0, min(100.0, float(scalar_auto_percentile_hi))))
        self._scalar_invert = bool(scalar_invert)
        self._scalar_nan_mode = next_scalar_nan_mode
        next_scale_mode = str(scale_mode or "fit").strip().lower()
        self._scale_mode = next_scale_mode if next_scale_mode in ("native", "fit") else "fit"
        self._timer.setInterval(max(1, int(throttle_ms)))
        self._sync_timer_with_update_state()

    def detach(self) -> None:
        try:
            self._timer.stop()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._reset_video_reader()
        self._reset_flow_reader()
        self._reset_scalar_reader()

    def _reset_video_reader(self) -> None:
        try:
            if self._video_reader is not None:
                self._video_reader.close()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._video_reader = None
        self._last_video_frame_id = 0
        self._latest_video = None

    def _reset_flow_reader(self) -> None:
        try:
            if self._flow_reader is not None:
                self._flow_reader.close()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._flow_reader = None
        self._last_flow_frame_id = 0

    def _reset_scalar_reader(self) -> None:
        try:
            if self._scalar_reader is not None:
                self._scalar_reader.close()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._scalar_reader = None

    def _sync_timer_with_update_state(self) -> None:
        has_video_source = (
            bool(self._video_key) if self._video_transport == _TRANSPORT_ZENOH else bool(self._video_shm_name)
        )
        has_flow_source = (
            bool(self._flow_key) if self._flow_transport == _TRANSPORT_ZENOH else bool(self._flow_shm_name)
        )
        has_scalar_source = (
            bool(self._scalar_key) if self._scalar_transport == _TRANSPORT_ZENOH else bool(self._scalar_shm_name)
        )
        has_source = has_video_source or has_flow_source or has_scalar_source
        if self.update_enabled() and has_source:
            if not self._timer.isActive():
                self._timer.start()
            return
        if self._timer.isActive():
            self._timer.stop()

    def _ensure_video_reader(self) -> bool:
        if self._video_reader is not None:
            return True
        if self._video_transport == _TRANSPORT_ZENOH:
            if not self._video_key:
                return False
            try:
                self._video_reader = ZenohLatestVideoFrameTransport.open_subscriber(self._video_key)
                return True
            except (OSError, RuntimeError, ValueError):
                self._video_reader = None
                return False
        if not self._video_shm_name:
            return False
        try:
            self._video_reader = LegacyShmLatestVideoFrameTransport.open_reader(self._video_shm_name, use_event=False)
            return True
        except (FileNotFoundError, OSError, RuntimeError):
            self._video_reader = None
            return False

    def _ensure_flow_reader(self) -> bool:
        if self._flow_reader is not None:
            return True
        if self._flow_transport == _TRANSPORT_ZENOH:
            if not self._flow_key:
                return False
            try:
                self._flow_reader = ZenohLatestVideoFrameTransport.open_subscriber(self._flow_key)
                return True
            except (OSError, RuntimeError, ValueError):
                self._flow_reader = None
                return False
        if not self._flow_shm_name:
            return False
        try:
            self._flow_reader = LegacyShmLatestVideoFrameTransport.open_reader(self._flow_shm_name, use_event=False)
            return True
        except (FileNotFoundError, OSError, RuntimeError):
            self._flow_reader = None
            return False

    def _ensure_scalar_reader(self) -> bool:
        if self._scalar_reader is not None:
            return True
        if self._scalar_transport == _TRANSPORT_ZENOH:
            if not self._scalar_key:
                return False
            try:
                self._scalar_reader = ZenohLatestVideoFrameTransport.open_subscriber(self._scalar_key)
                return True
            except (OSError, RuntimeError, ValueError):
                self._scalar_reader = None
                return False
        if not self._scalar_shm_name:
            return False
        try:
            self._scalar_reader = LegacyShmLatestVideoFrameTransport.open_reader(self._scalar_shm_name, use_event=False)
            return True
        except (FileNotFoundError, OSError, RuntimeError):
            self._scalar_reader = None
            return False

    def _update_video_cache(self) -> None:
        if not self._ensure_video_reader() or self._video_reader is None:
            return
        try:
            frame = self._video_reader.poll_latest()
        except (BufferError, OSError, RuntimeError, ValueError):
            return
        if frame is None:
            return
        try:
            if int(frame.fmt) != VIDEO_FORMAT_BGRA32:
                return
            frame_id = int(frame.frame_id)
            if frame_id == self._last_video_frame_id:
                return
            w = int(frame.width)
            h = int(frame.height)
            pitch = int(frame.pitch)
            if w <= 0 or h <= 0 or pitch <= 0:
                return
            frame_bytes = frame.payload_bytes()
            try:
                img = QtGui.QImage(frame_bytes, w, h, pitch, QtGui.QImage.Format_ARGB32)
                self._latest_video = img.copy()
                self._last_video_frame_id = frame_id
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return
        finally:
            frame.release()

    def _render_hsv_flow(self, payload: memoryview, width: int, height: int, pitch: int) -> QtGui.QImage | None:
        row_bytes = width * 4
        if row_bytes <= 0 or pitch < row_bytes:
            return None
        flow_rows = []
        for y in range(height):
            off = y * pitch
            flow_rows.append(bytes(payload[off : off + row_bytes]))
        flow_bytes = b"".join(flow_rows)
        uv = np.frombuffer(flow_bytes, dtype="<f2").reshape(height, width, 2).astype(np.float32)
        u = uv[:, :, 0]
        v = uv[:, :, 1]
        mag = np.sqrt(u * u + v * v)
        angle = np.arctan2(v, u)
        hue = ((angle + np.pi) / (2.0 * np.pi)).astype(np.float32)
        sat = np.ones_like(hue, dtype=np.float32)
        val = np.clip(mag / float(self._flow_mag_scale), 0.0, 1.0).astype(np.float32)
        rgb = _hsv_to_rgb_u8(hue, sat, val)
        try:
            img = QtGui.QImage(rgb.data, width, height, width * 3, QtGui.QImage.Format_RGB888)
            return img.copy()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _render_arrows_flow(self, payload: memoryview, width: int, height: int, pitch: int) -> QtGui.QImage | None:
        if self._latest_video is not None and self._latest_video.width() == width and self._latest_video.height() == height:
            canvas = self._latest_video.copy()
        else:
            canvas = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
            canvas.fill(QtGui.QColor(16, 16, 16))

        row_bytes = width * 4
        if row_bytes <= 0 or pitch < row_bytes:
            return canvas
        flow_rows = []
        for y in range(height):
            off = y * pitch
            flow_rows.append(bytes(payload[off : off + row_bytes]))
        flow_bytes = b"".join(flow_rows)
        uv = np.frombuffer(flow_bytes, dtype="<f2").reshape(height, width, 2).astype(np.float32)

        p = QtGui.QPainter(canvas)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            stride = max(2, int(self._flow_stride))
            scale = 20.0 / float(self._flow_mag_scale)
            for y in range(stride // 2, height, stride):
                for x in range(stride // 2, width, stride):
                    dx = float(uv[y, x, 0])
                    dy = float(uv[y, x, 1])
                    mag = float(np.hypot(dx, dy))
                    if mag <= 0.01:
                        continue
                    v01 = max(0.0, min(1.0, mag / float(self._flow_mag_scale)))
                    rr = int(40 + 215 * v01)
                    gg = int(220 - 120 * v01)
                    bb = int(220 - 200 * v01)
                    pen = QtGui.QPen(QtGui.QColor(rr, gg, bb, 190))
                    pen.setWidthF(1.2)
                    p.setPen(pen)
                    x2 = float(x) + dx * scale
                    y2 = float(y) + dy * scale
                    p.drawLine(QtCore.QPointF(float(x), float(y)), QtCore.QPointF(x2, y2))
        finally:
            p.end()
        return canvas

    def _render_scalar_colormap(self, payload: memoryview, width: int, height: int, pitch: int) -> QtGui.QImage | None:
        row_bytes = width * 4
        if row_bytes <= 0 or pitch < row_bytes:
            return None
        scalar_rows: list[bytes] = []
        for y in range(height):
            offset = y * pitch
            scalar_rows.append(bytes(payload[offset : offset + row_bytes]))
        scalar_bytes = b"".join(scalar_rows)
        scalar_values = np.frombuffer(scalar_bytes, dtype="<f4").reshape(height, width)
        normalized, alpha = _normalize_scalar_values(
            scalar_values,
            range_mode=self._scalar_range_mode,
            manual_min=self._scalar_min,
            manual_max=self._scalar_max,
            auto_percentile_lo=self._scalar_auto_percentile_lo,
            auto_percentile_hi=self._scalar_auto_percentile_hi,
            invert=self._scalar_invert,
            nan_mode=self._scalar_nan_mode,
        )
        rgb = _apply_colormap(normalized, colormap=self._scalar_colormap)
        bgra = np.empty((height, width, 4), dtype=np.uint8)
        bgra[:, :, 0] = rgb[:, :, 2]
        bgra[:, :, 1] = rgb[:, :, 1]
        bgra[:, :, 2] = rgb[:, :, 0]
        bgra[:, :, 3] = alpha
        try:
            img = QtGui.QImage(bgra.data, width, height, width * 4, QtGui.QImage.Format_ARGB32)
            return img.copy()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _try_render_flow(self) -> QtGui.QImage | None:
        if self._flow_display_mode == "off":
            return None
        if not self._ensure_flow_reader() or self._flow_reader is None:
            return None
        try:
            frame = self._flow_reader.poll_latest()
        except (BufferError, OSError, RuntimeError, ValueError):
            return None
        if frame is None:
            return None
        try:
            if int(frame.fmt) != VIDEO_FORMAT_FLOW2_F16:
                return None
            frame_id = int(frame.frame_id)
            if frame_id == self._last_flow_frame_id:
                return None
            self._last_flow_frame_id = frame_id

            width = int(frame.width)
            height = int(frame.height)
            pitch = int(frame.pitch)
            if width <= 0 or height <= 0 or pitch <= 0:
                return None
            if self._flow_display_mode == "hsv":
                return self._render_hsv_flow(frame.payload, width, height, pitch)
            return self._render_arrows_flow(frame.payload, width, height, pitch)
        finally:
            frame.release()

    def _try_render_scalar(self) -> QtGui.QImage | None:
        if self._scalar_display_mode == "off":
            return None
        if not self._ensure_scalar_reader() or self._scalar_reader is None:
            return None
        try:
            frame = self._scalar_reader.poll_latest()
        except (BufferError, OSError, RuntimeError, ValueError):
            return None
        if frame is None:
            return None
        try:
            if int(frame.fmt) != VIDEO_FORMAT_SCALAR1_F32:
                return None

            width = int(frame.width)
            height = int(frame.height)
            pitch = int(frame.pitch)
            if width <= 0 or height <= 0 or pitch <= 0:
                return None
            return self._render_scalar_colormap(frame.payload, width, height, pitch)
        finally:
            frame.release()

    def _present(self, image: QtGui.QImage) -> None:
        pix = QtGui.QPixmap.fromImage(image)
        if self._scale_mode == "fit":
            target = self._image.size()
            if self._last_pixmap_size != target:
                self._last_pixmap_size = target
            try:
                pix = pix.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self._image.setPixmap(pix)

    def _tick(self) -> None:
        if not self.update_enabled():
            return
        self._update_video_cache()
        flow_image = self._try_render_flow()
        if flow_image is not None:
            self._present(flow_image)
            return
        scalar_image = self._try_render_scalar()
        if scalar_image is not None:
            self._present(scalar_image)
            return
        if self._latest_video is not None:
            self._present(self._latest_video)


class _LatestVideoWidget(NodeBaseWidget):
    def __init__(
        self,
        parent=None,
        name: str = _WIDGET_NAME,
        label: str = "",
        *,
        on_update_toggled: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(parent=parent, name=name, label=label)
        self._pane = _LatestVideoPane()
        self.set_custom_widget(self._pane)
        self._block = False
        self._on_update_toggled_cb = on_update_toggled
        self._pane.update_checkbox().toggled.connect(self.on_value_changed)  # type: ignore[attr-defined]
        self._pane.update_checkbox().toggled.connect(self._on_update_toggled)

    def get_value(self) -> object:
        return {"update": bool(self._pane.update_enabled())}

    def set_value(self, value: object) -> None:
        _ = value

    def set_update_enabled(self, enabled: bool) -> None:
        try:
            self._block = True
            self._pane.set_update_enabled(enabled)
        finally:
            self._block = False

    def on_value_changed(self, *args, **kwargs):
        if self._block:
            return
        return super().on_value_changed(*args, **kwargs)

    def _on_update_toggled(self, enabled: bool) -> None:
        if self._block:
            return
        cb = self._on_update_toggled_cb
        if cb is None:
            return
        cb(bool(enabled))

    def set_config(
        self,
        *,
        shm_name: str,
        video_transport: object,
        video_key: str,
        throttle_ms: int,
        flow_transport: object,
        flow_key: str,
        flow_shm_name: str,
        flow_display_mode: str,
        flow_mag_scale: float,
        flow_stride: int,
        scalar_transport: object,
        scalar_key: str,
        scalar_shm_name: str,
        scalar_display_mode: str,
        scalar_colormap: str,
        scalar_range_mode: str,
        scalar_min: float,
        scalar_max: float,
        scalar_auto_percentile_lo: float,
        scalar_auto_percentile_hi: float,
        scalar_invert: bool,
        scalar_nan_mode: str,
        scale_mode: str,
    ) -> None:
        self._pane.set_config(
            shm_name=shm_name,
            video_transport=video_transport,
            video_key=video_key,
            throttle_ms=throttle_ms,
            flow_transport=flow_transport,
            flow_key=flow_key,
            flow_shm_name=flow_shm_name,
            flow_display_mode=flow_display_mode,
            flow_mag_scale=flow_mag_scale,
            flow_stride=flow_stride,
            scalar_transport=scalar_transport,
            scalar_key=scalar_key,
            scalar_shm_name=scalar_shm_name,
            scalar_display_mode=scalar_display_mode,
            scalar_colormap=scalar_colormap,
            scalar_range_mode=scalar_range_mode,
            scalar_min=scalar_min,
            scalar_max=scalar_max,
            scalar_auto_percentile_lo=scalar_auto_percentile_lo,
            scalar_auto_percentile_hi=scalar_auto_percentile_hi,
            scalar_invert=scalar_invert,
            scalar_nan_mode=scalar_nan_mode,
            scale_mode=scale_mode,
        )

    def detach(self) -> None:
        self._pane.detach()


class VizVideoRenderNode(F8StudioOperatorBaseNode):
    def __init__(self):
        super().__init__(qgraphics_item=F8StudioVizOperatorNodeItem)
        self.add_ephemeral_widget(
            _LatestVideoWidget(
                self.view,
                name=_WIDGET_NAME,
                label="",
                on_update_toggled=self._on_update_toggled,
            )
        )
        self._sync_update_checkbox_from_state(default=True)

    def sync_from_spec(self) -> None:
        super().sync_from_spec()
        self._sync_update_checkbox_from_state(default=True)

    def set_property(self, name, value, push_undo=True):  # type: ignore[override]
        super().set_property(name, value, push_undo=push_undo)
        if str(name or "").strip() == _STATE_UI_UPDATE:
            self._sync_update_checkbox_from_state(default=bool(value))

    def _on_update_toggled(self, enabled: bool) -> None:
        self.set_state_bool(_STATE_UI_UPDATE, bool(enabled))

    def _sync_update_checkbox_from_state(self, *, default: bool) -> None:
        self.sync_bool_state_to_widget(
            state_name=_STATE_UI_UPDATE,
            default=default,
            widget_name=_WIDGET_NAME,
            widget_type=_LatestVideoWidget,
            apply_value=_LatestVideoWidget.set_update_enabled,
        )

    def _widget(self) -> _LatestVideoWidget | None:
        return self.widget_by_name(_WIDGET_NAME, _LatestVideoWidget)

    def apply_ui_command(self, cmd: UiCommand) -> None:
        c = str(cmd.command or "")
        if c == "viz.video.detach":
            widget = self._widget()
            if widget is not None:
                widget.detach()
            return

        if c != "viz.video.set":
            return
        try:
            payload = dict(cmd.payload or {})
            shm_name = str(payload.get("shmName") or "").strip()
            video_transport = payload.get("videoTransport")
            video_key = str(payload.get("videoKey") or "").strip()
            throttle_ms = int(payload.get("throttleMs") or 33)
            flow_transport = payload.get("flowTransport")
            flow_key = str(payload.get("flowKey") or "").strip()
            flow_shm_name = str(payload.get("flowShmName") or "").strip()
            flow_display_mode = str(payload.get("flowDisplayMode") or "off").strip().lower()
            flow_mag_scale = float(payload.get("flowMagScale") or 20.0)
            flow_stride = int(payload.get("flowStride") or 12)
            scalar_transport = payload.get("scalarTransport")
            scalar_key = str(payload.get("scalarKey") or "").strip()
            scalar_shm_name = str(payload.get("scalarShmName") or "").strip()
            scalar_display_mode = str(payload.get("scalarDisplayMode") or "off").strip().lower()
            scalar_colormap = str(payload.get("scalarColormap") or "turbo").strip().lower()
            scalar_range_mode = str(payload.get("scalarRangeMode") or "auto").strip().lower()
            scalar_min = float(payload.get("scalarMin") or -1.0)
            scalar_max = float(payload.get("scalarMax") or 1.0)
            scalar_auto_percentile_lo = float(payload.get("scalarAutoPercentileLo") or 2.0)
            scalar_auto_percentile_hi = float(payload.get("scalarAutoPercentileHi") or 98.0)
            scalar_invert = _to_bool(payload.get("scalarInvert"), default=False)
            scalar_nan_mode = str(payload.get("scalarNanMode") or "transparent").strip().lower()
            scale_mode = str(payload.get("scaleMode") or "fit").strip().lower()
        except (AttributeError, TypeError, ValueError):
            return
        widget = self._widget()
        if widget is None:
            return
        widget.set_config(
            shm_name=shm_name,
            video_transport=video_transport,
            video_key=video_key,
            throttle_ms=throttle_ms,
            flow_transport=flow_transport,
            flow_key=flow_key,
            flow_shm_name=flow_shm_name,
            flow_display_mode=flow_display_mode,
            flow_mag_scale=flow_mag_scale,
            flow_stride=flow_stride,
            scalar_transport=scalar_transport,
            scalar_key=scalar_key,
            scalar_shm_name=scalar_shm_name,
            scalar_display_mode=scalar_display_mode,
            scalar_colormap=scalar_colormap,
            scalar_range_mode=scalar_range_mode,
            scalar_min=scalar_min,
            scalar_max=scalar_max,
            scalar_auto_percentile_lo=scalar_auto_percentile_lo,
            scalar_auto_percentile_hi=scalar_auto_percentile_hi,
            scalar_invert=scalar_invert,
            scalar_nan_mode=scalar_nan_mode,
            scale_mode=scale_mode,
        )
