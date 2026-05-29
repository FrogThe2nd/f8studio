from __future__ import annotations

import json
import time
from typing import Any

from f8pysdk.codec import decode_obj, dump_json

_JSON_VALUE_BYTES_LIMIT = 64 * 1024


def summarize_data_port_payload(
    *,
    service_id: str,
    node_id: str,
    port: str,
    key: str,
    payload: bytes,
    include_value: bool,
    max_value_bytes: int = _JSON_VALUE_BYTES_LIMIT,
) -> dict[str, Any]:
    payload_bytes = bytes(payload)
    observed_at_ms = int(time.time() * 1000)
    base: dict[str, Any] = {
        "serviceId": str(service_id),
        "nodeId": str(node_id),
        "port": str(port),
        "key": str(key),
        "observedAtMs": observed_at_ms,
        "payloadBytes": len(payload_bytes),
        "decoded": False,
        "payloadKind": "bytes",
    }
    if not payload_bytes:
        base["payloadKind"] = "empty"
        return base

    try:
        decoded = decode_obj(payload_bytes)
    except ValueError:
        return base

    base["decoded"] = True
    if isinstance(decoded, dict) and "value" in decoded:
        value = decoded.get("value")
        ts_raw = decoded.get("tsMs") if "tsMs" in decoded else decoded.get("ts")
        if ts_raw is not None:
            try:
                base["tsMs"] = int(ts_raw)
            except (TypeError, ValueError):
                base["tsRaw"] = str(ts_raw)
        base["payloadKind"] = _payload_kind(value)
        _attach_value(base, value, include_value=include_value, max_value_bytes=max_value_bytes)
        return base

    base["payloadKind"] = _payload_kind(decoded)
    _attach_value(base, decoded, include_value=include_value, max_value_bytes=max_value_bytes)
    return base


def _payload_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "json_object"
    if isinstance(value, list):
        return "json_array"
    if isinstance(value, (str, int, float, bool)):
        return "json_scalar"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "bytes"
    return type(value).__name__


def _attach_value(
    sample: dict[str, Any],
    value: Any,
    *,
    include_value: bool,
    max_value_bytes: int,
) -> None:
    json_value = dump_json(value, mode="json")
    try:
        encoded = json.dumps(json_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        sample["valueSummary"] = str(type(value).__name__)
        return

    sample["valueJsonBytes"] = len(encoded)
    if not include_value:
        sample["valueOmitted"] = True
        return
    if len(encoded) > max(0, int(max_value_bytes)):
        sample["valueOmitted"] = True
        sample["omitReason"] = "value_too_large"
        return
    sample["value"] = json_value
