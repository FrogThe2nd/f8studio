from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from typing import Protocol, cast
from uuid import uuid4

import msgspec

from f8pysdk import F8JsonValue, F8OperatorSpec, F8ServiceSpec, F8VariantRecord
from f8pysdk.msgspec_codec import dump_json, validate_as

from ..graph_assets.common import JsonObject, json_object_from_value
from .variant_models import F8VariantKind, variant_now_iso


class _VariantNodeModel(Protocol):
    properties: Mapping[str, object]
    custom_properties: Mapping[str, object]

    def get_property(self, name: str) -> object: ...


class _VariantNode(Protocol):
    spec: object
    model: _VariantNodeModel
    type_: object

    def ui_overrides(self) -> JsonObject: ...


class _NamedSpec(Protocol):
    name: object


def _json_object_or_none(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def _json_object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    entries = cast(list[object], value)
    out: list[JsonObject] = []
    for entry in entries:
        entry_object = _json_object_or_none(entry)
        if entry_object is not None:
            out.append(entry_object)
    return out


def _json_object_field(payload: JsonObject, key: str) -> JsonObject | None:
    return _json_object_or_none(payload.get(key))


def _string_list(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values if str(value).strip()]


def _state_fields_by_name(spec_json: JsonObject) -> dict[str, JsonObject]:
    fields = _json_object_list(spec_json.get("stateFields"))
    out: dict[str, JsonObject] = {}
    for entry in fields:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        out[name] = entry
    return out


def _apply_state_ui_overrides(spec_json: JsonObject, ui: JsonObject) -> None:
    state_overrides = _json_object_field(ui, "stateFields")
    if state_overrides is None:
        return
    fields = _state_fields_by_name(spec_json)
    allowed_keys = ("showOnNode", "uiControl", "label", "description")
    for name, patch_value in state_overrides.items():
        patch = _json_object_or_none(patch_value)
        if patch is None:
            continue
        field = fields.get(str(name))
        if field is None:
            continue
        for key in allowed_keys:
            if key in patch:
                field[key] = patch[key]


def _apply_data_port_ui_overrides(spec_json: JsonObject, ui: JsonObject) -> None:
    data_ports = _json_object_field(ui, "dataPorts")
    if data_ports is None:
        return
    for override_key, spec_key in (("in", "dataInPorts"), ("out", "dataOutPorts")):
        patch_map = _json_object_field(data_ports, override_key)
        if patch_map is None:
            continue
        ports = _json_object_list(spec_json.get(spec_key))
        by_name: dict[str, JsonObject] = {}
        for port in ports:
            name = str(port.get("name") or "").strip()
            if name:
                by_name[name] = port
        for name, patch_value in patch_map.items():
            patch = _json_object_or_none(patch_value)
            if patch is None:
                continue
            port = by_name.get(str(name))
            if port is None:
                continue
            if "showOnNode" in patch:
                port["showOnNode"] = bool(patch["showOnNode"])


def _apply_command_ui_overrides(spec_json: JsonObject, ui: JsonObject) -> None:
    commands_overrides = _json_object_field(ui, "commands")
    if commands_overrides is None:
        return
    commands = _json_object_list(spec_json.get("commands"))
    if not commands:
        return
    by_name: dict[str, JsonObject] = {}
    for command in commands:
        name = str(command.get("name") or "").strip()
        if name:
            by_name[name] = command
    for name, patch_value in commands_overrides.items():
        patch = _json_object_or_none(patch_value)
        if patch is None:
            continue
        command = by_name.get(str(name))
        if command is None:
            continue
        if "showOnNode" in patch:
            command["showOnNode"] = bool(patch["showOnNode"])


def _apply_state_defaults_from_values(spec_json: JsonObject, state_values: JsonObject) -> None:
    if not state_values:
        return
    fields = _state_fields_by_name(spec_json)
    for name, field in fields.items():
        if name not in state_values:
            continue
        value_schema = _json_object_field(field, "valueSchema")
        if value_schema is None:
            value_schema = {}
            field["valueSchema"] = value_schema
        value_schema["default"] = state_values[name]


def _locked_identity(base_spec_json: JsonObject, out_spec_json: JsonObject) -> None:
    out_spec_json["specKind"] = base_spec_json.get("specKind")
    out_spec_json["paletteCategory"] = base_spec_json.get("paletteCategory")
    out_spec_json["serviceClass"] = base_spec_json.get("serviceClass")
    out_spec_json["schemaVersion"] = base_spec_json.get("schemaVersion")
    if base_spec_json.get("specKind") == "operator":
        out_spec_json["operatorClass"] = base_spec_json.get("operatorClass")


def _spec_json_payload(spec_obj: F8OperatorSpec | F8ServiceSpec) -> JsonObject:
    return json_object_from_value(dump_json(spec_obj, mode="json"))


def compose_variant_spec(
    *,
    spec_obj: F8OperatorSpec | F8ServiceSpec,
    ui_overrides: JsonObject,
    state_values: JsonObject,
    label: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> JsonObject:
    base = _spec_json_payload(spec_obj)
    out = deepcopy(base)
    _apply_state_ui_overrides(out, ui_overrides)
    _apply_data_port_ui_overrides(out, ui_overrides)
    _apply_command_ui_overrides(out, ui_overrides)
    _apply_state_defaults_from_values(out, state_values)
    if label is not None:
        out["label"] = str(label)
    if description is not None:
        out["description"] = str(description)
    if tags is not None:
        out["tags"] = _string_list(tags)
    _locked_identity(base, out)
    if isinstance(spec_obj, F8OperatorSpec):
        validated_operator_spec: F8OperatorSpec = validate_as(F8OperatorSpec, out)
        return _spec_json_payload(validated_operator_spec)
    validated_service_spec: F8ServiceSpec = validate_as(F8ServiceSpec, out)
    return _spec_json_payload(validated_service_spec)


def _model_property_names(model: _VariantNodeModel) -> tuple[set[str], set[str]]:
    return (set(model.properties.keys()), set(model.custom_properties.keys()))


def _state_fields(spec_obj: F8OperatorSpec | F8ServiceSpec) -> list[_NamedSpec]:
    if isinstance(spec_obj.stateFields, msgspec.UnsetType):
        return []
    return [cast(_NamedSpec, cast(object, field)) for field in list(spec_obj.stateFields or [])]


def build_variant_record_from_node(
    *,
    node: _VariantNode,
    name: str,
    description: str,
    tags: list[str],
    variant_id: str | None = None,
) -> F8VariantRecord:
    spec_candidate = node.spec
    if not isinstance(spec_candidate, (F8OperatorSpec, F8ServiceSpec)):
        raise TypeError("Node spec must be F8OperatorSpec or F8ServiceSpec")
    spec_obj = spec_candidate

    ui_overrides = node.ui_overrides()
    model = node.model
    property_names, custom_property_names = _model_property_names(model)
    state_values: JsonObject = {}
    for state_spec in _state_fields(spec_obj):
        field_name = str(state_spec.name or "").strip()
        if not field_name:
            continue
        if field_name not in property_names and field_name not in custom_property_names:
            continue
        try:
            state_values[field_name] = model.get_property(field_name)
        except (KeyError, RuntimeError, TypeError):
            continue

    spec_json = compose_variant_spec(
        spec_obj=spec_obj,
        ui_overrides=ui_overrides,
        state_values=state_values,
        label=name,
        description=description,
        tags=tags,
    )

    is_operator = isinstance(spec_obj, F8OperatorSpec)
    now = variant_now_iso()
    return F8VariantRecord(
        variantId=str(variant_id or uuid4().hex),
        kind=F8VariantKind.operator if is_operator else F8VariantKind.service,
        baseNodeType=str(node.type_ or ""),
        serviceClass=str(spec_obj.serviceClass),
        operatorClass=str(spec_obj.operatorClass) if is_operator else None,
        name=str(name).strip(),
        description=str(description).strip(),
        tags=_string_list(tags),
        spec=cast(dict[str, F8JsonValue], spec_json),
        createdAt=now,
        updatedAt=now,
    )
