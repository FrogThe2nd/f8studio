from __future__ import annotations

from qtpy import QtGui

from f8pystudio.qt_font_utils import normalize_font_point_size


def test_normalize_font_point_size_uses_existing_point_size() -> None:
    font = QtGui.QFont()
    font.setPointSize(11)

    normalized = normalize_font_point_size(font)

    assert normalized.pointSize() == 11


def test_normalize_font_point_size_converts_pixel_size_when_point_size_missing() -> None:
    font = QtGui.QFont()
    font.setPixelSize(16)

    normalized = normalize_font_point_size(font)

    assert normalized.pointSize() == 12


def test_normalize_font_point_size_falls_back_when_font_has_no_size() -> None:
    font = QtGui.QFont()

    normalized = normalize_font_point_size(font, fallback_point_size=9)

    assert normalized.pointSize() == 9
