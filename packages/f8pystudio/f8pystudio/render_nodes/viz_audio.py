from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np
import pyqtgraph as pg  # type: ignore[import-not-found]
from qtpy import QtCore, QtWidgets
from NodeGraphQt.nodes.base_node import NodeBaseWidget

from f8pysdk.audio_transport import (
    LatestAudioChunkTransport,
    SAMPLE_FORMAT_F32LE,
    ZenohLatestAudioChunkTransport,
)

from ..nodegraph.operator_basenode import F8StudioOperatorBaseNode
from ..nodegraph.viz_operator_nodeitem import F8StudioVizOperatorNodeItem
from f8pystudio.contracts.ui_commands import UiCommand

_STATE_UI_UPDATE = "uiUpdate"
_WIDGET_NAME = "__audio_latest"


class _LatestAudioPane(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

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

        self._plot = pg.PlotWidget(self)
        self._plot.setBackground((20, 20, 20))
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        # Compact viz-style: no axis captions or tick labels.
        try:
            axb = self._plot.getAxis("bottom")
            axl = self._plot.getAxis("left")
            axb.setLabel("")
            axl.setLabel("")
            axb.setStyle(showValues=False)
            axl.setStyle(showValues=False)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            pi = self._plot.getPlotItem()
            if pi is not None:
                try:
                    pi.layout.setContentsMargins(2, 2, 2, 2)
                except (AttributeError, RuntimeError, TypeError):
                    pass
                try:
                    pi.setDefaultPadding(0.02)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._curve = self._plot.plot([], [], pen=pg.mkPen((120, 200, 255), width=1))

        # self._status = QtWidgets.QLabel("")
        # self._status.setStyleSheet("color: rgb(160, 160, 160);")

        layout.addLayout(top)
        layout.addWidget(self._plot, 1)
        # layout.addWidget(self._status)

        # Node label + state fields already provide context; keep pane compact by default.
        # self._title.setVisible(False)
        # self._status.setVisible(False)
        self.setMinimumWidth(200)
        self.setMinimumHeight(120)
        self.setMaximumWidth(200)
        self.setMaximumHeight(150)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)  # type: ignore[attr-defined]
        self._timer.setInterval(20)

        self._reader: LatestAudioChunkTransport | None = None
        self._audio_stream_key = ""
        self._last_seq = 0

        self._history_ms = 250
        self._channel = 0
        self._sample_rate = 48000
        self._window_frames = 0
        self._x = np.zeros((0,), dtype=np.float32)
        self._y = np.zeros((0,), dtype=np.float32)

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
        audio_stream_key: str,
        throttle_ms: int,
        history_ms: int,
        channel: int,
    ) -> None:
        audio_stream_key = str(audio_stream_key or "").strip()
        self._history_ms = max(20, int(history_ms))
        self._channel = max(0, int(channel))
        throttle_ms = max(0, int(throttle_ms))
        self._timer.setInterval(max(1, throttle_ms) if throttle_ms > 0 else 1)
        if audio_stream_key != self._audio_stream_key:
            self._audio_stream_key = audio_stream_key
            self._reset_reader()
        self._sync_timer_with_update_state()

    def detach(self) -> None:
        try:
            self._timer.stop()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._reset_reader()

    def _reset_reader(self) -> None:
        try:
            if self._reader is not None:
                self._reader.close()
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._reader = None
        self._last_seq = 0

    def _sync_timer_with_update_state(self) -> None:
        if self.update_enabled() and self._audio_stream_key:
            if not self._timer.isActive():
                self._timer.start()
            return
        if self._timer.isActive():
            self._timer.stop()

    def _ensure_reader(self) -> bool:
        if self._reader is not None:
            return True
        try:
            if not self._audio_stream_key:
                return False
            r: LatestAudioChunkTransport = ZenohLatestAudioChunkTransport.open_subscriber(self._audio_stream_key)
            self._reader = r
            return True
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            del exc
            self._reader = None
            return False

    def _rebuild_window(self, sample_rate: int) -> None:
        self._sample_rate = int(sample_rate)
        self._window_frames = max(1, int(math.ceil(float(self._sample_rate) * float(self._history_ms) / 1000.0)))
        self._x = np.linspace(-float(self._history_ms) / 1000.0, 0.0, self._window_frames, dtype=np.float32)
        self._y = np.zeros((self._window_frames,), dtype=np.float32)
        self._curve.setData(self._x, self._y)

    def _tick(self) -> None:
        if not self.update_enabled():
            return
        if not self._ensure_reader():
            return
        assert self._reader is not None
        chunk = self._reader.poll_latest()
        if chunk is None:
            return
        try:
            if int(chunk.fmt) != int(SAMPLE_FORMAT_F32LE):
                return
            if chunk.sample_rate <= 0 or chunk.channels <= 0:
                return
            if int(chunk.sample_rate) != int(self._sample_rate) or self._window_frames == 0:
                self._rebuild_window(int(chunk.sample_rate))

            seq = int(chunk.seq)
            if seq <= 0 or seq == int(self._last_seq):
                return

            frames = int(chunk.frames)
            if frames <= 0:
                self._last_seq = seq
                return

            channels = int(chunk.channels)
            samples = np.frombuffer(chunk.payload, dtype=np.float32).copy()
            try:
                samples = samples.reshape((frames, channels))
            except (TypeError, ValueError):
                self._last_seq = seq
                return
            idx = min(max(0, int(self._channel)), max(0, channels - 1))
            y = samples[:, idx]

            if frames >= self._window_frames:
                self._y[:] = y[-self._window_frames :]
            else:
                self._y[:-frames] = self._y[frames:]
                self._y[-frames:] = y[:frames]

            self._curve.setData(self._x, self._y)
            self._last_seq = seq
        finally:
            chunk.release()


class _LatestAudioWidget(NodeBaseWidget):
    def __init__(
        self,
        parent=None,
        name: str = _WIDGET_NAME,
        label: str = "",
        *,
        on_update_toggled: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(parent=parent, name=name, label=label)
        self._pane = _LatestAudioPane()
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
        audio_stream_key: str,
        throttle_ms: int,
        history_ms: int,
        channel: int,
    ) -> None:
        self._pane.set_config(
            audio_stream_key=audio_stream_key,
            throttle_ms=throttle_ms,
            history_ms=history_ms,
            channel=channel,
        )

    def detach(self) -> None:
        self._pane.detach()


class VizAudioRenderNode(F8StudioOperatorBaseNode):
    """
    Render node for `f8.viz.audio`.
    """

    def __init__(self):
        super().__init__(qgraphics_item=F8StudioVizOperatorNodeItem)
        self.add_ephemeral_widget(
            _LatestAudioWidget(
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
            widget_type=_LatestAudioWidget,
            apply_value=_LatestAudioWidget.set_update_enabled,
        )

    def _widget(self) -> _LatestAudioWidget | None:
        return self.widget_by_name(_WIDGET_NAME, _LatestAudioWidget)

    def apply_ui_command(self, cmd: UiCommand) -> None:
        c = str(cmd.command or "")
        if c == "viz.audio.detach":
            widget = self._widget()
            if widget is not None:
                widget.detach()
            return
        if c != "viz.audio.set":
            return
        try:
            payload = dict(cmd.payload or {})
            audio_stream_key = str(payload.get("audioStreamKey") or "").strip()
            throttle_ms = int(payload.get("throttleMs") or 20)
            history_ms = int(payload.get("historyMs") or 250)
            channel = int(payload.get("channel") or 0)
        except (AttributeError, TypeError, ValueError):
            return
        widget = self._widget()
        if widget is None:
            return
        widget.set_config(
            audio_stream_key=audio_stream_key,
            throttle_ms=throttle_ms,
            history_ms=history_ms,
            channel=channel,
        )
