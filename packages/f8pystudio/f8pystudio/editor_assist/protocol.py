from __future__ import annotations

from f8pysdk.codec import dump_json
import keyword
import logging
from typing import Any, Literal

import msgspec

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec

from ..ui.support.ui_control import ui_control_language
from .workspace import (
    EditorAssistContext,
    EditorAssistDataInPort,
    EditorAssistInputsBinding,
    EditorAssistDataOutPort,
    EditorAssistOutputsBinding,
    EditorAssistStateField,
    EditorAssistStatesBinding,
)

logger = logging.getLogger(__name__)

_WARNED_PROTOCOL_ERRORS: set[str] = set()
_FIELD_KINDS: tuple[str, ...] = ("state", "port", "prop")
_CONTEXT_ONLY_LANGUAGES: tuple[str, ...] = ("json", "lua")


def _spec_label(spec: F8ServiceSpec | F8OperatorSpec | None) -> str:
    if isinstance(spec, F8ServiceSpec):
        return f"service:{str(spec.serviceClass or '').strip() or 'unknown'}"
    if isinstance(spec, F8OperatorSpec):
        service_class = str(spec.serviceClass or "").strip() or "unknown"
        operator_class = str(spec.operatorClass or "").strip() or "unknown"
        return f"operator:{service_class}/{operator_class}"
    return "unknown-spec"


def _warn_protocol_once(message: str) -> None:
    key = str(message or "").strip()
    if not key or key in _WARNED_PROTOCOL_ERRORS:
        return
    _WARNED_PROTOCOL_ERRORS.add(key)
    logger.warning("%s", key)


def _error_context(
    spec: F8ServiceSpec | F8OperatorSpec | None,
    detail: str,
    *,
    language: str = "python",
) -> EditorAssistContext:
    lang = str(language or "plaintext").strip().lower() or "plaintext"
    msg = f"Editor assist disabled for {_spec_label(spec)}: {str(detail or '').strip()}"
    _warn_protocol_once(msg)
    return EditorAssistContext(language=lang, error_message=msg)


def _state_fields(spec: F8ServiceSpec | F8OperatorSpec) -> list[Any]:
    raw_fields = spec.stateFields
    if isinstance(raw_fields, list):
        return raw_fields
    return []


def _find_state_field(spec: F8ServiceSpec | F8OperatorSpec, key: str) -> Any | None:
    name = str(key or "").strip()
    if not name:
        return None
    for field in _state_fields(spec):
        field_name = str(field.name or "").strip()
        if field_name == name:
            return field
    return None


def _data_in_ports(spec: F8ServiceSpec | F8OperatorSpec) -> list[Any]:
    raw_ports = spec.dataInPorts
    if isinstance(raw_ports, list):
        return raw_ports
    return []


def _data_out_ports(spec: F8ServiceSpec | F8OperatorSpec) -> list[Any]:
    raw_ports = spec.dataOutPorts
    if isinstance(raw_ports, list):
        return raw_ports
    return []


def _field_editor_assist_payload(
    spec: F8ServiceSpec | F8OperatorSpec,
    *,
    field_kind: str,
    field_key: str,
    language: str,
) -> tuple[dict[str, Any] | None, str | None]:
    key = str(field_key or "").strip()
    if not key:
        return None, "field key must be non-empty"

    lang = str(language or "").strip().lower()

    if field_kind == "state":
        field = _find_state_field(spec, key)
        if field is None:
            return None, f"state field not found: {key}"

        ui_language = ui_control_language(str(field.uiControl or ""))
        if ui_language and ui_language != lang:
            return (
                None,
                f"state:{key} uiControl language={ui_language!r} does not match requested language={lang!r}",
            )

        payload_obj = field.editorAssist
        if payload_obj is None or isinstance(payload_obj, msgspec.UnsetType):
            if lang == "json":
                return {}, None
            return None, f"field-level editorAssist missing for state:{key}"

        if isinstance(payload_obj, dict):
            return dict(payload_obj), None
        try:
            dumped = dump_json(payload_obj, mode="json")
        except (AttributeError, TypeError, ValueError):
            return None, f"state:{key} editorAssist must be an object"
        if not isinstance(dumped, dict):
            return None, f"state:{key} editorAssist must be an object"
        return dumped, None

    if field_kind == "port":
        return None, "field_kind 'port' is not supported by editorAssist"

    if field_kind == "prop":
        return None, "field_kind 'prop' is not supported by field-level editorAssist yet"

    return None, f"unsupported field_kind={field_kind!r}"


def _is_valid_type_name(name: str) -> bool:
    txt = str(name or "").strip()
    return bool(txt and txt.isidentifier() and not keyword.iskeyword(txt))


def _is_valid_module_name(name: str) -> bool:
    txt = str(name or "").strip()
    if not txt:
        return False
    for part in txt.split("."):
        if not part or not part.isidentifier() or keyword.iskeyword(part):
            return False
    return True


def _spec_data_in_ports(spec: F8ServiceSpec | F8OperatorSpec) -> tuple[EditorAssistDataInPort, ...]:
    out: list[EditorAssistDataInPort] = []
    for port in _data_in_ports(spec):
        name = str(port.name or "").strip()
        if not name:
            continue
        required_raw = port.required
        required = True if isinstance(required_raw, msgspec.UnsetType) else bool(required_raw)
        description_raw = port.description
        description = "" if isinstance(description_raw, msgspec.UnsetType) else str(description_raw or "").strip()
        value_schema: dict[str, Any] | None = None
        schema_obj = port.valueSchema
        if schema_obj is not None:
            try:
                dumped = dump_json(schema_obj, mode="json")
                if isinstance(dumped, dict):
                    value_schema = dumped
            except (AttributeError, TypeError, ValueError):
                value_schema = None
        out.append(
            EditorAssistDataInPort(
                name=name,
                required=required,
                value_schema=value_schema,
                description=description,
            )
        )
    return tuple(out)


def _spec_data_out_ports(spec: F8ServiceSpec | F8OperatorSpec) -> tuple[EditorAssistDataOutPort, ...]:
    out: list[EditorAssistDataOutPort] = []
    for port in _data_out_ports(spec):
        name = str(port.name or "").strip()
        if not name:
            continue
        required_raw = port.required
        required = True if isinstance(required_raw, msgspec.UnsetType) else bool(required_raw)
        description_raw = port.description
        description = "" if isinstance(description_raw, msgspec.UnsetType) else str(description_raw or "").strip()
        value_schema: dict[str, Any] | None = None
        schema_obj = port.valueSchema
        if schema_obj is not None:
            try:
                dumped = dump_json(schema_obj, mode="json")
                if isinstance(dumped, dict):
                    value_schema = dumped
            except (AttributeError, TypeError, ValueError):
                value_schema = None
        out.append(
            EditorAssistDataOutPort(
                name=name,
                required=required,
                value_schema=value_schema,
                description=description,
            )
        )
    return tuple(out)


def _spec_readable_state_fields(spec: F8ServiceSpec | F8OperatorSpec) -> tuple[EditorAssistStateField, ...]:
    out: list[EditorAssistStateField] = []
    for field in _state_fields(spec):
        name = str(field.name or "").strip()
        if not name:
            continue
        access = str(field.access.value or "").strip().lower()
        if access not in ("rw", "ro", "wo"):
            continue
        required_raw = field.required
        required = False if isinstance(required_raw, msgspec.UnsetType) else bool(required_raw)
        description_raw = field.description
        description = "" if isinstance(description_raw, msgspec.UnsetType) else str(description_raw or "").strip()
        value_schema: dict[str, Any] | None = None
        schema_obj = field.valueSchema
        if schema_obj is not None:
            try:
                dumped = dump_json(schema_obj, mode="json")
                if isinstance(dumped, dict):
                    value_schema = dumped
            except (AttributeError, TypeError, ValueError):
                value_schema = None
        out.append(
            EditorAssistStateField(
                name=name,
                required=required,
                value_schema=value_schema,
                access=access,
                description=description,
            )
        )
    return tuple(out)


def _field_target_kwargs(
    spec: F8ServiceSpec | F8OperatorSpec,
    *,
    field_kind: str,
    field_key: str,
    language: str,
) -> dict[str, Any]:
    key = str(field_key or "").strip()
    lang = str(language or "").strip().lower()
    if field_kind != "state":
        return {}
    field = _find_state_field(spec, key)
    if field is None:
        return {}

    label_raw = field.label
    description_raw = field.description
    value_schema: dict[str, Any] | None = None
    schema_obj = field.valueSchema
    if schema_obj is not None:
        try:
            dumped = dump_json(schema_obj, mode="json")
            if isinstance(dumped, dict):
                value_schema = dumped
        except (AttributeError, TypeError, ValueError):
            value_schema = None

    return {
        "target_field_kind": "state",
        "target_field_name": key,
        "target_field_label": (
            ""
            if isinstance(label_raw, msgspec.UnsetType)
            else str(label_raw or "").strip()
        ),
        "target_field_description": (
            ""
            if isinstance(description_raw, msgspec.UnsetType)
            else str(description_raw or "").strip()
        ),
        "target_ui_language": ui_control_language(str(field.uiControl or "")) or lang,
        "target_value_schema": value_schema,
    }


def _node_instance_purpose(node: Any | None) -> str:
    if node is None:
        return ""
    try:
        return str(node.nodePurpose or "").strip()
    except Exception:
        return ""


def editor_assist_context_for_field(
    spec: F8ServiceSpec | F8OperatorSpec | None,
    *,
    field_kind: Literal["state", "port", "prop"],
    field_key: str,
    language: str,
    node: Any | None = None,
) -> EditorAssistContext | None:
    lang = str(language or "").strip().lower()
    key = str(field_key or "").strip()
    kind = str(field_kind or "").strip().lower()
    if lang not in ("python", *_CONTEXT_ONLY_LANGUAGES) or not key:
        return None
    if kind not in _FIELD_KINDS:
        return _error_context(spec, f"unsupported field_kind={kind!r}", language=lang)
    if spec is None:
        return _error_context(spec, "missing spec object", language=lang)
    if not isinstance(spec, (F8ServiceSpec, F8OperatorSpec)):
        return _error_context(spec, "spec is not service/operator", language=lang)

    payload, payload_error = _field_editor_assist_payload(
        spec, field_kind=kind, field_key=key, language=lang
    )
    if payload_error is not None:
        return _error_context(spec, payload_error, language=lang)
    if not isinstance(payload, dict):
        return _error_context(spec, f"field-level editorAssist invalid for {kind}:{key}", language=lang)

    target_kwargs = _field_target_kwargs(spec, field_kind=kind, field_key=key, language=lang)

    if not payload and lang == "json":
        # No payload but JSON language: provide standard node context.
        node_kind = "service" if isinstance(spec, F8ServiceSpec) else "operator"
        return EditorAssistContext(
            language="json",
            node_kind=node_kind,
            service_class=str(spec.serviceClass or "").strip(),
            operator_class=(
                str(spec.operatorClass or "").strip() if isinstance(spec, F8OperatorSpec) else ""
            ),
            node_description=(
                ""
                if isinstance(spec.description, msgspec.UnsetType)
                else str(spec.description or "").strip()
            ),
            node_instance_purpose=_node_instance_purpose(node),
            **target_kwargs,
            data_in_ports=_spec_data_in_ports(spec),
            data_out_ports=_spec_data_out_ports(spec),
            state_fields=_spec_readable_state_fields(spec),
        )

    version = payload.get("version")
    if not isinstance(version, int) or version != 1:
        return _error_context(spec, f"editorAssist.version must be 1, got {version!r}", language=lang)

    payload_language = str(payload.get("language") or "").strip().lower()
    if not payload_language:
        return _error_context(spec, "editorAssist.language must be a non-empty string", language=lang)
    if payload_language != lang:
        return _error_context(
            spec,
            f"editorAssist.language={payload_language!r} does not match requested language={lang!r}",
            language=lang,
        )

    if lang in _CONTEXT_ONLY_LANGUAGES:
        # Future: handle language-specific payload features. Currently just provide context.
        node_kind = "service" if isinstance(spec, F8ServiceSpec) else "operator"
        return EditorAssistContext(
            language=lang,
            node_kind=node_kind,
            service_class=str(spec.serviceClass or "").strip(),
            operator_class=(
                str(spec.operatorClass or "").strip() if isinstance(spec, F8OperatorSpec) else ""
            ),
            node_description=(
                ""
                if isinstance(spec.description, msgspec.UnsetType)
                else str(spec.description or "").strip()
            ),
            node_instance_purpose=_node_instance_purpose(node),
            **target_kwargs,
            data_in_ports=_spec_data_in_ports(spec),
            data_out_ports=_spec_data_out_ports(spec),
            state_fields=_spec_readable_state_fields(spec),
        )

    if lang != "python":
        return _error_context(spec, f"unsupported editorAssist.language={payload_language!r}", language=lang)

    python_payload = payload.get("python")
    if not isinstance(python_payload, dict):
        return _error_context(spec, "missing editorAssist.python block", language=lang)

    raw_support_files = python_payload.get("support_files")
    if not isinstance(raw_support_files, dict):
        return _error_context(spec, "editorAssist.python.support_files must be a dict[str, str]", language=lang)
    support_files: list[tuple[str, str]] = []
    for raw_name, raw_text in raw_support_files.items():
        name = str(raw_name or "").strip()
        if not name:
            return _error_context(spec, "support_files contains empty file name", language=lang)
        if not isinstance(raw_text, str):
            return _error_context(spec, f"support_files[{name!r}] must be str", language=lang)
        support_files.append((name, raw_text))
    if not support_files:
        return _error_context(spec, "support_files must not be empty", language=lang)

    overlay_prefix = python_payload.get("overlay_prefix")
    if not isinstance(overlay_prefix, str):
        return _error_context(spec, "editorAssist.python.overlay_prefix must be str", language=lang)

    dynamic_inputs_binding: EditorAssistInputsBinding | None = None
    dynamic_outputs_binding: EditorAssistOutputsBinding | None = None
    dynamic_states_binding: EditorAssistStatesBinding | None = None
    dynamic_bindings = python_payload.get("dynamic_bindings")
    if dynamic_bindings is not None:
        if not isinstance(dynamic_bindings, dict):
            return _error_context(spec, "editorAssist.python.dynamic_bindings must be an object", language=lang)
        inputs_binding = dynamic_bindings.get("inputs")
        if inputs_binding is not None:
            if not isinstance(inputs_binding, dict):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs must be an object",
                    language=lang,
                )
            enabled = inputs_binding.get("enabled")
            if not isinstance(enabled, bool):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.enabled must be bool",
                    language=lang,
                )
            source = str(inputs_binding.get("source") or "data_in_ports").strip()
            if source != "data_in_ports":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.source must be 'data_in_ports'",
                    language=lang,
                )
            type_name = str(inputs_binding.get("type_name") or "F8Inputs").strip()
            module_name = str(inputs_binding.get("module_name") or "f8_dynamic_inputs").strip()
            schema_mode = str(inputs_binding.get("schema_mode") or "basic_recursive").strip()
            access_mode = str(inputs_binding.get("access_mode") or "object_and_mapping").strip()
            if not _is_valid_type_name(type_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.type_name must be a valid identifier",
                    language=lang,
                )
            if not _is_valid_module_name(module_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.module_name must be a valid module path",
                    language=lang,
                )
            if schema_mode != "basic_recursive":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.schema_mode must be 'basic_recursive'",
                    language=lang,
                )
            if access_mode != "object_and_mapping":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.inputs.access_mode must be 'object_and_mapping'",
                    language=lang,
                )
            if enabled:
                dynamic_inputs_binding = EditorAssistInputsBinding(
                    source=source,
                    type_name=type_name,
                    module_name=module_name,
                    schema_mode=schema_mode,
                    access_mode=access_mode,
                )
        states_binding = dynamic_bindings.get("states")
        if states_binding is not None:
            if not isinstance(states_binding, dict):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states must be an object",
                    language=lang,
                )
            enabled = states_binding.get("enabled")
            if not isinstance(enabled, bool):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.enabled must be bool",
                    language=lang,
                )
            source = str(states_binding.get("source") or "state_fields").strip()
            if source != "state_fields":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.source must be 'state_fields'",
                    language=lang,
                )
            type_name = str(states_binding.get("type_name") or "F8States").strip()
            module_name = str(states_binding.get("module_name") or "f8_dynamic_states").strip()
            schema_mode = str(states_binding.get("schema_mode") or "basic_recursive").strip()
            access_mode = str(states_binding.get("access_mode") or "object_and_mapping").strip()
            if not _is_valid_type_name(type_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.type_name must be a valid identifier",
                    language=lang,
                )
            if not _is_valid_module_name(module_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.module_name must be a valid module path",
                    language=lang,
                )
            if schema_mode != "basic_recursive":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.schema_mode must be 'basic_recursive'",
                    language=lang,
                )
            if access_mode != "object_and_mapping":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.states.access_mode must be 'object_and_mapping'",
                    language=lang,
                )
            if enabled:
                dynamic_states_binding = EditorAssistStatesBinding(
                    source=source,
                    type_name=type_name,
                    module_name=module_name,
                    schema_mode=schema_mode,
                    access_mode=access_mode,
                )
        outputs_binding = dynamic_bindings.get("outputs")
        if outputs_binding is not None:
            if not isinstance(outputs_binding, dict):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs must be an object",
                    language=lang,
                )
            enabled = outputs_binding.get("enabled")
            if not isinstance(enabled, bool):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.enabled must be bool",
                    language=lang,
                )
            source = str(outputs_binding.get("source") or "data_out_ports").strip()
            if source != "data_out_ports":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.source must be 'data_out_ports'",
                    language=lang,
                )
            type_name = str(outputs_binding.get("type_name") or "F8Outputs").strip()
            module_name = str(outputs_binding.get("module_name") or "f8_dynamic_outputs").strip()
            schema_mode = str(outputs_binding.get("schema_mode") or "basic_recursive").strip()
            access_mode = str(outputs_binding.get("access_mode") or "object_and_mapping").strip()
            if not _is_valid_type_name(type_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.type_name must be a valid identifier",
                    language=lang,
                )
            if not _is_valid_module_name(module_name):
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.module_name must be a valid module path",
                    language=lang,
                )
            if schema_mode != "basic_recursive":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.schema_mode must be 'basic_recursive'",
                    language=lang,
                )
            if access_mode != "object_and_mapping":
                return _error_context(
                    spec,
                    "editorAssist.python.dynamic_bindings.outputs.access_mode must be 'object_and_mapping'",
                    language=lang,
                )
            if enabled:
                dynamic_outputs_binding = EditorAssistOutputsBinding(
                    source=source,
                    type_name=type_name,
                    module_name=module_name,
                    schema_mode=schema_mode,
                    access_mode=access_mode,
                )

    sorted_files = tuple(sorted(support_files, key=lambda item: item[0]))
    node_kind = "service" if isinstance(spec, F8ServiceSpec) else "operator"
    return EditorAssistContext(
        language="python",
        node_kind=node_kind,
        service_class=str(spec.serviceClass or "").strip(),
        operator_class=str(spec.operatorClass or "").strip() if isinstance(spec, F8OperatorSpec) else "",
        node_description="" if isinstance(spec.description, msgspec.UnsetType) else str(spec.description or "").strip(),
        node_instance_purpose=_node_instance_purpose(node),
        **target_kwargs,
        support_files=sorted_files,
        overlay_prefix=overlay_prefix,
        dynamic_inputs_binding=dynamic_inputs_binding,
        data_in_ports=_spec_data_in_ports(spec),
        dynamic_outputs_binding=dynamic_outputs_binding,
        data_out_ports=_spec_data_out_ports(spec),
        dynamic_states_binding=dynamic_states_binding,
        state_fields=_spec_readable_state_fields(spec),
    )
