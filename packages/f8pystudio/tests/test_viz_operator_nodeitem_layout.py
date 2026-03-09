from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from qtpy import QtCore

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pystudio.nodegraph.viz_operator_nodeitem import (
    F8StudioVizOperatorNodeItem,
    _required_non_state_port_region_height,
)


def test_required_non_state_port_region_height_zero_when_no_ports() -> None:
    assert _required_non_state_port_region_height(port_height=10.0, in_count=0, out_count=0) == 0.0


def test_required_non_state_port_region_height_scales_with_max_side_count() -> None:
    h1 = _required_non_state_port_region_height(port_height=10.0, in_count=1, out_count=0)
    h4 = _required_non_state_port_region_height(port_height=10.0, in_count=4, out_count=1)
    assert h4 > h1


def test_required_non_state_port_region_height_respects_port_height() -> None:
    h_small = _required_non_state_port_region_height(port_height=20.0, in_count=3, out_count=3)
    h_big = _required_non_state_port_region_height(port_height=30.0, in_count=3, out_count=3)
    assert h_big > h_small


def test_viz_calc_size_horizontal_uses_measured_state_panel_height() -> None:
    class _Text:
        @staticmethod
        def boundingRect() -> QtCore.QRectF:
            return QtCore.QRectF(0.0, 0.0, 20.0, 14.0)

    class _Stub:
        def __init__(self) -> None:
            self._text_item = _Text()
            self.inputs = []
            self.outputs = []
            self._widgets = {}
            self._state_inline_headers = {}
            self._state_inline_bodies = {}

        @staticmethod
        def _ensure_inline_state_widgets() -> bool:
            return False

        @staticmethod
        def _schedule_deferred_draw_node() -> None:
            return

        @staticmethod
        def _backend_node() -> object:
            return SimpleNamespace(
                effective_state_fields=lambda: [
                    SimpleNamespace(
                        showOnNode=True,
                        name="code",
                    )
                ]
            )

        @staticmethod
        def _state_field_name_if_visible(state_field: object) -> str | None:
            return F8StudioVizOperatorNodeItem._state_field_name_if_visible(state_field)

        @staticmethod
        def _measure_state_panel_height(state_name: str, *, default_header_h: float) -> float:
            del state_name, default_header_h
            return 120.0

        @staticmethod
        def _port_group(name: str) -> str:
            del name
            return "other"

        @staticmethod
        def _port_name(port: object) -> str:
            del port
            return ""

    stub = _Stub()
    _, height = F8StudioVizOperatorNodeItem._calc_size_horizontal(stub)  # type: ignore[arg-type]
    assert height >= 120.0
