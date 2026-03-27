from __future__ import annotations

import math

from qtpy import QtCore, QtGui, QtWidgets

from f8pystudio.components.controls import F8Dial


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _mouse_event(
    event_type: QtCore.QEvent.Type,
    pos: QtCore.QPointF,
    *,
    button: QtCore.Qt.MouseButton,
    buttons: QtCore.Qt.MouseButton,
) -> QtGui.QMouseEvent:
    return QtGui.QMouseEvent(
        event_type,
        pos,
        pos,
        pos,
        button,
        buttons,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )


def _dial_pos(widget: QtWidgets.QWidget, fraction: float) -> QtCore.QPointF:
    rect = QtCore.QRectF(widget.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
    center = rect.center()
    radius = min(rect.width(), rect.height()) / 2.0
    theta = (float(fraction) * 2.0 * math.pi) - (math.pi / 2.0)
    return QtCore.QPointF(center.x() + math.cos(theta) * radius, center.y() + math.sin(theta) * radius)


def test_dial_programmatic_value_respects_range() -> None:
    _ensure_app()
    widget = F8Dial(minimum=-1.0, maximum=1.0)

    widget.set_value(0.5)
    assert float(widget.value()) == 0.5

    widget.set_value(3.0)
    assert float(widget.value()) == 1.0


def test_dial_integer_mode_rounds_programmatic_value() -> None:
    _ensure_app()
    widget = F8Dial(minimum=0.0, maximum=10.0, integer=True)

    widget.set_value(2.6)

    assert int(widget.value()) == 3


def test_dial_set_range_none_falls_back_to_zero_one() -> None:
    _ensure_app()
    widget = F8Dial(minimum=-5.0, maximum=5.0)

    widget.set_range(None, None)
    widget.set_value(2.0)

    assert float(widget.value()) == 1.0


def test_dial_crosses_top_seam_with_wraparound() -> None:
    _ensure_app()
    widget = F8Dial(minimum=-1.0, maximum=1.0)
    widget.resize(96, 96)

    changing: list[float] = []
    committed: list[float] = []
    widget.valueChanging.connect(lambda value: changing.append(float(value)))  # type: ignore[attr-defined]
    widget.valueCommitted.connect(lambda value: committed.append(float(value)))  # type: ignore[attr-defined]

    near_max = _dial_pos(widget, 0.99)
    near_min = _dial_pos(widget, 0.01)
    widget.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress,
            near_max,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseMove,
            near_min,
            button=QtCore.Qt.MouseButton.NoButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonRelease,
            near_min,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.NoButton,
        )
    )

    assert len(changing) == 2
    assert len(committed) == 1
    assert changing[0] > 0.9
    assert changing[1] < -0.9
    assert committed[0] < -0.9


def test_dial_noloop_clamps_at_seam_without_wraparound() -> None:
    _ensure_app()
    widget = F8Dial(minimum=-1.0, maximum=1.0, loop=False)
    widget.resize(96, 96)

    changing: list[float] = []
    committed: list[float] = []
    widget.valueChanging.connect(lambda value: changing.append(float(value)))  # type: ignore[attr-defined]
    widget.valueCommitted.connect(lambda value: committed.append(float(value)))  # type: ignore[attr-defined]

    near_max = _dial_pos(widget, 0.99)
    near_min = _dial_pos(widget, 0.01)
    widget.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress,
            near_max,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseMoveEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseMove,
            near_min,
            button=QtCore.Qt.MouseButton.NoButton,
            buttons=QtCore.Qt.MouseButton.LeftButton,
        )
    )
    widget.mouseReleaseEvent(
        _mouse_event(
            QtCore.QEvent.Type.MouseButtonRelease,
            near_min,
            button=QtCore.Qt.MouseButton.LeftButton,
            buttons=QtCore.Qt.MouseButton.NoButton,
        )
    )

    assert len(changing) == 2
    assert len(committed) == 1
    assert changing[0] > 0.9
    assert changing[1] > 0.98
    assert committed[0] > 0.98


def test_dial_small_size_keeps_negative_value_visible() -> None:
    _ensure_app()
    widget = F8Dial(minimum=-1.0, maximum=1.0)
    widget.resize(56, 56)
    widget.set_value(-0.75)

    widget.show()
    widget.repaint()

    assert str(widget.value()).startswith("-")
