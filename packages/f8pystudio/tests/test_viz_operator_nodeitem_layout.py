from __future__ import annotations

import os
import sys

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pystudio.nodegraph.viz_operator_nodeitem import _required_non_state_port_region_height


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
