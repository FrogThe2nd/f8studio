from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json, validate_as
from typing import Any

from .generated import F8ComplexObjectTypeSchema, F8DataPortSpec, F8DataTypeSchema
from .generated import F8MonitorReport, F8MonitorSnapshot
from .schema_helpers import boolean_schema, complex_object_schema, integer_schema, number_schema, string_schema

MONITOR_PORT_NAME = "monitor"
LEGACY_TELEMETRY_PORT_NAME = "telemetry"
MONITOR_SNAPSHOT_SCHEMA_VERSION = "f8monitor/1"
MONITOR_REPORT_SCHEMA_VERSION = "f8monitorReport/1"


class MonitorContractError(ValueError):
    """Raised when monitor payloads/describe contracts violate the unified schema contract."""


def monitor_snapshot_value_schema() -> F8DataTypeSchema:
    cpu = complex_object_schema(
        properties={
            "processPercent": number_schema(default=0.0, minimum=0.0),
            "systemPercent": number_schema(default=0.0, minimum=0.0),
        }
    )
    memory = complex_object_schema(
        properties={
            "rssBytes": integer_schema(default=0, minimum=0),
            "vmsBytes": integer_schema(default=0, minimum=0),
        }
    )
    gpu = complex_object_schema(
        properties={
            "vendor": string_schema(default=""),
            "deviceIndex": integer_schema(default=-1),
            "utilPercent": number_schema(default=0.0, minimum=0.0),
            "memoryUsedBytes": integer_schema(default=0, minimum=0),
            "memoryTotalBytes": integer_schema(default=0, minimum=0),
            "available": boolean_schema(default=False),
        }
    )
    frame = complex_object_schema(
        properties={
            "observed": integer_schema(default=0, minimum=0),
            "processed": integer_schema(default=0, minimum=0),
            "dropped": integer_schema(default=0, minimum=0),
        }
    )
    timing = complex_object_schema(
        properties={
            "processMsAvg": number_schema(default=0.0, minimum=0.0),
            "processMsP95": number_schema(default=0.0, minimum=0.0),
            "waitMsAvg": number_schema(default=0.0, minimum=0.0),
            "waitMsP95": number_schema(default=0.0, minimum=0.0),
            "latencyMsAvg": number_schema(default=0.0, minimum=0.0),
            "latencyMsP95": number_schema(default=0.0, minimum=0.0),
        }
    )
    queue = complex_object_schema(
        properties={
            "depth": integer_schema(default=0, minimum=0),
        }
    )
    error = complex_object_schema(
        properties={
            "countWindow": integer_schema(default=0, minimum=0),
            "lastCode": string_schema(default=""),
            "lastMessage": string_schema(default=""),
            "lastTsMs": integer_schema(default=0, minimum=0),
        }
    )
    cpu_schema = cpu
    memory_schema = memory
    gpu_schema = gpu
    frame_schema = frame
    timing_schema = timing
    queue_schema = queue
    error_schema = error

    root = complex_object_schema(
        properties={
            "schemaVersion": string_schema(default="f8monitor/1", enum=["f8monitor/1"]),
            "serviceId": string_schema(default=""),
            "serviceClass": string_schema(default=""),
            "nodeId": string_schema(default=""),
            "tsMs": integer_schema(default=0, minimum=0),
            "alive": boolean_schema(default=True),
            "ready": boolean_schema(default=False),
            "active": boolean_schema(default=True),
            "uptimeMs": integer_schema(default=0, minimum=0),
            "cpu": cpu_schema,
            "memory": memory_schema,
            "gpu": gpu_schema,
            "frame": frame_schema,
            "timing": timing_schema,
            "queue": queue_schema,
            "error": error_schema,
        }
    )
    if isinstance(root, F8ComplexObjectTypeSchema):
        return root
    return root


def monitor_snapshot_data_port() -> F8DataPortSpec:
    return F8DataPortSpec(
        name=MONITOR_PORT_NAME,
        description="Unified runtime monitor snapshots (health/resource/perf/error).",
        valueSchema=monitor_snapshot_value_schema(),
        required=True,
        showOnNode=False,
    )


def monitor_snapshot_schema_dict() -> dict[str, object]:
    return dump_json(monitor_snapshot_value_schema(), mode="json", by_alias=True)


def validate_monitor_snapshot_payload(payload: dict[str, Any] | F8MonitorSnapshot) -> F8MonitorSnapshot:
    if isinstance(payload, F8MonitorSnapshot):
        data = dump_json(payload, mode="json", by_alias=True)
        return validate_as(F8MonitorSnapshot, data)
    if not isinstance(payload, dict):
        raise MonitorContractError("monitor snapshot payload must be dict or F8MonitorSnapshot")
    return validate_as(F8MonitorSnapshot, payload)


def validate_monitor_report_payload(payload: dict[str, Any] | F8MonitorReport) -> F8MonitorReport:
    if isinstance(payload, F8MonitorReport):
        data = dump_json(payload, mode="json", by_alias=True)
        return validate_as(F8MonitorReport, data)
    if not isinstance(payload, dict):
        raise MonitorContractError("monitor report payload must be dict or F8MonitorReport")
    return validate_as(F8MonitorReport, payload)


def validate_describe_monitor_contract(
    payload: dict[str, Any],
) -> None:
    if not isinstance(payload, dict):
        raise MonitorContractError("describe payload must be a dict")

    target = payload
    service_obj = payload.get("service")
    if isinstance(service_obj, dict):
        target = service_obj

    ports_obj = target.get("dataOutPorts")
    if not isinstance(ports_obj, list):
        raise MonitorContractError("service.dataOutPorts must be a list")

    monitor_port: dict[str, Any] | None = None
    telemetry_ports: list[dict[str, Any]] = []
    for item in ports_obj:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name == MONITOR_PORT_NAME:
            monitor_port = item
            continue
        if name == LEGACY_TELEMETRY_PORT_NAME:
            telemetry_ports.append(item)

    if monitor_port is None:
        raise MonitorContractError("service.dataOutPorts must contain `monitor`")

    required_raw = monitor_port.get("required")
    if required_raw is not None and not bool(required_raw):
        raise MonitorContractError("`monitor` dataOutPort must set required=true")

    monitor_schema_obj = monitor_port.get("valueSchema")
    if not isinstance(monitor_schema_obj, dict):
        raise MonitorContractError("`monitor` dataOutPort must contain object valueSchema")
    try:
        parsed_schema = dump_json(
            validate_as(F8DataTypeSchema, monitor_schema_obj),
            mode="json",
            by_alias=True,
        )
    except Exception as exc:
        raise MonitorContractError(f"`monitor` valueSchema is invalid: {type(exc).__name__}: {exc}") from exc

    expected_schema = monitor_snapshot_schema_dict()
    if parsed_schema != expected_schema:
        raise MonitorContractError("`monitor` valueSchema must match F8MonitorSnapshot schema")

    if telemetry_ports:
        raise MonitorContractError("legacy `telemetry` output port is forbidden; use `monitor` only")
