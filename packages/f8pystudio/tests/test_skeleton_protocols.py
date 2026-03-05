from __future__ import annotations

import os
import sys
import unittest

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PKG_STUDIO not in sys.path:
    sys.path.insert(0, PKG_STUDIO)

from f8pystudio.skeleton_protocols import skeleton_edges_for_nodes


class SkeletonProtocolsTests(unittest.TestCase):
    def test_unity_humanoid_edges_follow_named_hierarchy(self) -> None:
        node_names = ["Hips", "Spine", "Chest", "LeftUpperLeg", "LeftLowerLeg"]
        edges = skeleton_edges_for_nodes("unity_humanoid", node_names)
        self.assertIsNotNone(edges)
        assert edges is not None
        self.assertIn((0, 1), edges)
        self.assertIn((1, 2), edges)
        self.assertIn((0, 3), edges)
        self.assertIn((3, 4), edges)

    def test_unity_humanoid_skips_missing_nodes(self) -> None:
        node_names = ["Spine", "Head"]
        edges = skeleton_edges_for_nodes("unity_humanoid", node_names)
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
