from __future__ import annotations

from f8pysdk.codec import validate_as
from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec

from f8pystudio.editor_assist.protocol import editor_assist_context_for_field


def _operator_spec_with_field_editor_assist(
    editor_assist: dict | None,
    *,
    state_key: str = "code",
    port_key: str = "x",
    with_top_level: bool = False,
    attach_to_state: bool = True,
    extra_state_fields: list[dict] | None = None,
) -> F8OperatorSpec:
    state_fields = [
        {
            "name": state_key,
            "description": "Primary code body.",
            "valueSchema": {"type": "string"},
            "access": "rw",
        }
    ]
    if extra_state_fields:
        state_fields.extend(extra_state_fields)
    data_in_ports = [
        {"name": port_key, "description": "Main numeric input.", "required": True, "valueSchema": {"type": "number"}},
        {"name": "y", "description": "Optional samples.", "required": False, "valueSchema": {"type": "array", "items": {"type": "integer"}}},
        {
            "name": "z",
            "description": "Structured payload.",
            "required": True,
            "valueSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    ]
    data_out_ports = [
        {"name": "result", "description": "Script output payload.", "required": False, "valueSchema": {"type": "string"}},
    ]
    if editor_assist is not None and attach_to_state:
        state_fields[0]["editorAssist"] = editor_assist

    base = {
        "schemaVersion": F8OperatorSchemaVersion.f8operator_1,
        "serviceClass": "f8.pyengine",
        "operatorClass": "f8.python_script",
        "label": "Python Script",
        "description": "Execute custom python code for the current node.",
        "stateFields": state_fields,
        "dataInPorts": data_in_ports,
        "dataOutPorts": data_out_ports,
    }
    if with_top_level and editor_assist is not None:
        base["editorAssist"] = editor_assist
    return validate_as(F8OperatorSpec, base)


class _NodeWithPurpose:
    def __init__(self, purpose: str) -> None:
        self.nodePurpose = purpose


def test_editor_assist_context_for_field_accepts_valid_python_payload() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
            },
        },
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.error_message == ""
    assert context.language == "python"
    assert context.node_kind == "operator"
    assert context.service_class == "f8.pyengine"
    assert context.operator_class == "f8.python_script"
    assert context.node_description == "Execute custom python code for the current node."
    assert context.overlay_prefix == "from f8_script_api import *\n"
    assert context.support_files == (("f8_script_api.pyi", "class F8PyEngineContext:\n    ...\n"),)
    assert context.dynamic_inputs_binding is None
    assert tuple(port.name for port in context.data_in_ports) == ("x", "y", "z")
    assert tuple(port.name for port in context.data_out_ports) == ("result",)
    assert tuple(field.name for field in context.state_fields) == ("code",)
    assert context.node_instance_purpose == ""


def test_editor_assist_context_for_field_includes_instance_purpose_when_node_is_provided() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
            },
        },
    )

    context = editor_assist_context_for_field(
        spec,
        field_kind="state",
        field_key="code",
        language="python",
        node=_NodeWithPurpose("Extract the final bone map for the avatar rig."),
    )

    assert context is not None
    assert context.node_instance_purpose == "Extract the final bone map for the avatar rig."


def test_editor_assist_context_for_field_returns_error_when_protocol_missing() -> None:
    spec = _operator_spec_with_field_editor_assist(None)
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.support_files == ()
    assert "field-level editorAssist missing for state:code" in context.error_message


def test_editor_assist_context_for_field_returns_error_when_field_missing() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "",
            },
        },
        state_key="other",
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert "state field not found: code" in context.error_message


def test_editor_assist_context_for_field_supports_json_language() -> None:
    spec = _operator_spec_with_field_editor_assist(None, state_key="clsWeights")
    spec.stateFields[0].uiControl = "code[json]"
    spec.stateFields[0].label = "Class Weights"
    spec.stateFields[0].description = "JSON map of cls -> weight."
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="clsWeights", language="json")
    assert context is not None
    assert context.language == "json"
    assert context.target_field_kind == "state"
    assert context.target_field_name == "clsWeights"
    assert context.target_field_label == "Class Weights"
    assert context.target_field_description == "JSON map of cls -> weight."
    assert context.target_ui_language == "json"
    assert context.node_kind == "operator"


def test_editor_assist_context_for_field_accepts_dynamic_inputs_binding() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
                "dynamic_bindings": {
                    "inputs": {
                        "enabled": True,
                        "source": "data_in_ports",
                        "type_name": "F8Inputs",
                        "module_name": "f8_dynamic_inputs",
                        "schema_mode": "basic_recursive",
                        "access_mode": "object_and_mapping",
                    }
                },
            },
        }
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.dynamic_inputs_binding is not None
    assert context.dynamic_inputs_binding.type_name == "F8Inputs"
    assert context.dynamic_inputs_binding.module_name == "f8_dynamic_inputs"
    assert tuple(port.name for port in context.data_in_ports) == ("x", "y", "z")
    assert context.data_in_ports[0].description == "Main numeric input."


def test_editor_assist_context_for_field_accepts_dynamic_outputs_binding() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
                "dynamic_bindings": {
                    "outputs": {
                        "enabled": True,
                        "source": "data_out_ports",
                        "type_name": "F8Outputs",
                        "module_name": "f8_dynamic_outputs",
                        "schema_mode": "basic_recursive",
                        "access_mode": "object_and_mapping",
                    }
                },
            },
        }
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.dynamic_outputs_binding is not None
    assert context.dynamic_outputs_binding.type_name == "F8Outputs"
    assert context.dynamic_outputs_binding.module_name == "f8_dynamic_outputs"
    assert tuple(port.name for port in context.data_out_ports) == ("result",)
    assert context.data_out_ports[0].description == "Script output payload."


def test_editor_assist_context_exposes_outputs_without_dynamic_outputs_binding() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
                "dynamic_bindings": {
                    "inputs": {
                        "enabled": True,
                        "source": "data_in_ports",
                    },
                    "states": {
                        "enabled": True,
                        "source": "state_fields",
                    },
                },
            },
        }
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.dynamic_outputs_binding is None
    assert tuple(port.name for port in context.data_out_ports) == ("result",)
    assert context.data_out_ports[0].description == "Script output payload."


def test_editor_assist_context_for_field_accepts_dynamic_states_binding() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
                "dynamic_bindings": {
                    "states": {
                        "enabled": True,
                        "source": "state_fields",
                        "type_name": "F8States",
                        "module_name": "f8_dynamic_states",
                        "schema_mode": "basic_recursive",
                        "access_mode": "object_and_mapping",
                    }
                },
            },
        },
        extra_state_fields=[
            {"name": "visible_rw", "valueSchema": {"type": "number"}, "access": "rw"},
            {"name": "visible_ro", "valueSchema": {"type": "string"}, "access": "ro"},
            {"name": "hidden_wo", "valueSchema": {"type": "string"}, "access": "wo"},
        ],
    )
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.dynamic_states_binding is not None
    assert context.dynamic_states_binding.type_name == "F8States"
    assert context.dynamic_states_binding.module_name == "f8_dynamic_states"
    state_names = tuple(field.name for field in context.state_fields)
    assert "code" in state_names
    assert "visible_rw" in state_names
    assert "visible_ro" in state_names
    assert "hidden_wo" in state_names
    assert context.state_fields[0].description == "Primary code body."


def test_invalid_dynamic_inputs_source_is_rejected_by_schema() -> None:
    with_top_level = {
        "version": 1,
        "language": "python",
        "python": {
            "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
            "overlay_prefix": "",
            "dynamic_bindings": {"inputs": {"enabled": True, "source": "state_fields"}},
        },
    }
    try:
        _ = _operator_spec_with_field_editor_assist(with_top_level)
    except Exception as exc:
        message = str(exc)
        assert "state_fields" in message
        assert "dynamic_bindings.inputs.source" in message
    else:
        raise AssertionError("invalid dynamic input source must fail schema validation")


def test_invalid_dynamic_states_source_is_rejected_by_schema() -> None:
    payload = {
        "version": 1,
        "language": "python",
        "python": {
            "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
            "overlay_prefix": "",
            "dynamic_bindings": {"states": {"enabled": True, "source": "data_in_ports"}},
        },
    }
    try:
        _ = _operator_spec_with_field_editor_assist(payload)
    except Exception as exc:
        message = str(exc)
        assert "data_in_ports" in message
        assert "dynamic_bindings.states.source" in message
    else:
        raise AssertionError("invalid dynamic states source must fail schema validation")


def test_invalid_dynamic_outputs_source_is_rejected_by_schema() -> None:
    payload = {
        "version": 1,
        "language": "python",
        "python": {
            "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
            "overlay_prefix": "",
            "dynamic_bindings": {"outputs": {"enabled": True, "source": "state_fields"}},
        },
    }
    try:
        _ = _operator_spec_with_field_editor_assist(payload)
    except Exception as exc:
        message = str(exc)
        assert "state_fields" in message
        assert "dynamic_bindings.outputs.source" in message
    else:
        raise AssertionError("invalid dynamic outputs source must fail schema validation")


def test_editor_assist_context_for_port_field_is_not_supported() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
            },
        }
    )
    context = editor_assist_context_for_field(spec, field_kind="port", field_key="x", language="python")
    assert context is not None
    assert "field_kind 'port' is not supported by editorAssist" in context.error_message


def test_editor_assist_context_for_field_ignores_top_level_payload() -> None:
    payload = {
        "version": 1,
        "language": "python",
        "python": {
            "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
            "overlay_prefix": "from f8_script_api import *\n",
        },
    }
    spec = _operator_spec_with_field_editor_assist(payload, with_top_level=True, attach_to_state=False)
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert "field-level editorAssist missing for state:code" in context.error_message


def test_editor_assist_context_rejects_when_state_ui_language_mismatches() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "from f8_script_api import *\n",
            },
        }
    )
    spec.stateFields[0].uiControl = "code[lua]"
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert "uiControl language='lua' does not match requested language='python'" in context.error_message


def test_editor_assist_context_for_field_exposes_target_metadata_for_python() -> None:
    spec = _operator_spec_with_field_editor_assist(
        {
            "version": 1,
            "language": "python",
            "python": {
                "support_files": {"f8_script_api.pyi": "class F8PyEngineContext:\n    ...\n"},
                "overlay_prefix": "",
            },
        }
    )
    spec.stateFields[0].label = "Script Body"
    spec.stateFields[0].uiControl = "code[python]"
    context = editor_assist_context_for_field(spec, field_kind="state", field_key="code", language="python")
    assert context is not None
    assert context.target_field_kind == "state"
    assert context.target_field_name == "code"
    assert context.target_field_label == "Script Body"
    assert context.target_ui_language == "python"
    assert context.target_field_description == "Primary code body."
