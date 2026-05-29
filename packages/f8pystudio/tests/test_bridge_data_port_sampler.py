from __future__ import annotations

from f8pysdk.codec import encode_obj
from f8pystudio.bridge.data_port_sampler import summarize_data_port_payload


def test_summarize_data_port_payload_includes_small_json_value() -> None:
    sample = summarize_data_port_payload(
        service_id="svc",
        node_id="node",
        port="out",
        key="f8/svc/svc/nodes/node/data/out",
        payload=encode_obj({"value": {"answer": 42}, "ts": 123}),
        include_value=True,
        max_value_bytes=1024,
    )

    assert sample["decoded"] is True
    assert sample["payloadKind"] == "json_object"
    assert sample["value"] == {"answer": 42}
    assert sample["tsMs"] == 123


def test_summarize_data_port_payload_omits_large_json_value() -> None:
    sample = summarize_data_port_payload(
        service_id="svc",
        node_id="node",
        port="out",
        key="f8/svc/svc/nodes/node/data/out",
        payload=encode_obj({"value": {"text": "abcdef"}}),
        include_value=True,
        max_value_bytes=4,
    )

    assert sample["decoded"] is True
    assert sample["valueOmitted"] is True
    assert sample["omitReason"] == "value_too_large"
    assert "value" not in sample


def test_summarize_data_port_payload_reports_raw_bytes_metadata() -> None:
    sample = summarize_data_port_payload(
        service_id="svc",
        node_id="node",
        port="out",
        key="f8/svc/svc/nodes/node/data/out",
        payload=b"\x00\x01\x02",
        include_value=True,
    )

    assert sample["decoded"] is False
    assert sample["payloadKind"] == "bytes"
    assert sample["payloadBytes"] == 3
    assert "value" not in sample
