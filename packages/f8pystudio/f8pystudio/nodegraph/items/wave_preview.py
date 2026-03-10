from __future__ import annotations

import math
from typing import Any, Callable

from qtpy import QtCore, QtWidgets

try:
    import pyqtgraph as pg  # type: ignore[import-not-found]
except ModuleNotFoundError:
    pg = None  # type: ignore[assignment]


WAVE_PREVIEW_DEPENDENCY_FIELDS = frozenset({"minValue", "maxValue", "maxT"})


class WavePreviewControl(QtWidgets.QWidget):
    def __init__(self, *, field_tooltip: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._plot_widget: Any = None
        self._curve_item: Any = None
        if pg is not None:
            plot_widget = pg.PlotWidget(self)
            plot_widget.setBackground((16, 16, 16))
            plot_widget.hideAxis("bottom")
            plot_widget.hideAxis("left")
            self._curve_item = plot_widget.plot([], [], pen=pg.mkPen((120, 210, 255), width=2))
            plot_widget.setMinimumWidth(0)
            plot_widget.setMinimumHeight(40)
            plot_widget.setMaximumHeight(50)
            plot_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self._plot_widget = plot_widget
            root.addWidget(plot_widget)
        else:
            spacer = QtWidgets.QWidget(self)
            spacer.setMinimumHeight(40)
            spacer.setMaximumHeight(50)
            spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            root.addWidget(spacer)

        self.setMinimumWidth(0)
        self.setMaximumHeight(80)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setObjectName("inline_wave_preview")
        if field_tooltip:
            self.setToolTip(field_tooltip)

    def set_preview_data(
        self,
        *,
        preview_cycle: Any,
        min_value: Any,
        max_value: Any,
        max_t: Any,
    ) -> None:
        points = self._normalize_preview_cycle(preview_cycle)
        if pg is None or self._plot_widget is None or self._curve_item is None:
            return

        if not points:
            self._curve_item.setData([], [])
            self._plot_widget.update()
            self.update()
            return

        x_max = self._coerce_positive_preview_x(max_t)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        self._curve_item.setData(xs, ys)

        self._plot_widget.setXRange(0.0, x_max, padding=0.0)
        y_range = self._coerce_preview_y_range(min_value, max_value)
        if y_range is None:
            self._plot_widget.enableAutoRange(axis="y", enable=True)
        else:
            self._plot_widget.enableAutoRange(axis="y", enable=False)
            self._plot_widget.setYRange(y_range[0], y_range[1], padding=0.0)

        self._plot_widget.update()
        self.update()

    @staticmethod
    def _normalize_preview_cycle(raw_value: Any) -> list[tuple[float, float]]:
        if not isinstance(raw_value, list):
            return []

        points: list[tuple[float, float]] = []
        for item in raw_value:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                x_value = float(item[0])
                y_value = float(item[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(x_value) and math.isfinite(y_value):
                points.append((x_value, y_value))
        return points

    @staticmethod
    def _coerce_preview_y_range(min_value: Any, max_value: Any) -> tuple[float, float] | None:
        if isinstance(min_value, bool) or isinstance(max_value, bool):
            return None
        try:
            low = float(min_value)
            high = float(max_value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(low) or not math.isfinite(high) or low >= high:
            return None
        return low, high

    @staticmethod
    def _coerce_positive_preview_x(raw_value: Any) -> float:
        if isinstance(raw_value, bool):
            return 1.0
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(value) or value <= 0.0:
            return 1.0
        return value


def make_wave_preview_control(
    *,
    field_tooltip: str,
    preview_value_getter: Callable[[], Any],
    property_value_getter: Callable[[str], Any],
) -> tuple[WavePreviewControl, Callable[[Any], None]]:
    control = WavePreviewControl(field_tooltip=field_tooltip)

    def apply_preview(value: Any) -> None:
        control.set_preview_data(
            preview_cycle=value,
            min_value=property_value_getter("minValue"),
            max_value=property_value_getter("maxValue"),
            max_t=property_value_getter("maxT"),
        )

    apply_preview(preview_value_getter())
    return control, apply_preview
