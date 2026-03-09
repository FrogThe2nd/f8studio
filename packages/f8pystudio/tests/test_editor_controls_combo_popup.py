from __future__ import annotations

import os
import sys

from qtpy import QtCore

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pystudio.widgets.editor_controls import _choose_best_view_for_scene_point, _popup_above_y


class _FakeViewport:
    def __init__(self, rect: QtCore.QRect) -> None:
        self._rect = rect

    def rect(self) -> QtCore.QRect:
        return self._rect


class _FakeView:
    def __init__(
        self,
        *,
        visible: bool,
        viewport_rect: QtCore.QRect,
        mapped_point: QtCore.QPoint,
        focused: bool = False,
        active: bool = False,
    ) -> None:
        self._visible = visible
        self._viewport = _FakeViewport(viewport_rect)
        self._mapped_point = mapped_point
        self._focused = focused
        self._active = active

    def isVisible(self) -> bool:
        return self._visible

    def mapFromScene(self, _scene_pos: QtCore.QPointF) -> QtCore.QPoint:
        return self._mapped_point

    def viewport(self) -> _FakeViewport:
        return self._viewport

    def hasFocus(self) -> bool:
        return self._focused

    def isActiveWindow(self) -> bool:
        return self._active


def test_popup_above_y_uses_anchor_minus_popup_height() -> None:
    assert _popup_above_y(100, 30) == 70
    assert _popup_above_y(100, 0) == 100


def test_choose_best_view_prefers_view_containing_scene_point() -> None:
    point = QtCore.QPointF(0.0, 0.0)
    miss = _FakeView(visible=True, viewport_rect=QtCore.QRect(0, 0, 10, 10), mapped_point=QtCore.QPoint(30, 30))
    hit = _FakeView(visible=True, viewport_rect=QtCore.QRect(0, 0, 50, 50), mapped_point=QtCore.QPoint(10, 10))
    out = _choose_best_view_for_scene_point([miss, hit], point)
    assert out is hit


def test_choose_best_view_prefers_focused_when_multiple_contain() -> None:
    point = QtCore.QPointF(0.0, 0.0)
    view1 = _FakeView(visible=True, viewport_rect=QtCore.QRect(0, 0, 50, 50), mapped_point=QtCore.QPoint(10, 10))
    view2 = _FakeView(
        visible=True,
        viewport_rect=QtCore.QRect(0, 0, 50, 50),
        mapped_point=QtCore.QPoint(10, 10),
        focused=True,
    )
    out = _choose_best_view_for_scene_point([view1, view2], point)
    assert out is view2
