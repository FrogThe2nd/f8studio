from __future__ import annotations

import os
import sys
import unittest

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pystudio.operators.viz_three_d import VizThreeDRuntimeNode


class VizThreeDWorldUpTests(unittest.TestCase):
    def test_coerce_world_up_accepts_all_signed_axes(self) -> None:
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("+x", default="+y"), "+x")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("-x", default="+y"), "-x")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("+y", default="+x"), "+y")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("-y", default="+x"), "-y")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("+z", default="+x"), "+z")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("-z", default="+x"), "-z")

    def test_coerce_world_up_maps_legacy_unsigned_values(self) -> None:
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("x", default="+y"), "+x")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("y", default="+x"), "+y")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("z", default="+x"), "+z")

    def test_coerce_world_up_falls_back_to_valid_default(self) -> None:
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("bad", default="-z"), "-z")
        self.assertEqual(VizThreeDRuntimeNode._coerce_world_up("bad", default="bad-default"), "+y")


if __name__ == "__main__":
    unittest.main()
