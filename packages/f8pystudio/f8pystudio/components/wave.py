from __future__ import annotations

import math
from typing import Any, Callable

from qtpy import QtCore, QtGui, QtWidgets

try:
    import pyqtgraph as pg  # type: ignore[import-not-found]
except ModuleNotFoundError:
    pg = None  # type: ignore[assignment]


WAVE_PREVIEW_DEPENDENCY_FIELDS = frozenset({"minValue", "maxValue", "maxT"})
WAVE_PATTERN_EDITOR_DEPENDENCY_FIELDS = frozenset({"minValue", "maxValue", "maxT", "preview"})
_EDITOR_T_EPSILON = 1e-6


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
        points = normalize_preview_cycle(preview_cycle)
        if pg is None or self._plot_widget is None or self._curve_item is None:
            return

        if not points:
            self._curve_item.setData([], [])
            self._plot_widget.update()
            self.update()
            return

        x_max = coerce_positive_preview_x(max_t)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        self._curve_item.setData(xs, ys)

        self._plot_widget.setXRange(0.0, x_max, padding=0.0)
        y_range = coerce_preview_y_range(min_value, max_value)
        if y_range is None:
            self._plot_widget.enableAutoRange(axis="y", enable=True)
        else:
            self._plot_widget.enableAutoRange(axis="y", enable=False)
            self._plot_widget.setYRange(y_range[0], y_range[1], padding=0.0)

        self._plot_widget.update()
        self.update()

    _normalize_preview_cycle = staticmethod(lambda raw_value: normalize_preview_cycle(raw_value))
    _coerce_preview_y_range = staticmethod(lambda min_value, max_value: coerce_preview_y_range(min_value, max_value))
    _coerce_positive_preview_x = staticmethod(lambda raw_value: coerce_positive_preview_x(raw_value))


class WavePatternEditorControl(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        points_setter: Callable[[Any, bool], None],
        field_tooltip: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._points_setter = points_setter
        self._points: list[tuple[float, float]] = []
        self._preview_cycle: list[tuple[float, float]] = []
        self._max_t = 1.0
        self._min_value: float | None = 0.0
        self._max_value: float | None = 1.0
        self._selected_index: int | None = None
        self._drag_index: int | None = None
        self._read_only = False

        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.PreventContextMenu)
        self.setMouseTracking(True)
        self.setMinimumHeight(72)
        self.setMaximumHeight(92)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setObjectName("inline_wave_pattern_editor")
        if field_tooltip:
            self.setToolTip(field_tooltip)

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = bool(read_only)
        self.update()

    def set_editor_data(
        self,
        *,
        points: Any,
        preview_cycle: Any,
        min_value: Any,
        max_value: Any,
        max_t: Any,
    ) -> None:
        next_max_t = coerce_positive_preview_x(max_t)
        next_points = normalize_control_points(points, max_t=next_max_t)
        self._max_t = next_max_t
        self._points = next_points
        self._preview_cycle = normalize_preview_cycle(preview_cycle)
        self._min_value, self._max_value = effective_requested_y_range(min_value, max_value)
        if self._selected_index is not None and self._selected_index >= len(self._points):
            self._selected_index = None
        if self._drag_index is not None and self._drag_index >= len(self._points):
            self._drag_index = None
        self.update()

    def _visible_points_with_indices(self) -> list[tuple[int, tuple[float, float]]]:
        visible: list[tuple[int, tuple[float, float]]] = []
        for index, point in enumerate(self._points):
            t_value = point[0]
            if 0.0 <= t_value <= self._max_t:
                visible.append((index, point))
        return visible

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if not self._read_only and event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            if self._delete_selected_point(push_undo=True):
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        hit_index = self._find_hit_index(event.position())
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            if not self._read_only and hit_index is not None:
                self._selected_index = hit_index
                self._delete_selected_point(push_undo=True)
                self.update()
            event.accept()
            return

        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        if hit_index is not None:
            self._selected_index = hit_index
            if not self._read_only:
                self._drag_index = hit_index
            self.update()
            event.accept()
            return

        if self._read_only:
            super().mousePressEvent(event)
            return

        inserted_index = self._insert_point_at(event.position())
        self._selected_index = inserted_index
        self._drag_index = inserted_index
        self._commit_points(push_undo=True)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._read_only or self._drag_index is None:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        self._move_dragged_point(event.position())
        self._commit_points(push_undo=False)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_index is not None:
            self._move_dragged_point(event.position())
            self._drag_index = None
            self._commit_points(push_undo=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor(14, 14, 14, 200))
        graph_rect = graph_draw_rect(rect)
        painter.fillRect(graph_rect, QtGui.QColor(20, 20, 20, 220))
        painter.setPen(QtGui.QPen(QtGui.QColor(65, 65, 65), 1.0))
        painter.drawRoundedRect(graph_rect, 4.0, 4.0)

        y_range = self._effective_y_range()
        if y_range is not None:
            zero_y = point_to_widget_pos(0.0, 0.0, rect=graph_rect, max_t=self._max_t, y_range=y_range).y()
            if graph_rect.top() <= zero_y <= graph_rect.bottom():
                painter.setPen(QtGui.QPen(QtGui.QColor(55, 55, 55), 1.0, QtCore.Qt.PenStyle.DashLine))
                painter.drawLine(
                    QtCore.QPointF(graph_rect.left(), zero_y),
                    QtCore.QPointF(graph_rect.right(), zero_y),
                )

        self._draw_curve(painter, graph_rect, y_range)
        self._draw_points(painter, graph_rect, y_range)

        if self._read_only:
            painter.fillRect(rect, QtGui.QColor(0, 0, 0, 35))

    def _draw_curve(self, painter: QtGui.QPainter, rect: QtCore.QRectF, y_range: tuple[float, float] | None) -> None:
        if not self._preview_cycle or y_range is None:
            return
        path = QtGui.QPainterPath()
        first_pos = point_to_widget_pos(
            self._preview_cycle[0][0],
            self._preview_cycle[0][1],
            rect=rect,
            max_t=self._max_t,
            y_range=y_range,
        )
        path.moveTo(first_pos)
        for t_value, y_value in self._preview_cycle[1:]:
            path.lineTo(point_to_widget_pos(t_value, y_value, rect=rect, max_t=self._max_t, y_range=y_range))
        painter.setPen(QtGui.QPen(QtGui.QColor(120, 210, 255), 2.0))
        painter.drawPath(path)

    def _draw_points(self, painter: QtGui.QPainter, rect: QtCore.QRectF, y_range: tuple[float, float] | None) -> None:
        if y_range is None:
            return
        for index, (t_value, y_value) in self._visible_points_with_indices():
            pos = point_to_widget_pos(t_value, y_value, rect=rect, max_t=self._max_t, y_range=y_range)
            radius = 4.5 if index == self._selected_index else 3.5
            fill = QtGui.QColor(255, 180, 70) if index == self._selected_index else QtGui.QColor(245, 245, 245)
            painter.setPen(QtGui.QPen(QtGui.QColor(25, 25, 25), 1.0))
            painter.setBrush(fill)
            painter.drawEllipse(pos, radius, radius)

    def _effective_y_range(self) -> tuple[float, float] | None:
        requested = effective_requested_y_range(self._min_value, self._max_value)
        if requested is not None:
            return requested
        data_points = [point for _, point in self._visible_points_with_indices()]
        data_points.extend(self._preview_cycle)
        return auto_y_range_from_points(data_points)

    def _find_hit_index(self, pos: QtCore.QPointF) -> int | None:
        y_range = self._effective_y_range()
        if y_range is None:
            return None
        rect = graph_draw_rect(self.rect())
        return find_point_hit_index(
            self._visible_points_with_indices(),
            pos,
            rect=rect,
            max_t=self._max_t,
            y_range=y_range,
            radius_px=8.0,
        )

    def _insert_point_at(self, pos: QtCore.QPointF) -> int:
        rect = graph_draw_rect(self.rect())
        y_range = self._effective_y_range() or (0.0, 1.0)
        t_value, y_value = widget_pos_to_point(pos, rect=rect, max_t=self._max_t, y_range=y_range)
        next_points, inserted_index = insert_control_point(
            self._points,
            t_value=t_value,
            y_value=y_value,
            max_t=self._max_t,
        )
        self._points = next_points
        self.update()
        return inserted_index

    def _move_dragged_point(self, pos: QtCore.QPointF) -> None:
        if self._drag_index is None:
            return
        rect = graph_draw_rect(self.rect())
        y_range = self._effective_y_range() or (0.0, 1.0)
        t_value, y_value = widget_pos_to_point(pos, rect=rect, max_t=self._max_t, y_range=y_range)
        self._points = move_control_point(
            self._points,
            index=self._drag_index,
            t_value=t_value,
            y_value=y_value,
            max_t=self._max_t,
            y_range=y_range,
        )
        self._selected_index = self._drag_index
        self.update()

    def _delete_selected_point(self, *, push_undo: bool) -> bool:
        if self._selected_index is None:
            return False
        if not (0 <= self._selected_index < len(self._points)):
            self._selected_index = None
            return False
        del self._points[self._selected_index]
        if self._selected_index >= len(self._points):
            self._selected_index = len(self._points) - 1 if self._points else None
        self.update()
        self._commit_points(push_undo=push_undo)
        return True

    def _commit_points(self, *, push_undo: bool) -> None:
        self._points_setter(serialize_control_points(self._points), push_undo)
        self.update()



def normalize_preview_cycle(raw_value: Any) -> list[tuple[float, float]]:
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



def coerce_preview_y_range(min_value: Any, max_value: Any) -> tuple[float, float] | None:
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



def effective_requested_y_range(min_value: Any, max_value: Any) -> tuple[float, float] | None:
    return coerce_preview_y_range(min_value, max_value)



def auto_y_range_from_points(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    ys = [point[1] for point in points if math.isfinite(point[1])]
    if not ys:
        return None
    low = min(ys)
    high = max(ys)
    if math.isclose(low, high):
        pad = 1.0 if math.isclose(low, 0.0) else abs(low) * 0.25
        return low - pad, high + pad
    pad = (high - low) * 0.08
    return low - pad, high + pad



def coerce_positive_preview_x(raw_value: Any) -> float:
    if isinstance(raw_value, bool):
        return 1.0
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(value) or value <= 0.0:
        return 1.0
    return value



def normalize_control_points(raw_value: Any, *, max_t: float) -> list[tuple[float, float]]:
    del max_t
    if not isinstance(raw_value, list):
        return []
    deduped: dict[float, float] = {}
    for item in raw_value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            t_value = float(item[0])
            y_value = float(item[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t_value) or not math.isfinite(y_value):
            continue
        deduped[float(t_value)] = float(y_value)
    ordered = sorted(deduped.items(), key=lambda item: item[0])
    return [(float(t_value), float(y_value)) for t_value, y_value in ordered]



def serialize_control_points(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[float(t_value), float(y_value)] for t_value, y_value in points]



def graph_draw_rect(rect: QtCore.QRect | QtCore.QRectF) -> QtCore.QRectF:
    rectf = QtCore.QRectF(rect)
    return rectf.adjusted(8.0, 8.0, -8.0, -8.0)



def point_to_widget_pos(
    t_value: float,
    y_value: float,
    *,
    rect: QtCore.QRectF,
    max_t: float,
    y_range: tuple[float, float],
) -> QtCore.QPointF:
    low, high = y_range
    width = max(rect.width(), 1.0)
    height = max(rect.height(), 1.0)
    x_ratio = 0.0 if max_t <= 0.0 else min(max(t_value / max_t, 0.0), 1.0)
    y_ratio = 0.0 if math.isclose(low, high) else min(max((y_value - low) / (high - low), 0.0), 1.0)
    return QtCore.QPointF(rect.left() + x_ratio * width, rect.bottom() - y_ratio * height)



def widget_pos_to_point(
    pos: QtCore.QPointF,
    *,
    rect: QtCore.QRectF,
    max_t: float,
    y_range: tuple[float, float],
) -> tuple[float, float]:
    low, high = y_range
    width = max(rect.width(), 1.0)
    height = max(rect.height(), 1.0)
    x_ratio = min(max((pos.x() - rect.left()) / width, 0.0), 1.0)
    y_ratio = min(max((rect.bottom() - pos.y()) / height, 0.0), 1.0)
    t_value = x_ratio * max_t
    y_value = low + y_ratio * (high - low)
    return float(t_value), float(y_value)



def find_point_hit_index(
    points: list[tuple[int, tuple[float, float]]] | list[tuple[float, float]],
    pos: QtCore.QPointF,
    *,
    rect: QtCore.QRectF,
    max_t: float,
    y_range: tuple[float, float],
    radius_px: float,
) -> int | None:
    radius_sq = float(radius_px) * float(radius_px)
    for entry in points:
        if isinstance(entry[0], int) and len(entry) == 2 and isinstance(entry[1], tuple):
            index = int(entry[0])
            t_value, y_value = entry[1]
        else:
            index = -1
            t_value, y_value = entry
        point_pos = point_to_widget_pos(t_value, y_value, rect=rect, max_t=max_t, y_range=y_range)
        dx = point_pos.x() - pos.x()
        dy = point_pos.y() - pos.y()
        if (dx * dx) + (dy * dy) <= radius_sq:
            return index if index >= 0 else None
    return None



def _neighbor_epsilon(max_t: float) -> float:
    return max(float(max_t) * _EDITOR_T_EPSILON, _EDITOR_T_EPSILON)



def insert_control_point(
    points: list[tuple[float, float]],
    *,
    t_value: float,
    y_value: float,
    max_t: float,
) -> tuple[list[tuple[float, float]], int]:
    next_points = list(points)
    insert_index = 0
    while insert_index < len(next_points) and next_points[insert_index][0] < t_value:
        insert_index += 1

    epsilon = _neighbor_epsilon(max_t)
    low_bound = 0.0
    high_bound = float(max_t)
    if insert_index > 0:
        low_bound = next_points[insert_index - 1][0] + epsilon
    if insert_index < len(next_points):
        high_bound = next_points[insert_index][0] - epsilon
    if low_bound > high_bound:
        clamped_t = min(max(t_value, 0.0), float(max_t))
    else:
        clamped_t = min(max(t_value, low_bound), high_bound)

    next_points.insert(insert_index, (float(clamped_t), float(y_value)))
    return next_points, insert_index



def move_control_point(
    points: list[tuple[float, float]],
    *,
    index: int,
    t_value: float,
    y_value: float,
    max_t: float,
    y_range: tuple[float, float],
) -> list[tuple[float, float]]:
    if not (0 <= index < len(points)):
        return list(points)
    low_y, high_y = y_range
    epsilon = _neighbor_epsilon(max_t)
    low_bound = 0.0
    high_bound = float(max_t)
    if index > 0:
        low_bound = points[index - 1][0] + epsilon
    if index + 1 < len(points):
        high_bound = points[index + 1][0] - epsilon
    if low_bound > high_bound:
        clamped_t = min(max(t_value, 0.0), float(max_t))
    else:
        clamped_t = min(max(t_value, low_bound), high_bound)
    clamped_y = min(max(float(y_value), low_y), high_y)

    next_points = list(points)
    next_points[index] = (float(clamped_t), float(clamped_y))
    return next_points



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



def make_wave_pattern_editor_control(
    *,
    field_tooltip: str,
    points_value_getter: Callable[[], Any],
    property_value_getter: Callable[[str], Any],
    points_setter: Callable[[Any, bool], None],
) -> tuple[WavePatternEditorControl, Callable[[Any], None]]:
    control = WavePatternEditorControl(points_setter=points_setter, field_tooltip=field_tooltip)

    def apply_points(value: Any) -> None:
        control.set_editor_data(
            points=value,
            preview_cycle=property_value_getter("preview"),
            min_value=property_value_getter("minValue"),
            max_value=property_value_getter("maxValue"),
            max_t=property_value_getter("maxT"),
        )

    apply_points(points_value_getter())
    return control, apply_points


class WaveHeatmapControl(QtWidgets.QWidget):
    def __init__(self, *, field_tooltip: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumWidth(0)
        self.setMinimumHeight(28)
        self.setMaximumHeight(40)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setObjectName("inline_wave_heatmap")
        if field_tooltip:
            self.setToolTip(field_tooltip)

    def set_heatmap_data(self, values: Any) -> None:
        self._values = normalize_heatmap_values(values)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event
        painter = QtGui.QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor(14, 14, 14, 200))
        inner = QtCore.QRectF(rect).adjusted(4.0, 4.0, -4.0, -4.0)
        painter.setPen(QtGui.QPen(QtGui.QColor(65, 65, 65), 1.0))
        painter.drawRoundedRect(inner, 4.0, 4.0)
        if not self._values:
            return
        peak = max(self._values)
        if peak <= 0.0:
            peak = 1.0
        width = max(inner.width(), 1.0)
        bar_width = width / float(len(self._values))
        for index, value in enumerate(self._values):
            normalized = min(max(float(value) / peak, 0.0), 1.0)
            color = QtGui.QColor.fromHsvF(0.62 - (0.62 * normalized), 0.85, 0.25 + (0.70 * normalized), 1.0)
            x_pos = inner.left() + (float(index) * bar_width)
            bar_rect = QtCore.QRectF(x_pos, inner.top(), max(1.0, bar_width + 0.5), inner.height())
            painter.fillRect(bar_rect, color)


def normalize_heatmap_values(raw_value: Any) -> list[float]:
    if not isinstance(raw_value, list):
        return []
    values: list[float] = []
    for item in raw_value:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(float(value))
    return values


def make_wave_heatmap_control(
    *,
    field_tooltip: str,
    heatmap_value_getter: Callable[[], Any],
) -> tuple[WaveHeatmapControl, Callable[[Any], None]]:
    control = WaveHeatmapControl(field_tooltip=field_tooltip)

    def apply_heatmap(value: Any) -> None:
        control.set_heatmap_data(value)

    apply_heatmap(heatmap_value_getter())
    return control, apply_heatmap
