from __future__ import annotations

from typing import Any

from .generated import F8DataPortSpec, F8OperatorSpec, F8ServiceSpec, F8StateAccess, F8StateSpec
from .schema_helpers import boolean_schema, string_schema
from .monitor_schema import (
    LEGACY_TELEMETRY_PORT_NAME,
    MONITOR_PORT_NAME,
    monitor_snapshot_data_port,
    monitor_snapshot_schema_dict,
)


ACTIVE_FIELD_NAME = "active"
SVC_ID_FIELD_NAME = "svcId"
OPERATOR_ID_FIELD_NAME = "operatorId"

def _service_active_state_spec() -> F8StateSpec:
    return F8StateSpec(
        name=ACTIVE_FIELD_NAME,
        label="Active",
        description="Service lifecycle state (activate/deactivate).",
        valueSchema=boolean_schema(default=True),
        access=F8StateAccess.rw,
        required=True,
        showOnNode=False,
    )


def _svc_id_state_spec() -> F8StateSpec:
    return F8StateSpec(
        name=SVC_ID_FIELD_NAME,
        label="Service Id",
        description="Readonly: current service instance id (svcId).",
        valueSchema=string_schema(),
        access=F8StateAccess.ro,
        required=True,
        showOnNode=False,
    )


def _operator_id_state_spec() -> F8StateSpec:
    return F8StateSpec(
        name=OPERATOR_ID_FIELD_NAME,
        label="Operator Id",
        description="Readonly: current operator/node id (operatorId).",
        valueSchema=string_schema(),
        access=F8StateAccess.ro,
        required=True,
        showOnNode=False,
    )


def _copy_state_specs_without_names(
    state_fields: list[F8StateSpec] | None, *, names_to_remove: set[str]
) -> list[F8StateSpec]:
    filtered: list[F8StateSpec] = []
    for field in list(state_fields or []):
        field_name = str(field.name or "").strip()
        if field_name in names_to_remove:
            continue
        filtered.append(field)
    return filtered


def _copy_data_port_specs_without_names(
    ports: list[F8DataPortSpec] | None, *, names_to_remove: set[str]
) -> list[F8DataPortSpec]:
    filtered: list[F8DataPortSpec] = []
    for port in list(ports or []):
        port_name = str(port.name or "").strip()
        if port_name in names_to_remove:
            continue
        filtered.append(port)
    return filtered


def service_state_fields_with_builtins(state_fields: list[F8StateSpec] | None) -> list[F8StateSpec]:
    fields = _copy_state_specs_without_names(
        state_fields,
        names_to_remove={ACTIVE_FIELD_NAME, SVC_ID_FIELD_NAME},
    )
    fields.append(_service_active_state_spec())
    fields.append(_svc_id_state_spec())
    return fields


def service_data_out_ports_with_builtins(data_out_ports: list[F8DataPortSpec] | None) -> list[F8DataPortSpec]:
    ports = _copy_data_port_specs_without_names(
        data_out_ports,
        names_to_remove={MONITOR_PORT_NAME, LEGACY_TELEMETRY_PORT_NAME},
    )
    ports.append(monitor_snapshot_data_port())
    return ports


def operator_state_fields_with_builtins(state_fields: list[F8StateSpec] | None) -> list[F8StateSpec]:
    fields = _copy_state_specs_without_names(
        state_fields,
        names_to_remove={SVC_ID_FIELD_NAME, OPERATOR_ID_FIELD_NAME},
    )
    fields.append(_svc_id_state_spec())
    fields.append(_operator_id_state_spec())
    return fields


def upsert_builtin_state_fields_for_service_spec(service_spec: F8ServiceSpec) -> None:
    service_spec.stateFields = service_state_fields_with_builtins(list(service_spec.stateFields or []))
    service_spec.dataOutPorts = service_data_out_ports_with_builtins(list(service_spec.dataOutPorts or []))


def upsert_builtin_state_fields_for_operator_spec(operator_spec: F8OperatorSpec) -> None:
    operator_spec.stateFields = operator_state_fields_with_builtins(list(operator_spec.stateFields or []))


def _service_active_field_dict() -> dict[str, Any]:
    return {
        "name": ACTIVE_FIELD_NAME,
        "label": "Active",
        "description": "Service lifecycle state (activate/deactivate).",
        "valueSchema": {"type": "boolean", "default": True},
        "access": "rw",
        "required": True,
        "showOnNode": False,
    }


def _svc_id_field_dict() -> dict[str, Any]:
    return {
        "name": SVC_ID_FIELD_NAME,
        "label": "Service Id",
        "description": "Readonly: current service instance id (svcId).",
        "valueSchema": {"type": "string"},
        "access": "ro",
        "required": True,
        "showOnNode": False,
    }


def _operator_id_field_dict() -> dict[str, Any]:
    return {
        "name": OPERATOR_ID_FIELD_NAME,
        "label": "Operator Id",
        "description": "Readonly: current operator/node id (operatorId).",
        "valueSchema": {"type": "string"},
        "access": "ro",
        "required": True,
        "showOnNode": False,
    }


def _monitor_port_dict() -> dict[str, Any]:
    return {
        "name": MONITOR_PORT_NAME,
        "description": "Unified runtime monitor snapshots (health/resource/perf/error).",
        "required": True,
        "showOnNode": False,
        "valueSchema": monitor_snapshot_schema_dict(),
    }


def _state_field_dicts_with_builtins(
    state_fields: Any,
    *,
    names_to_remove: set[str],
    builtin_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(state_fields, list):
        for item in state_fields:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name in names_to_remove:
                continue
            normalized.append(dict(item))
    for field in builtin_fields:
        normalized.append(dict(field))
    return normalized


def _data_port_dicts_with_builtins(
    data_ports: Any,
    *,
    names_to_remove: set[str],
    builtin_ports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(data_ports, list):
        for item in data_ports:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name in names_to_remove:
                continue
            normalized.append(dict(item))
    for port in builtin_ports:
        normalized.append(dict(port))
    return normalized


def normalize_describe_payload_dict(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)

    service_obj = out.get("service")
    if isinstance(service_obj, dict):
        service_spec = dict(service_obj)
        service_spec["stateFields"] = _state_field_dicts_with_builtins(
            service_spec.get("stateFields"),
            names_to_remove={ACTIVE_FIELD_NAME, SVC_ID_FIELD_NAME},
            builtin_fields=[_service_active_field_dict(), _svc_id_field_dict()],
        )
        service_spec["dataOutPorts"] = _data_port_dicts_with_builtins(
            service_spec.get("dataOutPorts"),
            names_to_remove={MONITOR_PORT_NAME, LEGACY_TELEMETRY_PORT_NAME},
            builtin_ports=[_monitor_port_dict()],
        )
        out["service"] = service_spec

        operators_raw = out.get("operators")
        operators_out: list[dict[str, Any]] = []
        if isinstance(operators_raw, list):
            for operator_item in operators_raw:
                if not isinstance(operator_item, dict):
                    continue
                operator_spec = dict(operator_item)
                operator_spec["stateFields"] = _state_field_dicts_with_builtins(
                    operator_spec.get("stateFields"),
                    names_to_remove={SVC_ID_FIELD_NAME, OPERATOR_ID_FIELD_NAME},
                    builtin_fields=[_svc_id_field_dict(), _operator_id_field_dict()],
                )
                operators_out.append(operator_spec)
        out["operators"] = operators_out
        return out

    service_class = str(out.get("serviceClass") or "").strip()
    schema_version = str(out.get("schemaVersion") or "").strip()
    if service_class or schema_version == "f8service/1":
        out["stateFields"] = _state_field_dicts_with_builtins(
            out.get("stateFields"),
            names_to_remove={ACTIVE_FIELD_NAME, SVC_ID_FIELD_NAME},
            builtin_fields=[_service_active_field_dict(), _svc_id_field_dict()],
        )
        out["dataOutPorts"] = _data_port_dicts_with_builtins(
            out.get("dataOutPorts"),
            names_to_remove={MONITOR_PORT_NAME, LEGACY_TELEMETRY_PORT_NAME},
            builtin_ports=[_monitor_port_dict()],
        )
    return out
