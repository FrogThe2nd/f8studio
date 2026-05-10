from __future__ import annotations

import json
import unittest
from pathlib import Path


class CvkitTrackingDescribeTest(unittest.TestCase):
    def test_tracking_describe_exposes_current_configurable_state_fields(self) -> None:
        describe_path = Path("services/f8/cvkit/tracking/describe.json")
        payload = json.loads(describe_path.read_text(encoding="utf-8"))

        service = payload["service"]
        state_fields = service["stateFields"]
        init_select_field = next(field for field in state_fields if field["name"] == "initSelect")
        stop_cooldown_field = next(field for field in state_fields if field["name"] == "stopTrackingCooldownMs")
        active_field = next(field for field in state_fields if field["name"] == "active")

        self.assertEqual(init_select_field["access"], "rw")
        self.assertEqual(init_select_field["valueSchema"]["default"], "closest_center")
        self.assertEqual(
            init_select_field["valueSchema"]["enum"],
            ["first_box", "closest_center", "largest_area", "highest_score"],
        )
        self.assertEqual(stop_cooldown_field["access"], "rw")
        self.assertEqual(stop_cooldown_field["valueSchema"]["default"], 1000)
        self.assertEqual(stop_cooldown_field["valueSchema"]["minimum"], 0.0)
        self.assertEqual(stop_cooldown_field["valueSchema"]["maximum"], 60000.0)
        self.assertEqual(active_field["access"], "rw")
        self.assertIs(active_field["valueSchema"]["default"], True)

    def test_tracking_describe_exposes_process_metrics_state_fields(self) -> None:
        describe_path = Path("services/f8/cvkit/tracking/describe.json")
        payload = json.loads(describe_path.read_text(encoding="utf-8"))

        service = payload["service"]
        state_fields = service["stateFields"]
        fields_by_name = {field["name"]: field for field in state_fields}

        for field_name in (
            "observedFrames",
            "processedFrames",
            "droppedFrames",
            "lastProcessMs",
            "avgProcessMs",
            "lastLatencyMs",
            "avgLatencyMs",
            "processFps",
        ):
            self.assertIn(field_name, fields_by_name)
            self.assertEqual(fields_by_name[field_name]["access"], "ro")
            self.assertFalse(fields_by_name[field_name]["showOnNode"])

        self.assertEqual(fields_by_name["observedFrames"]["valueSchema"]["type"], "integer")
        self.assertEqual(fields_by_name["processedFrames"]["valueSchema"]["type"], "integer")
        self.assertEqual(fields_by_name["droppedFrames"]["valueSchema"]["type"], "integer")
        self.assertEqual(fields_by_name["lastProcessMs"]["valueSchema"]["type"], "number")
        self.assertEqual(fields_by_name["avgProcessMs"]["valueSchema"]["type"], "number")
        self.assertEqual(fields_by_name["lastLatencyMs"]["valueSchema"]["type"], "number")
        self.assertEqual(fields_by_name["avgLatencyMs"]["valueSchema"]["type"], "number")
        self.assertEqual(fields_by_name["processFps"]["valueSchema"]["type"], "number")


if __name__ == "__main__":
    unittest.main()
