from __future__ import annotations

import json
import unittest
from pathlib import Path


class CvkitTrackingDescribeTest(unittest.TestCase):
    def test_tracker_kind_state_field_exposes_supported_trackers(self) -> None:
        describe_path = Path("services/f8/cvkit/tracking/describe.json")
        payload = json.loads(describe_path.read_text(encoding="utf-8"))

        service = payload["service"]
        state_fields = service["stateFields"]
        tracker_kind_field = next(field for field in state_fields if field["name"] == "trackerKind")

        self.assertEqual(tracker_kind_field["access"], "rw")
        self.assertEqual(tracker_kind_field["valueSchema"]["default"], "csrt")
        self.assertEqual(
            tracker_kind_field["valueSchema"]["enum"],
            ["csrt", "kcf", "mil", "boosting", "median_flow", "mosse", "tld"],
        )


if __name__ == "__main__":
    unittest.main()
