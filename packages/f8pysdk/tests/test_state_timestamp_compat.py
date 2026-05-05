from __future__ import annotations

from f8pysdk.service_bus.state.helpers import extract_ts_field


def test_extract_ts_field_accepts_python_and_cpp_state_payload_names() -> None:
    assert extract_ts_field({"tsMs": 101}) == 101
    assert extract_ts_field({"ts": 102}) == 102
    assert extract_ts_field({"ts_ms": 103}) == 103
    assert extract_ts_field({"value": 1}) is None
